"""Tests for M5: permanent review history (Owner decision, 2026-11-11).

Every successful review_task also stores one message on the task
(kind "review", author the leader, decision approved/rejected), and
only the server can write that kind. Written test-first: each test
here failed against the M4 code and passes after the M5 change.
Self-contained helpers (no import from test_acceptance) so this file
can run on its own.
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

AGENTS = {
    "agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "coder", "role": "worker", "skills": ["python"], "token": CODER},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(tmp: Path, port: int, agents: dict = AGENTS, rate_limit: int = 60):
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
    proc = subprocess.Popen(
        [sys.executable, "-m", "roundtable"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
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
    tmp = tmp_path_factory.mktemp("rt-m5")
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


def make_task(url, title="M5 task", **kwargs):
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def drive_to_submitted(url, task_id, result="the answer"):
    err, data = call(url, CODER, "claim_task", {"task_id": task_id})
    assert not err, f"claim failed: {data}"
    err, data = call(url, CODER, "submit_result", {"task_id": task_id, "result": result})
    assert not err, f"submit failed: {data}"


def review_messages(url, task_id):
    err, data = call(url, LEADER, "get_task", {"task_id": task_id})
    assert not err, f"get_task failed: {data}"
    return [m for m in data["task"]["messages"] if m["kind"] == "review"]


def db_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# -- M5: only the server writes kind "review" ------------------------------

def test_m5_post_message_review_rejected_for_worker_and_leader(server):
    url = server["url"]
    task = make_task(url, "M5 review-kind task")
    for token in (CODER, LEADER):
        err, data = call(
            url, token, "post_message",
            {"task_id": task["id"], "body": "fake review", "kind": "review"},
        )
        assert err, "post_message kind=review must fail"
        assert "reserved" in json.dumps(data), data
    # Nothing was stored: no review message, no message at all.
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err and data["task"]["messages"] == []


def test_m5_oversized_review_note_rejected_and_stores_nothing(server):
    url = server["url"]
    # Exactly at the limit: accepted, and stored in full.
    task = make_task(url, "M5 oversized note")
    drive_to_submitted(url, task["id"])
    note_ok = "x" * 4000
    err, data = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": False, "review_note": note_ok},
    )
    assert not err, data
    reviews = review_messages(url, task["id"])
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "rejected"
    assert reviews[0]["body"] == note_ok
    # One over the limit: rejected, and stores nothing.
    task2 = make_task(url, "M5 oversized note 2")
    drive_to_submitted(url, task2["id"])
    err, data = call(
        url, LEADER, "review_task",
        {"task_id": task2["id"], "approve": False, "review_note": "x" * 4001},
    )
    assert err and "4000" in json.dumps(data), data
    err, data = call(url, LEADER, "get_task", {"task_id": task2["id"]})
    assert not err
    assert data["task"]["status"] == "submitted"
    assert data["task"]["review_note"] is None
    assert review_messages(url, task2["id"]) == []


def test_m5_audit_log_has_one_review_task_row_per_call(server):
    url = server["url"]
    db = server["db"]
    task = make_task(url, "M5 audit rows")
    drive_to_submitted(url, task["id"])
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": False, "review_note": "nope"},
    )
    assert not err
    drive_to_submitted(url, task["id"])
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": True, "review_note": "good"},
    )
    assert not err
    rows = db_rows(
        db,
        "SELECT tool FROM audit_log WHERE task_id = ? AND tool = 'review_task'",
        (task["id"],),
    )
    assert len(rows) == 2, "one audit row per review_task call, no more"
    assert len(review_messages(url, task["id"])) == 2


# -- M5: migration of an M4 database ---------------------------------------

M4_SCHEMA = """
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
        CHECK (kind IN ('progress', 'help_request', 'reply', 'note')),
    body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL, agent TEXT NOT NULL, tool TEXT NOT NULL,
    task_id INTEGER, outcome TEXT NOT NULL);
CREATE INDEX idx_messages_task ON messages(task_id);
"""


def build_m4_db(tmp: Path) -> Path:
    """A database as M4 created it: one submitted task, one progress
    message, and the old messages CHECK (no 'review')."""
    db_path = tmp / "roundtable.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(M4_SCHEMA)
        conn.execute(
            "INSERT INTO agents (name, role, skills) VALUES "
            "('planner', 'leader', '[]'), ('coder', 'worker', '[]')"
        )
        conn.execute(
            "INSERT INTO tasks (title, status, created_by, created_at, "
            "updated_at) VALUES ('Old task', 'submitted', 'planner', "
            "'2026-11-11T00:00:00Z', '2026-11-11T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO messages (task_id, author, kind, body, created_at) "
            "VALUES (1, 'coder', 'progress', 'Working on it', "
            "'2026-11-11T00:00:00Z')"
        )
        conn.commit()
    return db_path


def test_m5_migrates_m4_database(tmp_path):
    """A database created by M4 (messages kind CHECK without 'review',
    no decision column) must still start, keep its messages, and gain
    review history on first run. Existing tasks get no back-filled
    history."""
    tmp = tmp_path
    db_path = build_m4_db(tmp)
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        # The old message survived the migration, with no decision.
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert not err
        assert len(data["task"]["messages"]) == 1
        old = data["task"]["messages"][0]
        assert old["kind"] == "progress" and old["body"] == "Working on it"
        assert old["decision"] is None
        # A review now stores a review message on the migrated DB.
        err, data = call(
            url, LEADER, "review_task",
            {"task_id": 1, "approve": True,
             "review_note": "approved after migration"},
        )
        assert not err, data
        reviews = review_messages(url, 1)
        assert len(reviews) == 1
        assert reviews[0]["decision"] == "approved"
        assert reviews[0]["author"] == "planner"
        assert data["task"]["review_note"] == "approved after migration"
        # No back-filled history: the old task has only its one progress
        # message plus the one review just made.
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert len(data["task"]["messages"]) == 2
    finally:
        stop_server(proc)


def test_m5_leftover_messages_new_does_not_block_startup(tmp_path):
    """R1: a previous migration run may have died after creating
    messages_new. A leftover table must not block the next start."""
    tmp = tmp_path
    db_path = build_m4_db(tmp)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages_new (id INTEGER PRIMARY KEY "
            "AUTOINCREMENT, task_id INTEGER NOT NULL, author TEXT NOT "
            "NULL, kind TEXT NOT NULL, body TEXT NOT NULL, decision "
            "TEXT, created_at TEXT NOT NULL)"
        )
        conn.commit()
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        # Startup succeeded and the migration completed: a review
        # stores a review message on the migrated DB.
        err, data = call(
            url, LEADER, "review_task",
            {"task_id": 1, "approve": True, "review_note": "ok"},
        )
        assert not err, data
        assert len(review_messages(url, 1)) == 1
    finally:
        stop_server(proc)
