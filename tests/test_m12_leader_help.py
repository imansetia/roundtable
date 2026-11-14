"""Tests for M12: the leader may work, only when needed (Owner
decision, 2026-11-15).

The leader may claim an open, unassigned task only when the leader
holds every required skill AND at least one leader-help rule holds —
(a) a worker has posted help_request on the task, (b) no active
worker holds every required skill, (c) the task has been released
or rejected at least 2 times, counted from the audit log and the
review history. The reason is recorded as a server-written note in
the task history. When the leader submits such a task, the
four-eyes rule applies: the leader cannot review its own work — an
active peer worker holding every required skill reviews it instead;
if none is eligible the task stays submitted and says so. Workers
still never review worker-submitted tasks. Written test-first:
each test here failed before the M12 change and passes after.

Self-contained helpers (no import from other test files) so this
file can run on its own.
"""

from __future__ import annotations

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
CODER = "worker-test-token-" + "2" * 32
RUSTY = "worker-test-token-" + "3" * 32

# The leader holds python and cobol so it can step in for tasks the
# coder cannot do (no eligible worker) and for cobol emergencies.
M12_AGENTS = {
    "agents": [
        {
            "name": "planner",
            "role": "leader",
            "skills": ["planning", "python", "cobol"],
            "token": LEADER,
        },
        {"name": "coder", "role": "worker", "skills": ["python"], "token": CODER},
        {"name": "rusty", "role": "worker", "skills": ["rust"], "token": RUSTY},
    ]
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(tmp: Path, port: int, agents: dict = M12_AGENTS):
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
            "ROUNDTABLE_RATE_LIMIT_PER_MINUTE": "600",
        }
    )
    import subprocess

    log = tmp / "server.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "roundtable"],
        cwd=str(REPO),
        env=env,
        stdout=open(log, "wb"),
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{log.read_text(errors='replace')}")
        try:
            with httpx.Client(trust_env=False, timeout=2) as http:
                resp = http.post(url, json={})
            if resp.status_code == 401:
                return proc, url, db_path
        except httpx.ConnectError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start within 30s")


def stop_server(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def call(url: str, token: str, tool: str, args: dict):
    async def _run():
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, trust_env=False
        ) as http:
            async with streamable_http_client(url, http_client=http) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, args)
        if result.is_error:
            text = "".join(
                c.text for c in result.content if getattr(c, "type", None) == "text"
            )
            return True, {"error_text": text}
        if result.structured_content is not None:
            return False, result.structured_content
        text = "".join(
            c.text for c in result.content if getattr(c, "type", None) == "text"
        )
        return False, json.loads(text)

    import asyncio

    return asyncio.run(_run())


def make_task(url: str, title: str, **kwargs) -> dict:
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def db_rows(db_path: Path, sql: str, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


# -- SPEC section 9, test 26 (M12): the leader-help rules ------------------


def test_26_leader_claim_rejected_when_no_rule_holds(tmp_path):
    # A python task with an active eligible worker (coder), no
    # help_request, no releases/rejections: none of the rules (a),
    # (b), (c) holds — the leader's claim is rejected with an error
    # naming the rules, and the task stays open.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 no-reason task", required_skills=["python"])
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert err
        text = str(data)
        assert "leader" in text.lower()
        # The error names the rules the server checked.
        assert "help_request" in text
        assert "every active worker holds all required skills" in text
        assert "2 times" in text
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err and data["task"]["status"] == "open"
        assert data["task"]["assignee"] is None
        # No server note was written for a rejected claim.
        assert data["task"]["messages"] == []
    finally:
        stop_server(proc)


def test_27_leader_claim_allowed_for_help_request(tmp_path):
    # Rule (a): a worker posted help_request on the task; the leader
    # holds the skill and claims. The reason is recorded as a
    # server-written note and returned to the leader.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 help task", required_skills=["python"])
        err, data = call(
            url, CODER, "post_message",
            {"task_id": task["id"], "kind": "help_request",
             "body": "Stuck — need the leader on this one."},
        )
        assert not err, data
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["status"] == "claimed"
        assert data["task"]["assignee"] == "planner"
        assert data["task"]["leader_claim_reason"] == (
            "a worker asked for help on this task"
        )
        # The server-written note is in the task history (get_task
        # carries the messages; the claim response does not).
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        notes = [m for m in data["task"]["messages"] if m["kind"] == "note"]
        assert any("leader" in m["body"].lower() for m in notes)
        assert any(
            "a worker asked for help on this task" in m["body"] for m in notes
        )
    finally:
        stop_server(proc)


def test_28_leader_claim_allowed_when_no_worker_is_eligible(tmp_path):
    # Rule (b): no active worker holds every required skill (cobol
    # is held by nobody but the leader) — the leader may claim.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 cobol task", required_skills=["cobol"])
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["leader_claim_reason"] == (
            "no active worker holds all required skills"
        )
    finally:
        stop_server(proc)


def test_29_leader_claim_allowed_after_two_release_or_reject(tmp_path):
    # Rule (c): the task has been released by a worker and rejected
    # in review — two strike events counted from the audit log and
    # the review history, not from any field an agent can set.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 thrice task", required_skills=["python"])
        # Strike 1: the worker claims and releases.
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, CODER, "release_task", {"task_id": task["id"]})
        assert not err
        # Strike 2: the worker claims, submits, and the leader
        # rejects the review (a review message with decision
        # 'rejected' lands in the history).
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, CODER, "submit_result",
                      {"task_id": task["id"], "result": "attempt"})
        assert not err
        err, _ = call(url, LEADER, "review_task",
                      {"task_id": task["id"], "approve": False,
                       "review_note": "not good enough"})
        assert not err
        # Now the leader may claim (rule (c)).
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["leader_claim_reason"] == (
            "released or rejected at least 2 times"
        )
    finally:
        stop_server(proc)


