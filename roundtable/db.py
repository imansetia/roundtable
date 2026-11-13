"""SQLite storage for Roundtable (SPEC.md section 5).

Single SQLite file, no other services. All SQL uses parameters
(SPEC section 7.4) — no string-built queries anywhere in this module.
"""

from __future__ import annotations

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('leader', 'worker')),
    skills TEXT NOT NULL DEFAULT '[]',
    last_seen TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    required_skills TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'claimed', 'submitted', 'done', 'cancelled')),
    created_by TEXT NOT NULL,
    assignee TEXT,
    result TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    author TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('progress', 'help_request', 'reply', 'note', 'review')),
    body TEXT NOT NULL,
    decision TEXT CHECK (decision IN ('approved', 'rejected')),
    created_at TEXT NOT NULL
);
-- M6: every submission is kept, not only the latest (see SPEC.md
-- section 5). A submission is not a message — it carries a full
-- result (up to 20,000 chars, vs a message's 4,000) and is written
-- only by submit_result — so it lives in its own append-only table
-- instead of stretching the messages CHECK again one milestone
-- after M5. tasks.result keeps its meaning (the latest).
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    author TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    task_id INTEGER,
    outcome TEXT NOT NULL,
    arguments TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_submissions_task ON submissions(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open the SQLite file and create the schema if needed."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    # Migration for databases created by M1 (no `active` column yet).
    columns = [row[1] for row in conn.execute("PRAGMA table_info(agents)")]
    if "active" not in columns:
        conn.execute("ALTER TABLE agents ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    _migrate_messages_table(conn)
    _migrate_audit_log(conn)
    _migrate_tasks_table(conn)
    return conn


def _migrate_audit_log(conn: sqlite3.Connection) -> None:
    """M6: audit_log gains `arguments` (the call's arguments as JSON)
    and `error` (the error message, on failed calls). Each ADD COLUMN
    is a single atomic statement and runs only if the column is
    missing, so a crash between the two just leaves the second for
    the next start. The submissions table comes from SCHEMA above
    (CREATE TABLE IF NOT EXISTS). No back-fill: old rows keep
    arguments and error NULL."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)")]
    if "arguments" not in columns:
        conn.execute("ALTER TABLE audit_log ADD COLUMN arguments TEXT")
        conn.commit()
    if "error" not in columns:
        conn.execute("ALTER TABLE audit_log ADD COLUMN error TEXT")
        conn.commit()


def _migrate_messages_table(conn: sqlite3.Connection) -> None:
    """M5: the messages table gains kind 'review' and a `decision`
    column. SQLite cannot change a CHECK constraint in place, so an
    old table is rebuilt: new table, copy, drop, rename. Existing
    messages keep their ids and get decision NULL; existing tasks get
    no back-filled history. A database created by M5 already has the
    new table and skips this entirely."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
    ).fetchone()
    if row is None:
        return  # fresh database: SCHEMA above already has the new table
    sql = row[0] or ""
    columns = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    if "'review'" not in sql:
        # R1: crash-safe. A previous run may have died after creating
        # messages_new, so drop any leftover first; then run the whole
        # rebuild in one explicit transaction, so a crash in the
        # middle rolls back instead of leaving the table half-moved.
        # A failed migration raises here and blocks startup loudly —
        # the Owner restores the backup (DEPLOY.md Step 44).
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE IF EXISTS messages_new")
            conn.execute(
                "CREATE TABLE messages_new ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "task_id INTEGER NOT NULL REFERENCES tasks(id), "
                "author TEXT NOT NULL, "
                "kind TEXT NOT NULL "
                "CHECK (kind IN ('progress', 'help_request', 'reply', 'note', 'review')), "
                "body TEXT NOT NULL, "
                "decision TEXT CHECK (decision IN ('approved', 'rejected')), "
                "created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO messages_new "
                "(id, task_id, author, kind, body, created_at) "
                "SELECT id, task_id, author, kind, body, created_at FROM messages"
            )
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE messages_new RENAME TO messages")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id)"
            )
            # DROP + RENAME do not reliably carry the AUTOINCREMENT
            # sequence, so pin it to the largest id copied over.
            # (sqlite_sequence has no PRIMARY KEY, so no ON CONFLICT.)
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('messages', "
                "(SELECT COALESCE(MAX(id), 0) FROM messages))"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    elif "decision" not in columns:
        # CHECK already allows 'review' but the column is missing
        # (an intermediate state): a plain ADD COLUMN is enough.
        conn.execute(
            "ALTER TABLE messages ADD COLUMN decision TEXT "
            "CHECK (decision IN ('approved', 'rejected'))"
        )
        conn.commit()


def _migrate_tasks_table(conn: sqlite3.Connection) -> None:
    """M7: tasks gain `required_skills` (a JSON list) instead of the
    single `required_skill` (Owner decision, 2026-11-13: skills are
    enforced on the server). A task may require several skills; the
    old column held at most one. SQLite cannot change a column in
    place, so an old table is rebuilt (same crash-safe pattern as
    the M5 messages migration): DROP TABLE IF EXISTS tasks_new
    first, then the whole rebuild in one explicit transaction.
    Existing tasks keep their ids, status, assignee, result and
    review_note; a set required_skill becomes a one-item list, an
    unset one becomes []. The messages and submissions tables
    reference tasks, so foreign-key enforcement is switched off
    for the rebuild (outside the transaction — the pragma is a
    no-op inside one) and back on afterwards; the ids are copied
    unchanged, so the references stay valid. A failed migration
    raises here and blocks startup loudly — the Owner restores
    the backup (DEPLOY.md Step 44)."""
    columns = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    if "required_skill" not in columns:
        return  # fresh database, or already migrated
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS tasks_new")
        conn.execute(
            "CREATE TABLE tasks_new ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, "
            "description TEXT NOT NULL DEFAULT '', "
            "required_skills TEXT NOT NULL DEFAULT '[]', "
            "status TEXT NOT NULL DEFAULT 'open' "
            "CHECK (status IN ('open', 'claimed', 'submitted', 'done', 'cancelled')), "
            "created_by TEXT NOT NULL, "
            "assignee TEXT, "
            "result TEXT, "
            "review_note TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tasks_new "
            "(id, title, description, required_skills, status, created_by, "
            "assignee, result, review_note, created_at, updated_at) "
            "SELECT id, title, description, "
            "CASE WHEN required_skill IS NULL OR required_skill = '' "
            "THEN '[]' ELSE json_array(required_skill) END, "
            "status, created_by, assignee, result, review_note, "
            "created_at, updated_at FROM tasks"
        )
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
        # DROP TABLE also dropped the tasks indexes (SCHEMA already
        # ran, so they are not recreated after this point).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)"
        )
        # DROP + RENAME do not reliably carry the AUTOINCREMENT
        # sequence, so pin it to the largest id copied over.
        # (sqlite_sequence has no PRIMARY KEY, so no ON CONFLICT.)
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'tasks'")
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('tasks', "
            "(SELECT COALESCE(MAX(id), 0) FROM tasks))"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def skills_to_json(skills: list[str]) -> str:
    return json.dumps(list(skills))


def skills_from_json(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
