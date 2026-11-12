"""Tests for the M1 audit fixes (AUDIT-M1.md, R1-R7 and Q4).

Written test-first: each test here failed against the M1 code and
passes after the fixes. Self-contained helpers (no import from
test_acceptance) so this file can run on its own.
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

from roundtable.config import load_agents_config

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
    tmp = tmp_path_factory.mktemp("rt-audit")
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


def make_task(url, title="Audit fix task", **kwargs):
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def db_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# -- R1: duplicate tokens and short tokens rejected at startup -----------

def test_r1_duplicate_tokens_rejected(tmp_path):
    config = tmp_path / "agents.json"
    dup = "duplicate-test-token-" + "7" * 32
    config.write_text(json.dumps({"agents": [
        {"name": "a", "role": "leader", "skills": [], "token": dup},
        {"name": "b", "role": "worker", "skills": [], "token": dup},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate token"):
        load_agents_config(str(config))


def test_r1_short_tokens_rejected(tmp_path):
    for bad in ("short", "x" * 31):
        config = tmp_path / "agents.json"
        config.write_text(json.dumps({"agents": [
            {"name": "a", "role": "leader", "skills": [], "token": bad},
        ]}), encoding="utf-8")
        with pytest.raises(ValueError, match="at least 32"):
            load_agents_config(str(config))


def test_r1_32_char_token_accepted(tmp_path):
    config = tmp_path / "agents.json"
    config.write_text(json.dumps({"agents": [
        {"name": "a", "role": "leader", "skills": [], "token": "y" * 32},
    ]}), encoding="utf-8")
    agents = load_agents_config(str(config))
    assert [a.name for a in agents] == ["a"]


# -- R2: malformed / non-ASCII bearer token -> 401, never 500 -----------

def _raw_post_status(port: int, raw_auth: bytes) -> int:
    """Send a raw HTTP request (httpx refuses non-ASCII header values
    client-side, so the non-ASCII probe needs a socket)."""
    import socket as _socket

    body = b"{}"
    req = (
        b"POST /mcp HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Authorization: " + raw_auth + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    with _socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(req)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return int(data.split(b" ", 2)[1])


def test_r2_non_ascii_and_malformed_bearer_is_401(server):
    url = server["url"]
    port = int(url.rsplit(":", 1)[1].split("/")[0])
    # Non-ASCII bearer token, sent as raw bytes: must be 401, never 500.
    assert _raw_post_status(port, "Bearer éé".encode("utf-8")) == 401
    assert _raw_post_status(port, b"Bearer \xff\xfe") == 401
    assert _raw_post_status(port, b"Bearer ") == 401
    with httpx.Client(trust_env=False, timeout=5) as http:
        for header in ("Bearer", "Basic abcdef"):
            resp = http.post(url, json={}, headers={"Authorization": header})
            assert resp.status_code == 401, f"{header!r} -> {resp.status_code}"
            assert resp.status_code != 500


# -- R3: removed agents leave the board ----------------------------------

def test_r3_migrates_m1_database_without_active_column(tmp_path):
    """A database created by M1 (agents table has no `active` column)
    must still start and gain the column on first run."""
    tmp = tmp_path
    db_path = tmp / "roundtable.db"
    old_schema = """
    CREATE TABLE agents (name TEXT PRIMARY KEY, role TEXT NOT NULL,
        skills TEXT NOT NULL DEFAULT '[]', last_seen TEXT);
    CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', required_skill TEXT,
        status TEXT NOT NULL DEFAULT 'open', created_by TEXT NOT NULL,
        assignee TEXT, result TEXT, review_note TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL, author TEXT NOT NULL, kind TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT NOT NULL, agent TEXT NOT NULL, tool TEXT NOT NULL,
        task_id INTEGER, outcome TEXT NOT NULL);
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(old_schema)
        conn.execute(
            "INSERT INTO agents (name, role, skills) VALUES "
            "('planner', 'leader', '[]'), ('coder', 'worker', '[]')"
        )
        conn.execute(
            "INSERT INTO tasks (title, status, created_by, assignee, "
            "created_at, updated_at) VALUES "
            "('Old task', 'claimed', 'planner', 'coder', '2026-11-10T00:00:00Z', "
            "'2026-11-10T00:00:00Z')"
        )
        conn.commit()
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        err, data = call(url, LEADER, "list_agents", {})
        assert not err
        assert {a["name"] for a in data["agents"]} == {"planner", "coder", "reviewer"}
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert not err and data["task"]["status"] == "claimed"
    finally:
        stop_server(proc)


