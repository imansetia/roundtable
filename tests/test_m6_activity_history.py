"""Tests for M6: complete activity history (Owner decision, 2026-11-12).

Every submission is kept (not only the latest), and every tool call
leaves an audit row with its arguments and, on error, the error
message. Written test-first: each test here failed against the M5
code and passes after the M6 change. Self-contained helpers (no
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
    tmp = tmp_path_factory.mktemp("rt-m6")
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


def make_task(url, title="M6 task", **kwargs):
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def drive_to_submitted(url, task_id, result="the answer"):
    err, data = call(url, CODER, "claim_task", {"task_id": task_id})
    assert not err, f"claim failed: {data}"
    err, data = call(url, CODER, "submit_result", {"task_id": task_id, "result": result})
    assert not err, f"submit failed: {data}"


def db_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def db_count(db_path, sql, params=()) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


# -- M6: call history -------------------------------------------------------

def test_m6_failed_call_leaves_audit_row_with_arguments_and_error(server):
    url = server["url"]
    db = server["db"]
    task = make_task(url, "M6 failed call")
    # A worker's cancel_task (role error) and a post_message with the
    # reserved kind (both agents) are failed calls, not silent ones.
    err, _ = call(url, CODER, "cancel_task", {"task_id": task["id"]})
    assert err
    err, _ = call(
        url, CODER, "post_message",
        {"task_id": task["id"], "body": "fake review", "kind": "review"},
    )
    assert err
    err, _ = call(
        url, LEADER, "post_message",
        {"task_id": task["id"], "body": "fake review", "kind": "review"},
    )
    assert err
    rows = db_rows(
        db,
        "SELECT tool, outcome, arguments, error FROM audit_log "
        "WHERE task_id = ? AND outcome = 'error' ORDER BY id",
        (task["id"],),
    )
    assert [r["tool"] for r in rows] == [
        "cancel_task", "post_message", "post_message",
    ]
    for row in rows:
        args = json.loads(row["arguments"])
        assert args["task_id"] == task["id"]
        assert row["error"], "an error row carries the error message"
    assert "only the leader" in rows[0]["error"]
    assert "reserved" in rows[1]["error"]
    assert "reserved" in rows[2]["error"]
    cancel_args = json.loads(rows[0]["arguments"])
    assert cancel_args == {"task_id": task["id"]}
    review_args = json.loads(rows[1]["arguments"])
    assert review_args["kind"] == "review"


def test_m6_no_audit_row_contains_a_token(server):
    url = server["url"]
    db = server["db"]
    # The test tokens are distinctive strings; no audit row may ever
    # hold one — arguments come from tool calls, never from the
    # Authorization header, and error messages never echo it.
    task = make_task(url, "M6 token check")
    drive_to_submitted(url, task["id"])
    err, _ = call(url, CODER, "cancel_task", {"task_id": task["id"]})
    assert err
    rows = db_rows(db, "SELECT arguments, error FROM audit_log")
    assert rows, "the calls above must have left audit rows"
    for row in rows:
        for field in ("arguments", "error"):
            value = row[field] or ""
            assert LEADER not in value, field
            assert CODER not in value, field
            assert REVIEWER not in value, field


def test_m6_large_argument_stored_cut_with_marker(server):
    url = server["url"]
    db = server["db"]
    task = make_task(url, "M6 large argument")
    # A 100 KB body is rejected (message limit 4,000) — but the call
    # still leaves an audit row. F1: a call rejected for size keeps
    # only a short prefix (2 KB) plus the marker, not 64 KB. The
    # board data itself is rejected, not truncated: nothing was
    # stored on the task.
    big = "x" * 100_000
    err, _ = call(
        url, CODER, "post_message",
        {"task_id": task["id"], "body": big, "kind": "note"},
    )
    assert err
    rows = db_rows(
        db,
        "SELECT arguments, error FROM audit_log WHERE task_id = ? "
        "AND tool = 'post_message' ORDER BY id DESC LIMIT 1",
        (task["id"],),
    )
    assert len(rows) == 1
    stored = json.loads(rows[0]["arguments"])
    # The marker: truncated, with the original size in bytes, and a
    # short prefix (F1: at most 2 KB for a size-rejected call).
    assert stored["truncated"] is True
    expected_bytes = len(
        json.dumps(
            {"task_id": task["id"], "body": big, "kind": "note"}
        ).encode("utf-8")
    )
    assert stored["original_bytes"] == expected_bytes
    assert stored["original_bytes"] > 64 * 1024
    assert len(stored["arguments"].encode("utf-8")) <= 2 * 1024
    assert "4000" in (rows[0]["error"] or "")
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err and data["task"]["messages"] == []


def test_m6_audit_log_one_row_per_call(server):
    url = server["url"]
    db = server["db"]
    before = db_count(db, "SELECT COUNT(*) FROM audit_log")
    task = make_task(url, "M6 one row per call")
    drive_to_submitted(url, task["id"])
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": True, "review_note": "good"},
    )
    assert not err
    err, _ = call(url, CODER, "cancel_task", {"task_id": task["id"]})
    assert err  # already done, and a worker cannot cancel
    after = db_count(db, "SELECT COUNT(*) FROM audit_log")
    # create + claim + submit + review + one failed cancel = 5 calls,
    # 5 rows — the submission history lives in its own table, not here.
    assert after - before == 5, f"{after - before} rows for 5 calls"
    # And the rows carry the calls' arguments: the review row records
    # the decision, the failed cancel records the error.
    rows = db_rows(
        db,
        "SELECT tool, outcome, arguments, error FROM audit_log "
        "WHERE task_id = ? AND tool IN ('review_task', 'cancel_task') "
        "ORDER BY id",
        (task["id"],),
    )
    assert len(rows) == 2
    assert json.loads(rows[0]["arguments"])["approve"] is True
    assert rows[0]["outcome"] == "success"
    assert rows[1]["outcome"] == "error"
    assert "only the leader" in rows[1]["error"]


# -- M6 audit fixes: F1 (flood) -------------------------------------------

def test_m6_f1_rate_limited_call_stores_no_arguments(tmp_path):
    """F1: a rate-limited call must not store its arguments at all —
    only a marker naming the original size. Otherwise one token can
    write ~35 MB/min of audit arguments (the audit's flood probe:
    27 rate-limited rows alone stored 1.77 MB)."""
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port, AGENTS, rate_limit=3)
    try:
        # Four create_task calls with a large (8,000-char, valid)
        # description: the first three succeed, the fourth is
        # rate-limited.
        big_description = "y" * 8_000
        for i in range(3):
            err, _ = call(
                url, LEADER, "create_task",
                {"title": f"F1 task {i}", "description": big_description},
            )
            assert not err
        err, _ = call(
            url, LEADER, "create_task",
            {"title": "F1 task 3", "description": big_description},
        )
        assert err
        rows = db_rows(
            db_path,
            "SELECT tool, outcome, arguments, error FROM audit_log "
            "WHERE tool = 'create_task' ORDER BY id",
        )
        assert len(rows) == 4
        # The three successful calls keep their full arguments.
        for row in rows[:3]:
            assert json.loads(row["arguments"])["title"].startswith("F1 task")
        # The rate-limited call stores no arguments, only the marker.
        limited = rows[3]
        assert limited["outcome"] == "error"
        assert "rate limit" in (limited["error"] or "")
        stored = json.loads(limited["arguments"])
        assert stored["omitted"] == "rate_limited"
        expected_bytes = len(
            json.dumps(
                {
                    "title": "F1 task 3",
                    "description": big_description,
                    "required_skill": None,
                    "assignee": None,
                }
            ).encode("utf-8")
        )
        assert stored["original_bytes"] == expected_bytes
        assert stored["original_bytes"] > 8_000
        assert len(limited["arguments"].encode("utf-8")) < 200
    finally:
        stop_server(proc)


def test_m6_f2_history_cap_omits_arguments_and_warns_once(tmp_path):
    """F2: above ROUNDTABLE_HISTORY_MAX_MB, audit rows keep agent,
    tool, task, outcome and error, but store a marker instead of
    arguments — and the server logs a warning once. Tools never
    fail because of the cap."""
    tmp = tmp_path
    port = free_port()
    # A ~1 KB cap: one call with a 2,000-char description crosses it.
    proc, url, db_path = start_server(
        tmp, port, AGENTS, extra_env={"ROUNDTABLE_HISTORY_MAX_MB": "0.001"}
    )
    try:
        err, data = call(
            url, LEADER, "create_task",
            {"title": "F2 task", "description": "z" * 2_000},
        )
        assert not err, data
        task_id = data["task"]["id"]
        # A second successful call, also over the cap (a small call
        # would fit under it: the first call's marker does not count
        # toward the total, so the running total is still 0).
        err, _ = call(
            url, LEADER, "create_task",
            {"title": "F2 task 2", "description": "z" * 2_000},
        )
        assert not err
        rows = db_rows(
            db_path,
            "SELECT agent, tool, task_id, outcome, arguments, error "
            "FROM audit_log ORDER BY id",
        )
        assert len(rows) == 2
        for row in rows:
            stored = json.loads(row["arguments"])
            assert stored["omitted"] == "history_cap"
            assert stored["original_bytes"] > 1_000
        assert rows[0]["agent"] == "planner"
        assert rows[0]["tool"] == "create_task"
        assert rows[0]["task_id"] == task_id
        assert rows[0]["outcome"] == "success"
        assert rows[0]["error"] is None
        # The warning is logged exactly once, even though two calls
        # crossed the cap.
        log = (tmp / "server.log").read_text(errors="replace")
        assert log.count("History cap reached") == 1
    finally:
        stop_server(proc)


# -- M6: migration of an M5 database ---------------------------------------

M5_SCHEMA = """
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
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL, agent TEXT NOT NULL, tool TEXT NOT NULL,
    task_id INTEGER, outcome TEXT NOT NULL);
