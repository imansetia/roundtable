"""Tests for M7: skills are enforced on the server (Owner decision, 2026-11-13).

Today claim_task does not check required_skill at all; only worker.py
filters voluntarily. From M7 a task may require several skills, the
server rejects a claim unless the worker holds every one, create_task
warns when no active worker can claim, list_tasks gains a
claimable_by_me filter, and worker.py uses the server rule instead of
its own filter. Written test-first: each test here failed against the
M6 code and passes after the M7 change. Self-contained helpers (no
import from test_acceptance) so this file can run on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO = Path(__file__).resolve().parent.parent
LEADER = "leader-test-token-" + "1" * 32
CODER = "coder-test-token-" + "2" * 32
REVIEWER = "reviewer-test-token-" + "3" * 32
POLY = "poly-test-token-" + "4" * 32

AGENTS = {
    "agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "coder", "role": "worker", "skills": ["python"], "token": CODER},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]
}

# A worker holding two skills, for the multi-skill tests.
M7_AGENTS = {
    "agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "coder", "role": "worker", "skills": ["python"], "token": CODER},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
        {"name": "poly", "role": "worker", "skills": ["python", "rust"], "token": POLY},
    ]
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(
    tmp: Path,
    port: int,
    agents: dict = AGENTS,
    rate_limit: int = 60,
    extra_env: dict | None = None,
):
    config = tmp / "agents.json"
    config.write_text(json.dumps(agents), encoding="utf-8")
    db_path = tmp / "roundtable.db"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO),
            "ROUNDTABLE_HOST": "127.0.0.1",
            "ROUNDTABLE_PORT": str(port),
            "ROUNDTABLE_DB_PATH": str(db_path),
            "ROUNDTABLE_AGENTS_CONFIG": str(config),
            "ROUNDTABLE_RATE_LIMIT_PER_MINUTE": str(rate_limit),
        }
    )
    if extra_env:
        env.update(extra_env)
    # The server's log goes to a file, not a PIPE: uvicorn logs
    # every request, and an unread PIPE fills up (64 KB) and
    # blocks the server, which then times out the client. The log
    # is still available when the server exits early.
    proc = subprocess.Popen(
        [sys.executable, "-m", "roundtable"],
        cwd=str(REPO),
        env=env,
        stdout=open(tmp / "server.log", "wb"),
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out = (tmp / "server.log").read_text(errors="replace")
            raise RuntimeError(f"server exited early:\n{out}")
        try:
            with httpx.Client(trust_env=False, timeout=2) as http:
                resp = http.post(url, json={})
            if resp.status_code == 401:
                return proc, url, db_path
        except httpx.ConnectError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start within 20s")


def stop_server(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rt-m7")
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    yield {"url": url, "db": db_path}
    stop_server(proc)


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    text = "".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"error_text": text}


async def _call(url, token, tool, args):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as sess:
                await sess.initialize()
                result = await sess.call_tool(tool, args)
                return result.is_error, payload(result)


def call(url, token, tool, args):
    return asyncio.run(_call(url, token, tool, args))


def make_task(url, title="M7 task", **kwargs):
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def db_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def db_count(db_path, sql, params=()) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


# -- M7: server-side skill enforcement ------------------------------------

def test_m7_worker_without_required_skill_cannot_claim(server):
    url = server["url"]
    task = make_task(url, "M7 rust task", required_skills=["rust"])
    # Neither worker holds rust: the claim is rejected with a clear
    # error naming the missing skill, and nothing changes.
    err, data = call(url, CODER, "claim_task", {"task_id": task["id"]})
    assert err
    assert "lacks required skill" in str(data)
    assert "rust" in str(data)
    err, data = call(url, REVIEWER, "claim_task", {"task_id": task["id"]})
    assert err
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err
    assert data["task"]["status"] == "open"
    assert data["task"]["assignee"] is None


def test_m7_worker_with_all_required_skills_can_claim(tmp_path):
    """A worker holding every required skill claims; the others cannot."""
    tmp = tmp_path
    port = free_port()
    proc, url, _ = start_server(tmp, port, M7_AGENTS)
    try:
        task = make_task(url, "M7 poly task", required_skills=["python", "rust"])
        assert task["required_skills"] == ["python", "rust"]
        assert task["required_skill"] == "python"  # legacy view: first skill
        err, data = call(url, POLY, "claim_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["status"] == "claimed"
        assert data["task"]["assignee"] == "poly"
        # A one-skill task is claimable by the single-skill worker too.
        single = make_task(url, "M7 python task", required_skills=["python"])
        err, data = call(url, CODER, "claim_task", {"task_id": single["id"]})
        assert not err, data
    finally:
        stop_server(proc)


def test_m7_partial_multi_skill_match_is_rejected(tmp_path):
    """Holding some of the required skills is not enough: every one
    is required, and the error names the missing ones."""
    tmp = tmp_path
    port = free_port()
    proc, url, _ = start_server(tmp, port, M7_AGENTS)
    try:
        task = make_task(url, "M7 partial task", required_skills=["python", "rust"])
        err, data = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert err
        assert "lacks required skill" in str(data)
        assert "rust" in str(data)
        assert "python" not in str(data)  # python is held, not missing
        err, data = call(url, REVIEWER, "claim_task", {"task_id": task["id"]})
        assert err
        assert "python" in str(data) and "rust" in str(data)
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err and data["task"]["status"] == "open"
    finally:
        stop_server(proc)


def test_m7_no_matching_worker_warning(server):
    url = server["url"]
    # No active worker holds "go": the task is created, but the
    # leader is warned in the result (not an error).
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 go task", "required_skills": ["go"]},
    )
    assert not err, data
    assert data["task"]["status"] == "open"
    assert "warning" in data, "expected a no-matching-worker warning"
    assert "go" in data["warning"]
    # A task a worker can claim carries no warning.
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 python task", "required_skills": ["python"]},
    )
    assert not err, data
    assert "warning" not in data
    err, data = call(url, LEADER, "create_task", {"title": "M7 general task"})
    assert not err, data
    assert "warning" not in data


def test_m7_assignee_lacking_a_skill_is_rejected(server):
    url = server["url"]
    db = server["db"]
    before = db_count(db, "SELECT COUNT(*) FROM tasks")
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 assigned rust", "required_skills": ["rust"],
         "assignee": "coder"},
    )
    assert err
    assert "lacks required skill" in str(data)
    assert "rust" in str(data)
    assert db_count(db, "SELECT COUNT(*) FROM tasks") == before
    # An assignee who holds the skill is accepted, with no warning.
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 assigned python", "required_skills": ["python"],
         "assignee": "coder"},
    )
    assert not err, data
    assert "warning" not in data
    assert data["task"]["assignee"] == "coder"


def test_m7_old_required_skill_argument_still_works(server):
    url = server["url"]
    # The old argument means a one-item list, and the task output
    # carries both forms so existing clients still work.
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 legacy python", "required_skill": "python"},
    )
    assert not err, data
    assert data["task"]["required_skills"] == ["python"]
    assert data["task"]["required_skill"] == "python"
    err, data = call(url, CODER, "claim_task", {"task_id": data["task"]["id"]})
    assert not err, data
    # The legacy form is enforced too: a rust task cannot be
    # claimed by the python worker.
    legacy_rust = make_task(url, "M7 legacy rust", required_skill="rust")
    assert legacy_rust["required_skills"] == ["rust"]
    assert legacy_rust["required_skill"] == "rust"
    err, _ = call(url, CODER, "claim_task", {"task_id": legacy_rust["id"]})
    assert err
    # No skill at all: both forms are empty.
    err, data = call(url, LEADER, "create_task", {"title": "M7 legacy none"})
    assert not err, data
    assert data["task"]["required_skills"] == []
    assert data["task"]["required_skill"] is None
    # Giving both spellings is an error, not a silent choice.
    err, _ = call(
        url, LEADER, "create_task",
        {"title": "M7 both", "required_skill": "python",
         "required_skills": ["python"]},
    )
    assert err


def test_m7_claimable_by_me_lists_only_claimable(server):
    url = server["url"]
    python_task = make_task(url, "M7 list python", required_skills=["python"])
    rust_task = make_task(url, "M7 list rust", required_skills=["rust"])
    general_task = make_task(url, "M7 list general")
    assigned = make_task(
        url, "M7 list assigned", required_skill="review", assignee="reviewer"
    )
    err, data = call(
        url, CODER, "list_tasks", {"claimable_by_me": True, "limit": 100}
    )
    assert not err, data
    ids = {t["id"] for t in data["tasks"]}
    assert python_task["id"] in ids
    assert general_task["id"] in ids
    assert rust_task["id"] not in ids
    assert assigned["id"] not in ids
    assert data["total"] == len(data["tasks"])
    # The reviewer sees the task assigned to them (and the general
    # one), but not the python or rust tasks.
    err, data = call(
        url, REVIEWER, "list_tasks", {"claimable_by_me": True, "limit": 100}
    )
    assert not err, data
    ids = {t["id"] for t in data["tasks"]}
    assert assigned["id"] in ids
    assert general_task["id"] in ids
    assert python_task["id"] not in ids
    assert rust_task["id"] not in ids
    # The filter is for workers: the leader gets a clear error.
    err, _ = call(url, LEADER, "list_tasks", {"claimable_by_me": True})
    assert err


def test_m7_audit_log_records_the_new_arguments(server):
    url = server["url"]
    db = server["db"]
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 audit task", "required_skills": ["python", "rust"]},
    )
    assert not err, data
    task_id = data["task"]["id"]
    rows = db_rows(
        db,
        "SELECT arguments FROM audit_log WHERE tool = 'create_task' "
        "AND task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    )
    assert len(rows) == 1
    stored = json.loads(rows[0]["arguments"])
    assert stored["required_skills"] == ["python", "rust"]
    assert stored["required_skill"] is None
    assert stored["title"] == "M7 audit task"
    err, _ = call(url, CODER, "list_tasks", {"claimable_by_me": True})
    assert not err
    rows = db_rows(
        db,
        "SELECT arguments FROM audit_log WHERE tool = 'list_tasks' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert json.loads(rows[0]["arguments"])["claimable_by_me"] is True


# -- M7: migration of an M6 database ---------------------------------------

M6_SCHEMA = """
CREATE TABLE agents (name TEXT PRIMARY KEY, role TEXT NOT NULL
    CHECK (role IN ('leader', 'worker')), skills TEXT NOT NULL DEFAULT '[]',
    last_seen TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', required_skill TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'claimed', 'submitted', 'done', 'cancelled')),
    created_by TEXT NOT NULL, assignee TEXT, result TEXT, review_note TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id), author TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('progress', 'help_request', 'reply', 'note', 'review')),
    body TEXT NOT NULL,
    decision TEXT CHECK (decision IN ('approved', 'rejected')),
    created_at TEXT NOT NULL);
