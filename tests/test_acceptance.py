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

# M7: a worker holding two skills, for the multi-skill tests.
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
    tmp: Path, port: int, rate_limit: int = 60, agents: dict = AGENTS
) -> subprocess.Popen:
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
    # M5: the approve stores a review message too, so the task now has
    # the progress message and the review message, in that order.
    assert len(data["task"]["messages"]) == 2
    assert data["task"]["messages"][0]["kind"] == "progress"
    review = data["task"]["messages"][1]
    assert review["kind"] == "review"
    assert review["decision"] == "approved"
    assert review["author"] == "planner"
    assert review["body"] == "looks good"
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


def test_13_review_history_two_reviews_in_order(server):
    # SPEC section 9, test 11 (M5): reject then approve gives two
    # review messages in order, with the right decisions and authors.
    url = server["url"]
    task = make_task(url, "Review history task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "first try"})
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": False, "review_note": "Fix the hyphens"},
    )
    assert not err
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "second try"})
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": True, "review_note": "Looks good"},
    )
    assert not err
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err
    assert data["task"]["status"] == "done"
    # tasks.review_note keeps its current meaning: the latest note.
    assert data["task"]["review_note"] == "Looks good"
    reviews = [m for m in data["task"]["messages"] if m["kind"] == "review"]
    assert [(r["decision"], r["author"], r["body"]) for r in reviews] == [
        ("rejected", "planner", "Fix the hyphens"),
        ("approved", "planner", "Looks good"),
    ]


def test_14_two_submissions_both_kept_in_order(server):
    # SPEC section 9, test 12 (M6): two submissions on one task are
    # both kept, in order, and tasks.result is the latest.
    url = server["url"]
    task = make_task(url, "Submission history task")
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "first try"})
    err, _ = call(
        url, LEADER, "review_task",
        {"task_id": task["id"], "approve": False, "review_note": "try again"},
    )
    assert not err
    call(url, CODER, "claim_task", {"task_id": task["id"]})
    call(url, CODER, "submit_result", {"task_id": task["id"], "result": "second try"})
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err
    assert [s["result"] for s in data["task"]["submissions"]] == [
        "first try", "second try",
    ]
    assert all(s["author"] == "coder" for s in data["task"]["submissions"])
    assert data["task"]["result"] == "second try"


def test_15_failed_call_leaves_audit_row_with_arguments_and_error(server):
    # SPEC section 9, test 13 (M6): a failed call leaves an audit
    # row with its arguments and the error message.
    url = server["url"]
    db = server["db"]
    task = make_task(url, "Failed call audit task")
    err, _ = call(url, CODER, "cancel_task", {"task_id": task["id"]})
    assert err
    rows = db_rows(
        db,
        "SELECT tool, outcome, arguments, error FROM audit_log "
        "WHERE task_id = ? AND tool = 'cancel_task'",
        (task["id"],),
    )
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert json.loads(rows[0]["arguments"]) == {"task_id": task["id"]}
    assert "only the leader" in rows[0]["error"]


# -- SPEC section 9, M7 ----------------------------------------------------

def test_16_worker_without_required_skill_cannot_claim(server):
    # SPEC section 9, test 16 (M7): claim_task is rejected unless
    # the worker holds every required skill.
    url = server["url"]
    task = make_task(url, "M7 no-skill task", required_skills=["rust"])
    err, data = call(url, CODER, "claim_task", {"task_id": task["id"]})
    assert err
    assert "lacks required skill" in str(data)
    assert "rust" in str(data)
    err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
    assert not err and data["task"]["status"] == "open"
    assert data["task"]["assignee"] is None


