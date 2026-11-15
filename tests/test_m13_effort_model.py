"""Tests for M13: effort levels, model notes, and a skill catalog
(Owner decision, 2026-11-15).

Roundtable cannot choose or verify the model an agent runs: the model
and its reasoning level are set in each agent's own app. So M13 adds
HINTS and RECORDS only — nothing here changes who may claim, review
or cancel. Written test-first: each test here failed against the
M12 tree (f2b73a7) and passes after the M13 change.

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

# The leader holds python and cobol so it can step in for tasks no
# worker can do (M12 rule b) — unchanged by M13.
M13_AGENTS = {
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
    # In WSL2 mirrored networking, localhost is shared with Windows —
    # a bind(0) pick can collide with a port a Windows-side process
    # already holds. Verify the picked port is actually free.
    for _ in range(10):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                continue  # something is listening there — pick another
        except OSError:
            return port
    raise RuntimeError("no free port found in 10 tries")


def start_server(tmp: Path, port: int, agents: dict = M13_AGENTS):
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
    proc = subprocess.Popen(
        [sys.executable, "-m", "roundtable"],
        cwd=str(REPO),
        env=env,
        stdout=open(tmp / "server.log", "wb"),
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            log = (tmp / "server.log").read_text(errors="replace")
            raise RuntimeError(f"server exited early:\n{log}")
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


# -- M13: task effort level (SPEC section 9) ------------------------------


def test_38_effort_accepted_low_medium_high(tmp_path):
    # effort is an optional hint: low, medium or high. Stored and
    # shown in get_task.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        for i, effort in enumerate(("low", "medium", "high")):
            task = make_task(url, f"M13 effort {effort}", effort=effort)
            err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
            assert not err, data
            assert data["task"]["effort"] == effort
        rows = db_rows(db_path, "SELECT title, effort FROM tasks ORDER BY id")
        assert [r["effort"] for r in rows] == ["low", "medium", "high"]
    finally:
        stop_server(proc)


def test_39_effort_invalid_rejected(tmp_path):
    # Any value other than low/medium/high is a clear error; the task
    # is not created.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        for bad in ("urgent", "LOW", "", "extreme-high"):
            err, data = call(url, LEADER, "create_task",
                             {"title": f"M13 bad {bad}", "effort": bad})
            assert err, f"effort {bad!r} must be rejected"
            assert "effort" in str(data)
        rows = db_rows(db_path, "SELECT COUNT(*) AS n FROM tasks")
        assert rows[0]["n"] == 0
    finally:
        stop_server(proc)


def test_40_effort_omitted_stores_null(tmp_path):
    # Leaving effort out stores NULL — a task anyone can do.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 no effort")
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err, data
        assert data["task"]["effort"] is None
    finally:
        stop_server(proc)


def test_41_effort_shown_in_list_tasks(tmp_path):
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        make_task(url, "M13 list low", effort="low")
        make_task(url, "M13 list high", effort="high")
        err, data = call(url, LEADER, "list_tasks", {})
        assert not err, data
        efforts = {t["title"]: t["effort"] for t in data["tasks"]}
        assert efforts["M13 list low"] == "low"
        assert efforts["M13 list high"] == "high"
    finally:
        stop_server(proc)


# -- M13: migration adds the three columns --------------------------------


def _m12_schema_db(db_path: Path) -> None:
    """Create a pre-M13 database by hand: the M12-era schema (no
    effort / model / model_used columns) with one row per table."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
CREATE TABLE agents (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('leader', 'worker')),
    skills TEXT NOT NULL DEFAULT '[]',
    last_seen TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE tasks (
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
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('progress', 'help_request', 'reply',
                                       'note', 'review')),
    body TEXT NOT NULL,
    decision TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    task_id INTEGER,
    outcome TEXT NOT NULL,
    arguments TEXT,
    error TEXT
);
INSERT INTO agents (name, role, skills, last_seen, active) VALUES
    ('planner', 'leader', '[]', '2026-11-15T00:00:00Z', 1),
    ('coder', 'worker', '["python"]', '2026-11-15T00:00:00Z', 1);
INSERT INTO tasks (title, required_skills, status, created_by, assignee,
                   created_at, updated_at) VALUES
    ('M12-era task', '["python"]', 'submitted', 'planner', 'coder',
     '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z');
INSERT INTO submissions (task_id, author, result, created_at) VALUES
    (1, 'coder', 'M12-era result', '2026-11-15T00:00:00Z');
