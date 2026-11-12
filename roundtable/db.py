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
    required_skill TEXT,
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
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    task_id INTEGER,
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
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
    return conn


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


def skills_to_json(skills: list[str]) -> str:
    return json.dumps(list(skills))


def skills_from_json(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