def test_30_leader_without_required_skill_rejected(tmp_path):
    # The leader must hold every required skill, as for workers —
    # even when a worker has asked for help.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 rust task", required_skills=["rust"])
        err, _ = call(url, RUSTY, "post_message",
                      {"task_id": task["id"], "kind": "help_request",
                       "body": "Help!"})
        assert not err
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert err
        assert "lacks required skill" in str(data)
        assert "rust" in str(data)
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["status"] == "open"
    finally:
        stop_server(proc)


# -- SPEC section 9, test 31 (M12): four-eyes on leader work ---------------


def test_31_leader_cannot_review_own_submission(tmp_path):
    # The leader claims under rule (a), submits — then its own
    # review_task is rejected (four-eyes), and the task stays
    # submitted.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 four-eyes task", required_skills=["python"])
        err, _ = call(url, CODER, "post_message",
                      {"task_id": task["id"], "kind": "help_request",
                       "body": "Help!"})
        assert not err
        err, _ = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, LEADER, "submit_result",
                      {"task_id": task["id"], "result": "leader's work"})
        assert not err
        err, data = call(url, LEADER, "review_task",
                         {"task_id": task["id"], "approve": True})
        assert err
        assert "four-eyes" in str(data).lower() or "peer" in str(data).lower()
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["status"] == "submitted"
        assert data["task"]["assignee"] == "planner"
    finally:
        stop_server(proc)


def test_32_peer_worker_reviews_leader_submission(tmp_path):
    # An eligible peer worker reviews the leader's submission —
    # approve on one task, reject on another — with the same
    # review-message history as M5 (decision as a field, author is
    # the peer worker).
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task_ok = make_task(url, "M12 approve me", required_skills=["python"])
        err, _ = call(url, CODER, "post_message",
                      {"task_id": task_ok["id"], "kind": "help_request",
                       "body": "Help!"})
        assert not err
        err, _ = call(url, LEADER, "claim_task", {"task_id": task_ok["id"]})
        assert not err
        err, _ = call(url, LEADER, "submit_result",
                      {"task_id": task_ok["id"], "result": "leader's work"})
        assert not err
        err, data = call(url, CODER, "review_task",
                         {"task_id": task_ok["id"], "approve": True,
                          "review_note": "peer-approved"})
        assert not err, data
        assert data["task"]["status"] == "done"
        err, data = call(url, LEADER, "get_task", {"task_id": task_ok["id"]})
        assert not err
        reviews = [m for m in data["task"]["messages"] if m["kind"] == "review"]
        assert reviews[-1]["decision"] == "approved"
        assert reviews[-1]["author"] == "coder"

        task_re = make_task(url, "M12 reject me", required_skills=["python"])
        err, _ = call(url, CODER, "post_message",
                      {"task_id": task_re["id"], "kind": "help_request",
                       "body": "Help on this one too!"})
        assert not err
        err, _ = call(url, LEADER, "claim_task", {"task_id": task_re["id"]})
        assert not err
        err, _ = call(url, LEADER, "submit_result",
                      {"task_id": task_re["id"], "result": "weak work"})
        assert not err
        err, data = call(url, CODER, "review_task",
                         {"task_id": task_re["id"], "approve": False,
                          "review_note": "peer-rejected: fix it"})
        assert not err, data
        assert data["task"]["status"] == "open"
        assert data["task"]["review_note"] == "peer-rejected: fix it"
        err, data = call(url, LEADER, "get_task", {"task_id": task_re["id"]})
        assert not err
        reviews = [m for m in data["task"]["messages"] if m["kind"] == "review"]
        assert reviews[-1]["decision"] == "rejected"
        assert reviews[-1]["author"] == "coder"
    finally:
        stop_server(proc)