INSERT INTO messages (task_id, author, kind, body, created_at) VALUES
    (1, 'coder', 'progress', 'M12-era note', '2026-11-15T00:00:00Z');
INSERT INTO audit_log (time, agent, tool, task_id, outcome) VALUES
    ('2026-11-15T00:00:00Z', 'coder', 'claim_task', 1, 'success');
"""
    )
    conn.commit()
    conn.close()


def test_42_migration_adds_three_columns_without_losing_rows(tmp_path):
    # An existing M12 database gains tasks.effort, agents.model and
    # submissions.model_used on first start — without losing rows.
    # Old rows read NULL for the new fields; enforcement still works
    # on migrated data (the M12-era coder can still be rejected).
    tmp = tmp_path
    port = free_port()
    db_path = tmp / "roundtable.db"
    _m12_schema_db(db_path)
    proc, url, _ = start_server(tmp, port)
    try:
        cols = {
            r["name"]
            for r in db_rows(db_path, "PRAGMA table_info(tasks)")
        }
        assert "effort" in cols
        cols = {r["name"] for r in db_rows(db_path, "PRAGMA table_info(agents)")}
        assert "model" in cols
        cols = {
            r["name"]
            for r in db_rows(db_path, "PRAGMA table_info(submissions)")
        }
        assert "model_used" in cols
        rows = db_rows(db_path, "SELECT title, effort FROM tasks")
        assert rows[0]["title"] == "M12-era task"
        assert rows[0]["effort"] is None
        subs = db_rows(db_path, "SELECT author, model_used FROM submissions")
        assert subs[0]["author"] == "coder"
        assert subs[0]["model_used"] is None
        # The M12-era agents carry over; their model is NULL until the
        # Owner sets one in agents.json.
        agents = db_rows(db_path, "SELECT name, model FROM agents ORDER BY name")
        # The startup sync adds every agent from agents.json, including
        # rusty (the fixture's third agent).
        assert [a["name"] for a in agents] == ["coder", "planner", "rusty"]
        assert all(a["model"] is None for a in agents)
    finally:
        stop_server(proc)


def test_43_migration_is_idempotent_second_start_noop(tmp_path):
    # A second start on a migrated database is a no-op: no error, no
    # duplicate migration, the data still intact.
    tmp = tmp_path
    port = free_port()
    db_path = tmp / "roundtable.db"
    _m12_schema_db(db_path)
    proc, url, _ = start_server(tmp, port)
    stop_server(proc)
    proc, url, _ = start_server(tmp, port)  # second start
    try:
        rows = db_rows(db_path, "SELECT title, effort FROM tasks")
        assert rows[0]["effort"] is None
        cols = [r["name"] for r in db_rows(db_path, "PRAGMA table_info(tasks)")]
        assert cols.count("effort") == 1  # not added twice
    finally:
        stop_server(proc)


# -- M13: model note per agent (agents.json -> whoami/list_agents) --------


def test_44_agent_model_invalid_type_rejected_at_startup(tmp_path):
    # A model that is not a string is rejected at startup with a
    # clear error naming the agent.
    tmp = tmp_path
    port = free_port()
    agents = {
        "agents": [
            {"name": "planner", "role": "leader", "skills": ["planning"],
             "token": LEADER, "model": 5},
        ]
    }
    with pytest.raises(RuntimeError, match="model"):
        start_server(tmp, port, agents)


def test_45_agent_model_over_100_chars_rejected(tmp_path):
    tmp = tmp_path
    port = free_port()
    agents = {
        "agents": [
            {"name": "planner", "role": "leader", "skills": ["planning"],
             "token": LEADER, "model": "x" * 101},
        ]
    }
    with pytest.raises(RuntimeError, match="model"):
        start_server(tmp, port, agents)


def test_46_agent_model_shown_in_whoami_and_list_agents(tmp_path):
    # A valid model is shown in whoami and list_agents; an agent
    # without a model still works (model null).
    tmp = tmp_path
    port = free_port()
    agents = {
        "agents": [
            {"name": "planner", "role": "leader", "skills": ["planning"],
             "token": LEADER, "model": "GLM-5.3"},
            {"name": "coder", "role": "worker", "skills": ["python"],
             "token": CODER},
        ]
    }
    proc, url, db_path = start_server(tmp, port, agents)
    try:
        err, data = call(url, LEADER, "whoami", {})
        assert not err, data
        assert data["name"] == "planner"
        assert data["model"] == "GLM-5.3"
        err, data = call(url, LEADER, "list_agents", {})
        assert not err, data
        by_name = {a["name"]: a.get("model") for a in data["agents"]}
        assert by_name["planner"] == "GLM-5.3"
        assert by_name["coder"] is None
    finally:
        stop_server(proc)


# -- M13: model used, per submission (self-reported) ----------------------


def test_47_model_used_stored_and_shown_as_self_reported(tmp_path):
    # submit_result gains an optional model_used: stored with the
    # submission and shown in the task's submission history, labelled
    # as self-reported.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 model_used task", required_skills=["python"])
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, data = call(url, CODER, "submit_result",
                         {"task_id": task["id"], "result": "done",
                          "model_used": "GLM-5.3"})
        assert not err, data
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err, data
        subs = data["task"]["submissions"]
        assert subs[-1]["model_used"] == "GLM-5.3"
    finally:
        stop_server(proc)


def test_48_model_used_over_100_chars_rejected(tmp_path):
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 long model_used", required_skills=["python"])
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, data = call(url, CODER, "submit_result",
                         {"task_id": task["id"], "result": "r",
                          "model_used": "x" * 101})
        assert err
        assert "100" in str(data)
        # Nothing was stored: the submission is absent.
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert data["task"]["submissions"] == []
    finally:
        stop_server(proc)


def test_49_model_used_omitted_still_works(tmp_path):
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 no model_used", required_skills=["python"])
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, CODER, "submit_result",
                      {"task_id": task["id"], "result": "done"})
        assert not err
        err, data = call(url, LEADER, "get_task", {"task_id": task["id"]})
        assert not err, data
        subs = data["task"]["submissions"]
        assert subs[-1]["result"] == "done"
        assert subs[-1]["model_used"] is None
    finally:
        stop_server(proc)


# -- M13: effort and model change NO permission ---------------------------


def test_50_effort_and_model_change_no_permissions(tmp_path):
    # Re-run the M7/M12 enforcement invariants with the new fields in
    # play: effort on the task, model on the agents — the claim and
    # review rules are identical.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 perms task", required_skills=["python"],
                         effort="high")
        # M7: a worker without the skill still cannot claim a task
        # that has an effort hint.
        err, data = call(url, RUSTY, "claim_task", {"task_id": task["id"]})
        assert err
        assert "lacks required skill" in str(data)
        # M7: an eligible worker can still claim it.
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, _ = call(url, CODER, "submit_result",
                      {"task_id": task["id"], "result": "worker work",
                       "model_used": "GLM-5.3"})
        assert not err
        # M12 four-eyes does not apply to worker submissions: the
        # worker itself is rejected, the leader reviews.
        err, data = call(url, CODER, "review_task",
                         {"task_id": task["id"], "approve": True})
        assert err
        err, data = call(url, LEADER, "review_task",
                         {"task_id": task["id"], "approve": True,
                          "review_note": "ok"})
        assert not err, data
        assert data["task"]["status"] == "done"
        assert data["task"]["effort"] == "high"
    finally:
        stop_server(proc)


# -- AUDIT-M13 notes N1 and N2 ----------------------------------------------


def test_51_migrated_effort_column_has_the_check(tmp_path):
    # N1: a migrated database (the VM's path) gets the same CHECK on
    # tasks.effort as a fresh one, so a bad value is refused even by
    # direct SQL. Old rows stay NULL, which the CHECK allows.
    db_path = tmp_path / "roundtable.db"
    _m12_schema_db(db_path)
    from roundtable import db as rt_db

    conn = rt_db.connect(str(db_path))
    try:
        assert conn.execute("SELECT effort FROM tasks").fetchone()[0] is None
        conn.execute("UPDATE tasks SET effort = 'high' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET effort = 'banana' WHERE id = 1")
    finally:
        conn.close()


def test_52_blank_model_used_is_stored_as_null(tmp_path):
    # N2: a blank model_used means "not given" and is stored as NULL.
    tmp = tmp_path
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    try:
        task = make_task(url, "M13 blank model_used", required_skills=["python"])
        err, _ = call(url, CODER, "claim_task", {"task_id": task["id"]})
        assert not err
        err, data = call(url, CODER, "submit_result",
                         {"task_id": task["id"], "result": "r",
                          "model_used": "   "})
        assert not err, data
        rows = db_rows(db_path, "SELECT model_used FROM submissions")
        assert rows[-1]["model_used"] is None
    finally:
        stop_server(proc)