def test_17_worker_with_all_required_skills_can_claim(tmp_path):
    # SPEC section 9, test 17 (M7): a worker holding every required
    # skill claims.
    port = free_port()
    proc = start_server(tmp_path, port, agents=M7_AGENTS)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        task = make_task(url, "M7 all-skills task", required_skills=["python", "rust"])
        assert task["required_skills"] == ["python", "rust"]
        assert task["required_skill"] == "python"  # legacy view: first skill
        err, data = call(url, POLY, "claim_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["status"] == "claimed"
        assert data["task"]["assignee"] == "poly"
    finally:
        stop_server(proc)


def test_18_partial_multi_skill_match_is_rejected(tmp_path):
    # SPEC section 9, test 18 (M7): holding some of the required
    # skills is not enough.
    port = free_port()
    proc = start_server(tmp_path, port, agents=M7_AGENTS)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        task = make_task(url, "M7 partial task", required_skills=["python", "rust"])
        err, data = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert err
        assert "lacks required skill" in str(data)
        assert "rust" in str(data)
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err and data["task"]["status"] == "open"
    finally:
        stop_server(proc)


def test_19_no_matching_worker_warning(server):
    # SPEC section 9, test 19 (M7): create_task warns (not an error)
    # when no active worker has all the required skills.
    url = server["url"]
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 warn task", "required_skills": ["go"]},
    )
    assert not err, data
    assert data["task"]["status"] == "open"
    assert "warning" in data
    assert "go" in data["warning"]
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 no-warn task", "required_skills": ["python"]},
    )
    assert not err, data
    assert "warning" not in data
    # A direct assignee who lacks a skill is rejected, not warned.
    err, _ = call(
        url, LEADER, "create_task",
        {"title": "M7 bad assignee", "required_skills": ["rust"],
         "assignee": "coder"},
    )
    assert err


def test_20_old_required_skill_argument_still_works(server):
    # SPEC section 9, test 20 (M7): the old required_skill argument
    # means a one-item list, and the task output carries both forms.
    url = server["url"]
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 legacy task", "required_skill": "python"},
    )
    assert not err, data
    assert data["task"]["required_skills"] == ["python"]
    assert data["task"]["required_skill"] == "python"
    err, data = call(url, CODER, "claim_task", {"task_id": data["task"]["id"]})
    assert not err, data
    err, data = call(
        url, LEADER, "create_task",
        {"title": "M7 legacy rust", "required_skill": "rust"},
    )
    assert not err, data
    assert data["task"]["required_skills"] == ["rust"]
    err, _ = call(url, CODER, "claim_task", {"task_id": data["task"]["id"]})
    assert err


def test_21_migration_of_m6_database_keeps_all_tasks(tmp_path):
    # SPEC section 9, test 21 (M7): an M6 database (tasks with a
    # single required_skill) migrates: a set skill becomes a
    # one-item list, an unset one becomes [].
    db_path = tmp_path / "roundtable.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
            "required_skill TEXT, status TEXT NOT NULL DEFAULT 'open' "
            "CHECK (status IN ('open', 'claimed', 'submitted', 'done', "
            "'cancelled')), created_by TEXT NOT NULL, assignee TEXT, "
            "result TEXT, review_note TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tasks (title, required_skill, status, created_by, "
            "created_at, updated_at) VALUES "
            "('M6 python task', 'python', 'open', 'planner', "
            "'2026-11-12T00:00:00Z', '2026-11-12T00:00:00Z'), "
            "('M6 general task', NULL, 'open', 'planner', "
            "'2026-11-12T00:00:00Z', '2026-11-12T00:00:00Z')"
        )
        conn.commit()
    port = free_port()
    proc = start_server(tmp_path, port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        err, data = call(url, LEADER, "get_task", {"task_id": 1})
        assert not err, data
        assert data["task"]["required_skills"] == ["python"]
        assert data["task"]["required_skill"] == "python"
        err, data = call(url, LEADER, "get_task", {"task_id": 2})
        assert not err, data
        assert data["task"]["required_skills"] == []
        assert data["task"]["required_skill"] is None
        # Enforcement applies to migrated tasks.
        err, _ = call(url, REVIEWER, "claim_task", {"task_id": 1})
        assert err
        err, data = call(url, CODER, "claim_task", {"task_id": 1})
        assert not err, data
    finally:
        stop_server(proc)
