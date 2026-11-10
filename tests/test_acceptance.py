"""Acceptance tests for Roundtable M1 (SPEC.md section 9).

Every test talks to a real server over Streamable HTTP, as a real MCP
client would — no in-process shortcuts. Tests are plain sync functions
that drive the async client with ``asyncio.run`` so no extra pytest
plugin is needed.

Tokens/config/DB here are fake test values only, written to tmp dirs.
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
LEADER = "tok-test-leader"
CODER = "tok-test-coder"
REVIEWER = "tok-test-reviewer"

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


def start_server(tmp: Path, port: int, rate_limit: int = 60) -> subprocess.Popen:
    config = tmp / "agents.json"
    config.write_text(json.dumps(AGENTS), encoding="utf-8")
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
            if resp.status_code == 401:  # up, auth middleware answering
                return proc
        except httpx.ConnectError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start within 20s")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rt")
    port = free_port()
    proc = start_server(tmp, port)
    yield {"url": f"http://127.0.0.1:{port}/mcp", "db": tmp / "roundtable.db"}
    stop_server(proc)


def db_rows(db_path: Path, sql: str, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def db_count(db_path: Path, sql: str, params=()) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def payload(result):
    """Tool result payload: structured content, else JSON text, else text."""
    if result.structured_content is not None:
        return result.structured_content
    text = "".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"error_text": text}


async def _call(url: str, token: str | None, tool: str, args: dict):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as sess:
                await sess.initialize()
                result = await sess.call_tool(tool, args)
                return result.is_error, payload(result)


def call(url: str, token: str | None, tool: str, args: dict):
    return asyncio.run(_call(url, token, tool, args))


def make_task(url: str, title: str = "Test task", **kwargs) -> dict:
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


# -- SPEC section 9 -------------------------------------------------------

def test_01_no_token_or_wrong_token_is_401(server):
    url = server["url"]
    with httpx.Client(trust_env=False, timeout=5) as http:
        assert http.post(url, json={}).status_code == 401
        assert http.post(
            url, json={}, headers={"Authorization": "Bearer wrong-token"}
        ).status_code == 401
        assert http.post(
            url, json={}, headers={"Authorization": "Bearer " + LEADER}
        ).status_code in (200, 202, 400)  # reaches the MCP app, not the 401 gate
    # A bad-token MCP client cannot even initialise.
    with pytest.raises(Exception):
        call(url, "wrong-token", "whoami", {})


def test_02_worker_cannot_create_review_or_cancel(server):
    url, db = server["url"], server["db"]
    before = db_count(db, "SELECT COUNT(*) FROM tasks")
    err, data = call(url, CODER, "create_task", {"title": "Worker task"})
    assert err and "leader" in str(data).lower()
    assert db_count(db, "SELECT COUNT(*) FROM tasks") == before

    task = make_task(url, "Review-guard task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "r"})
    err, _ = call(url, CODER, "review_task", {"task_id": task["id"], "approve": True})
    assert err
    err, _ = call(url, CODER, "cancel_task", {"task_id": task["id"]})
    assert err
    task_now = db_rows(db, "SELECT status FROM tasks WHERE id = ?", (task["id"],))[0]
    assert task_now["status"] == "submitted"


def test_03_two_simultaneous_claims_exactly_one_succeeds(server):
    url = server["url"]
    task = make_task(url, "Race task")

    async def claim_both():
        results = await asyncio.gather(
            _call(url, CODER, "claim_task", {"task_id": task["id"]}),
            _call(url, REVIEWER, "claim_task", {"task_id": task["id"]}),
            return_exceptions=True,
        )
        return results

    results = asyncio.run(claim_both())
    successes = [r for r in results if isinstance(r, tuple) and not r[0]]
    assert len(successes) == 1, f"expected exactly one claim to succeed: {results}"
    winner = successes[0][1]["task"]["assignee"]
    assert winner in ("coder", "reviewer")


def test_04_other_agent_cannot_submit_or_release(server):
    url = server["url"]
    task = make_task(url, "Ownership task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    err, _ = call(url, REVIEWER, "submit_result", {"task_id": task["id"], "result": "x"})
    assert err
    err, _ = call(url, REVIEWER, "release_task", {"task_id": task["id"]})
    assert err
    err, data = call(url, CODER, "get_task", {"task_id": task["id"]})
    assert not err and data["task"]["status"] == "claimed"
    assert data["task"]["assignee"] == "coder"


def test_05_happy_path_create_claim_progress_submit_review_done(server):
    url = server["url"]
    task = make_task(url, "Happy task", description="do the thing")
    assert task["status"] == "open"
    err, data = call(url, CODER, "claim_task", {"task_id": task["id"]})
    assert not err and data["task"]["status"] == "claimed"
    err, data = call(
        url, CODER, "post_message",
        {"task_id": task["id"], "body": "halfway", "kind": "progress"},
    )
    assert not err and data["message"]["kind"] == "progress"
    err, data = call(url, CODER, "submit_result", {"task_id": task["id"], "result": "all done"})
    assert not err and data["task"]["status"] == "submitted"
    err, data = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": True, "review_note": "looks good"},
    )
    assert not err and data["task"]["status"] == "done"
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err
    assert data["task"]["status"] == "done"
    assert len(data["task"]["messages"]) == 1
    assert "untrusted_content_warning" in data


def test_06_reject_path_returns_task_to_open_with_note(server):
    url = server["url"]
    task = make_task(url, "Reject task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "first try"})
    err, data = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": False, "review_note": "needs more detail"},
    )
    assert not err
    assert data["task"]["status"] == "open"
    assert data["task"]["review_note"] == "needs more detail"
    assert data["task"]["assignee"] is None
    # It can be claimed again after rejection.
    err, data = call(url, REVIEWER, "claim_task", {"task_id": task["id"]})
    assert not err and data["task"]["assignee"] == "reviewer"


def test_07_oversized_input_is_rejected(server):
    url, db = server["url"], server["db"]
    err, data = call(url, LEADER, "create_task", {"title": "x" * 201})
    assert err and "200" in str(data)
    err, data = call(url, LEADER, "create_task", {"title": "ok", "description": "x" * 8001})
    assert err and "8000" in str(data)
    task = make_task(url, "Size task")
    err, data = call(url, CODER, "post_message", {"task_id": task["id"], "body": "x" * 4001})
    assert err and "4000" in str(data)
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    err, data = call(url, CODER, "submit_result", {"task_id": task["id"], "result": "x" * 20001})
    assert err and "20000" in str(data)
    assert db_count(db, "SELECT COUNT(*) FROM messages WHERE task_id = ?", (task["id"],)) == 0


def test_08_rate_limit_triggers(tmp_path):
    port = free_port()
    proc = start_server(tmp_path, port, rate_limit=3)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(3):
            err, _ = call(url, LEADER, "whoami", {})
            assert not err
        err, data = call(url, LEADER, "whoami", {})
        assert err and "rate limit" in str(data).lower()
    finally:
        stop_server(proc)


def test_09_every_tool_call_produces_an_audit_row(server):
    url, db = server["url"], server["db"]
    before = db_count(db, "SELECT COUNT(*) FROM audit_log")
    task = make_task(url, "Audit task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "create_task", {"title": "denied"})  # worker -> error
    after = db_count(db, "SELECT COUNT(*) FROM audit_log")
    assert after - before == 3  # create + claim + denied create
    rows = db_rows(
        db,
        "SELECT agent, tool, outcome FROM audit_log ORDER BY id DESC LIMIT 3",
    )
    outcomes = {(r["tool"], r["outcome"]) for r in rows}
    assert ("create_task", "success") in outcomes
    assert ("claim_task", "success") in outcomes
    assert ("create_task", "error") in outcomes


def test_10_caller_identity_cannot_be_set_by_argument(server):
    url = server["url"]
    # A worker passing a leader identity must not become the leader:
    # either the SDK rejects the unknown argument, or the role check does.
    err, _ = call(
        url, CODER, "create_task",
        {"title": "Spoof", "agent_name": "planner", "from": "planner"},
    )
    assert err
    err, data = call(url, CODER, "whoami", {})
    assert not err and data["name"] == "coder" and data["role"] == "worker"
    # And a leader passing a worker identity still acts as the leader.
    err, data = call(
        url, LEADER, "create_task",
        {"title": "Identity task", "agent_name": "coder", "from": "coder"},
    )
    if not err:
        assert data["task"]["created_by"] == "planner"


def test_11_release_and_cancel(server):
    url = server["url"]
    task = make_task(url, "Release task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    err, data = call(url, CODER, "release_task", {"task_id": task["id"]})
    assert not err and data["task"]["status"] == "open"
    err, data = call(url, LEADER, "cancel_task", {"task_id": task["id"]})
    assert not err and data["task"]["status"] == "cancelled"
    err, _ = call(url, LEADER, "cancel_task", {"task_id": task["id"]})
    assert err  # already final


def test_12_list_filters_and_pagination(server):
    url = server["url"]
    for i in range(3):
        make_task(url, f"List task {i}")
    err, data = call(url, LEADER, "list_tasks", {"status": "open", "limit": 2, "offset": 0})
    assert not err
    assert len(data["tasks"]) == 2
    assert all(t["status"] == "open" for t in data["tasks"])
    assert data["limit"] == 2 and data["total"] >= 3