CREATE INDEX idx_messages_task ON messages(task_id);
"""


def test_m6_migrates_m5_database(tmp_path):
    """A database created by M5 (audit_log without arguments/error,
    no submissions table) must still start and keep every existing
    row. No back-fill: old calls get no arguments, old tasks get no
    submissions."""
    tmp = tmp_path
    db_path = tmp / "roundtable.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(M5_SCHEMA)
        conn.execute(
            "INSERT INTO agents (name, role, skills) VALUES "
            "('planner', 'leader', '[]'), ('coder', 'worker', '[]')"
        )
        conn.execute(
            "INSERT INTO tasks (title, status, created_by, assignee, "
            "result, review_note, created_at, updated_at) VALUES "
            "('Old task', 'done', 'planner', 'coder', 'final answer', "
            "'looks good', '2026-11-11T00:00:00Z', '2026-11-11T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO messages (task_id, author, kind, body, decision, "
            "created_at) VALUES (1, 'coder', 'progress', 'Working on it', "
            "NULL, '2026-11-11T00:00:00Z'), (1, 'planner', 'review', "
            "'looks good', 'approved', '2026-11-11T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO audit_log (time, agent, tool, task_id, outcome) "
            "VALUES ('2026-11-11T00:00:00Z', 'planner', 'review_task', 1, "
            "'success')"
        )
        conn.commit()
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert not err
        assert data["task"]["status"] == "done"
        assert data["task"]["result"] == "final answer"
        assert len(data["task"]["messages"]) == 2
        assert data["task"]["submissions"] == []
        # The pre-M6 row keeps NULL arguments and error (no back-fill);
        # the get_task above added its own row, so filter by tool.
        old_rows = db_rows(
            db_path,
            "SELECT * FROM audit_log WHERE task_id = 1 AND tool = 'review_task'",
        )
        assert len(old_rows) == 1
        assert old_rows[0]["arguments"] is None
        assert old_rows[0]["error"] is None
        # New calls record arguments from now on.
        task = make_task(url, "M6 migrated task")
        drive_to_submitted(url, task["id"], result="first")
        err, _ = call(
            url, LEADER, "review_task",
            {"task_id": task["id"], "approve": False, "review_note": "again"},
        )
        assert not err
        drive_to_submitted(url, task["id"], result="second")
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        assert [s["result"] for s in data["task"]["submissions"]] == [
            "first", "second",
        ]
        assert data["task"]["result"] == "second"
    finally:
        stop_server(proc)