def test_33_worker_without_skills_cannot_peer_review(tmp_path):
    # A worker lacking a required skill cannot peer-review the
    # leader's submission.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 cobol review", required_skills=["cobol"])
        err, _ = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, LEADER, "submit_result",
                      {"task_id": task["id"], "result": "leader's cobol"})
        assert not err
        err, data = call(url, CODER, "review_task",
                         {"task_id": task["id"], "approve": True})
        assert err
        assert "lacks required skill" in str(data)
        assert "cobol" in str(data)
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["status"] == "submitted"
    finally:
        stop_server(proc)


def test_34_worker_never_reviews_own_submission(tmp_path):
    # Rule 3 of the brief: a worker never reviews a task it
    # submitted itself — only the leader does (unchanged for
    # worker-submitted tasks).
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 worker task", required_skills=["python"])
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, CODER, "submit_result",
                      {"task_id": task["id"], "result": "worker's work"})
        assert not err
        err, data = call(url, CODER, "review_task",
                         {"task_id": task["id"], "approve": True})
        assert err
        assert "leader" in str(data).lower()
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["status"] == "submitted"
    finally:
        stop_server(proc)


def test_35_no_reviewer_case_stays_submitted_and_says_so(tmp_path):
    # The leader submits a cobol task; no active worker holds cobol,
    # so no eligible peer reviewer exists: the task stays submitted
    # and list/get output says it waits for a peer reviewer.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 stuck task", required_skills=["cobol"])
        err, _ = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, LEADER, "submit_result",
                      {"task_id": task["id"], "result": "leader's cobol"})
        assert not err
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        assert data["task"]["status"] == "submitted"
        assert "peer" in data["task"].get("peer_review", "")
        err, data = call(url, LEADER, "list_tasks", {"status": "submitted"})
        assert not err
        stuck = [t for t in data["tasks"] if t["id"] == task["id"]]
        assert stuck and "peer" in stuck[0].get("peer_review", "")
    finally:
        stop_server(proc)


# -- SPEC section 9, test 36 (M12): the reason is in the history -----------


def test_36_reason_recorded_in_history_and_audit(tmp_path):
    # The leader-help reason is recorded as a server-written note in
    # the task history, and the claim itself is in the audit log.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 history task", required_skills=["cobol"])
        err, _ = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err
        notes = [m for m in data["task"]["messages"] if m["kind"] == "note"]
        assert any(
            "no active worker holds all required skills" in m["body"]
            for m in notes
        )
        rows = db_rows(
            db_path,
            "SELECT tool, agent FROM audit_log WHERE task_id = ?",
            (task["id"],),
        )
        claims = [r for r in rows if r["tool"] == "claim_task"]
        assert claims and claims[0]["agent"] == "planner"
    finally:
        stop_server(proc)


def test_37_failed_releases_do_not_count_as_strikes(tmp_path):
    # Claude audit C1: failed release_task calls are logged with
    # their task_id (since M6), so the leader could manufacture
    # rule-(c) strikes by calling release_task twice on an open
    # task — and so could a worker. Only SUCCESSFUL releases by
    # workers count as strikes.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M12 no fake strikes", required_skills=["python"])
        # Two FAILED release_task calls: the task is open, so both
        # are rejected and logged as errors with the task_id.
        err, _ = call(url, LEADER, "release_task", {"task_id": task["id"]})
        assert err
        err, _ = call(url, CODER, "release_task", {"task_id": task["id"]})
        assert err
        # The failed rows exist in the audit log (the bug counted
        # them): verify the precondition of the bypass.
        rows = db_rows(
            db_path,
            "SELECT tool, outcome, agent FROM audit_log "
            "WHERE tool = 'release_task' AND task_id = ?",
            (task["id"],),
        )
        assert len(rows) == 2
        assert all(r["outcome"] == "error" for r in rows)
        # The leader's claim must still be rejected.
        err, data = call(url, LEADER, "claim_task", {"task_id": task["id"]})
        assert err
        assert "leader claim rejected" in str(data)
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["status"] == "open"
        assert data["task"]["assignee"] is None
    finally:
        stop_server(proc)