CREATE TABLE submissions (id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id), author TEXT NOT NULL,
    result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL, agent TEXT NOT NULL, tool TEXT NOT NULL,
    task_id INTEGER, outcome TEXT NOT NULL, arguments TEXT, error TEXT);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_assignee ON tasks(assignee);
CREATE INDEX idx_messages_task ON messages(task_id);
CREATE INDEX idx_submissions_task ON submissions(task_id);
"""


def test_m7_migrates_m6_database(tmp_path):
    """A database created by M6 (tasks.required_skill, a single
    string) must still start and keep every task: a set skill
    becomes a one-item list, an unset one becomes []. The new
    skill enforcement applies to migrated tasks too. A leftover
    tasks_new table (a previous run died mid-migration) must not
    block startup."""
    tmp = tmp_path
    db_path = tmp / "roundtable.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(M6_SCHEMA)
        conn.execute(
            "INSERT INTO agents (name, role, skills) VALUES "
            "('planner', 'leader', '[]'), ('coder', 'worker', '[\"python\"]'), "
            "('reviewer', 'worker', '[\"review\"]')"
        )
        conn.execute(
            "INSERT INTO tasks (title, required_skill, status, created_by, "
            "assignee, result, review_note, created_at, updated_at) VALUES "
            "('Migrated python', 'python', 'open', 'planner', NULL, NULL, NULL, "
            "'2026-11-12T00:00:00Z', '2026-11-12T00:00:00Z'), "
            "('Migrated general', NULL, 'open', 'planner', NULL, NULL, NULL, "
            "'2026-11-12T00:00:00Z', '2026-11-12T00:00:00Z'), "
            "('Migrated done', 'review', 'done', 'planner', 'reviewer', "
            "'done result', 'ok', '2026-11-12T00:00:00Z', '2026-11-12T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO messages (task_id, author, kind, body, decision, "
            "created_at) VALUES (1, 'coder', 'progress', 'Working on it', "
            "NULL, '2026-11-12T00:00:00Z')"
        )
        # Leftover from a crashed migration run: the rebuild drops
        # it first instead of failing on it.
        conn.execute("CREATE TABLE tasks_new (id INTEGER PRIMARY KEY)")
        conn.commit()
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        # All three tasks survive, with the right skill lists.
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert not err, data
        assert data["task"]["title"] == "Migrated python"
        assert data["task"]["required_skills"] == ["python"]
        assert data["task"]["required_skill"] == "python"
        assert len(data["task"]["messages"]) == 1
        err, data = call(url, LEADER, "get_task", {"task_id": 2})
        assert not err, data
        assert data["task"]["required_skills"] == []
        assert data["task"]["required_skill"] is None
        err, data = call(url, LEADER, "get_task", {"task_id": 3})
        assert not err, data
        assert data["task"]["status"] == "done"
        assert data["task"]["result"] == "done result"
        assert data["task"]["required_skills"] == ["review"]
        # Enforcement applies to migrated tasks: coder claims the
        # python one (it holds python); the reviewer cannot claim
        # it (already claimed, and reviewer lacks python).
        err, data = call(url, CODER, "claim_task", {"task_id": 1})
        assert not err, data
        err, _ = call(url, REVIEWER, "claim_task", {"task_id": 1})
        assert err  # already claimed (and reviewer lacks python)
        # New tasks keep counting ids past the migrated ones.
        task = make_task(url, "M7 after migration")
        assert task["id"] == 4
        # The leftover table is gone and the tasks indexes are back.
        tables = db_rows(
            db_path, "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        assert "tasks_new" not in {t["name"] for t in tables}
        indexes = db_rows(
            db_path, "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
        assert {"idx_tasks_status", "idx_tasks_assignee"} <= {
            i["name"] for i in indexes
        }
    finally:
        stop_server(proc)