def test_r3_removed_agent_disappears_and_claimed_task_reopens(tmp_path):
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port, AGENTS)
    try:
        task = make_task(url, "R3 task")
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
    finally:
        stop_server(proc)

    # Restart with coder removed from the config (same DB file).
    agents_v2 = {"agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]}
    port2 = free_port()
    proc, url2, _ = start_server(tmp, port2, agents_v2)
    try:
        err, data = call(url2, LEADER, "list_agents", {})
        assert not err
        assert {a["name"] for a in data["agents"]} == {"planner", "reviewer"}
        err, data = call(url2, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        assert data["task"]["status"] == "open"
        assert data["task"]["assignee"] is None
        # The removed agent's token no longer works at all.
        with httpx.Client(trust_env=False, timeout=5) as http:
            resp = http.post(url2, json={}, headers={"Authorization": f"Bearer {CODER}"})
            assert resp.status_code == 401
    finally:
        stop_server(proc)

    # Restart again with the leader replaced: old leader must be gone too.
    agents_v3 = {"agents": [
        {"name": "boss", "role": "leader", "skills": ["planning"], "token": "boss-test-token-" + "9" * 32},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]}
    port3 = free_port()
    proc, url3, _ = start_server(tmp, port3, agents_v3)
    try:
        err, data = call(url3, "boss-test-token-" + "9" * 32, "list_agents", {})
        assert not err
        assert {a["name"] for a in data["agents"]} == {"boss", "reviewer"}
    finally:
        stop_server(proc)


# -- F1: open task pre-assigned to a removed agent must not get stuck ---

def test_f1_open_task_preassigned_to_removed_agent_gets_unassigned(tmp_path):
    tmp = tmp_path
    port = free_port()
    proc, url, _ = start_server(tmp, port, AGENTS)
    try:
        # Leader pre-assigns the task to coder; it stays open, unclaimed.
        task = make_task(url, "F1 task", assignee="coder")
        assert task["status"] == "open" and task["assignee"] == "coder"
    finally:
        stop_server(proc)

    # Restart with coder removed from the config (same DB file).
    agents_v2 = {"agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]}
    port2 = free_port()
    proc, url2, _ = start_server(tmp, port2, agents_v2)
    try:
        err, data = call(url2, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        assert data["task"]["status"] == "open"
        assert data["task"]["assignee"] is None
        # Another worker can now claim it.
        err, data = call(url2, REVIEWER, "claim_task", {"task_id": task["id"]})
        assert not err, f"claim failed: {data}"
        assert data["task"]["assignee"] == "reviewer"
    finally:
        stop_server(proc)


# -- R4: required_skill and assignee filter capped at 100 chars ----------

def test_r4_required_skill_capped_at_100(server):
    url = server["url"]
    err, _ = call(url, LEADER, "create_task", {"title": "R4", "required_skill": "x" * 101})
    assert err
    err, data = call(url, LEADER, "create_task", {"title": "R4 ok", "required_skill": "x" * 100})
    assert not err and data["task"]["required_skill"] == "x" * 100


def test_r4_assignee_filter_capped_at_100(server):
    url = server["url"]
    err, _ = call(url, LEADER, "list_tasks", {"assignee": "x" * 101})
    assert err
    err, data = call(url, LEADER, "list_tasks", {"assignee": "x" * 100})
    assert not err and data["tasks"] == []


# -- R5: list_tasks.total matches the filters ------------------------------

def test_r5_list_total_is_filtered(server):
    url = server["url"]
    done_task = make_task(url, "R5 done task")
    make_task(url, "R5 open task")
    call(url, CODER, "claim_task", {"task_id": done_task["id"]})
    call(url, CODER, "submit_result", {"task_id": done_task["id"], "result": "r"})
    call(url, LEADER, "review_task", {"task_id": done_task["id"], "approve": True})
    err, data = call(url, LEADER, "list_tasks", {"status": "done"})
    assert not err
    assert len(data["tasks"]) == len([t for t in data["tasks"] if t["status"] == "done"])
    assert data["total"] == len(data["tasks"])
    assert data["total"] >= 1


# -- R6: create_task audit row carries the new task id ---------------------

def test_r6_create_task_audit_row_has_task_id(server):
    url, db = server["url"], server["db"]
    task = make_task(url, "R6 audit task")
    rows = db_rows(
        db,
        "SELECT task_id, outcome FROM audit_log WHERE tool = 'create_task' "
        "AND outcome = 'success' ORDER BY id DESC LIMIT 1",
    )
    assert rows and rows[0]["task_id"] == task["id"]


# -- R7: README quick start -------------------------------------------------

def test_r7_readme_quick_start_is_valid():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "python3.11+" not in text
    assert "python3 -m venv .venv" in text
    assert "Python 3.11 or newer" in text


# -- Q4: no messages on done / cancelled tasks ------------------------------

def test_q4_no_messages_on_final_tasks(server):
    url = server["url"]
    task = make_task(url, "Q4 done task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "r"})
    call(url, LEADER, "review_task", {"task_id": task["id"], "approve": True})
    err, data = call(url, CODER, "post_message", {"task_id": task["id"], "body": "too late"})
    assert err and "done" in str(data).lower()

    task2 = make_task(url, "Q4 cancelled task")
    call(url, LEADER, "cancel_task", {"task_id": task2["id"]})
    err, data = call(url, CODER, "post_message", {"task_id": task2["id"], "body": "too late"})
    assert err and "cancelled" in str(data).lower()

    # Control: messages on an open task still work.
    task3 = make_task(url, "Q4 open task")
    err, data = call(url, CODER, "post_message", {"task_id": task3["id"], "body": "hi"})
    assert not err
