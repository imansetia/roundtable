"""Tests for M10: publication (Owner decision, 2026-11-14).

A stranger can try Roundtable on their own machine in minutes
(scripts/install-local.sh; an optional Dockerfile), and the
project has a safe public face: a read-only GET /status endpoint
returning aggregates only. Written test-first: each test here
failed against the M9 code and passes after the M10 change.
Self-contained helpers (no import from test_acceptance) so this
file can run on its own.

The status endpoint is the first server change since M7, so it
is treated as security-sensitive: no token is checked or needed,
no board content (titles, descriptions, results, messages) ever
leaves it, it is rate-limited per client IP, and it writes no
audit row. The /mcp bearer gate is untouched.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
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

DEPLOY = (REPO / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
SPEC = (REPO / "SPEC.md").read_text(encoding="utf-8")
CHANGELOG = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
ADD_AGENT = (REPO / "docs" / "ADD-AGENT.md").read_text(encoding="utf-8")
NGINX = (REPO / "deploy" / "nginx" / "roundtable.conf").read_text(encoding="utf-8")
INSTALL = (REPO / "scripts" / "install-local.sh").read_text(encoding="utf-8")
DOCKERFILE = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (REPO / "deploy" / ".dockerignore").read_text(encoding="utf-8")


def flat(text: str) -> str:
    """One line per paragraph: prose phrases must not depend on where
    the guide wraps its lines, and blockquote markers must not split
    a phrase. Code blocks are checked on raw text."""
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    return re.sub(r"\s+", " ", text)


DEPLOY_FLAT = flat(DEPLOY)
SPEC_FLAT = flat(SPEC)


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


def status_url(mcp_url: str) -> str:
    return mcp_url.removesuffix("/mcp") + "/status"


async def _call(session, tool: str, args: dict) -> dict:
    result = await session.call_tool(tool, args)
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


async def _with_session(mcp_url: str, token: str, coro) -> None:
    """Run ``coro(session)`` as one agent: the token goes on an
    httpx client (SDK 2.3.0's streamable_http_client takes no
    headers argument) and the client yields (read, write)."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, trust_env=False) as http:
        async with streamable_http_client(mcp_url, http_client=http) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await coro(session)


async def _drive_tasks(mcp_url: str) -> None:
    """One task in each status: open, claimed, submitted, done,
    cancelled. The titles/descriptions are distinctive so the
    no-content test can look for them."""
    ids: list[int] = []

    async def create(session) -> None:
        for label in ("open", "claimed", "submitted", "done", "cancelled"):
            task = await _call(
                session,
                "create_task",
                {
                    "title": f"SECRET-TITLE-{label}-7f3a9",
                    "description": f"SECRET-DESC-{label}-7f3a9",
                },
            )
            ids.append(task["task"]["id"])

    async def work(session) -> None:
        await _call(session, "claim_task", {"task_id": ids[1]})
        await _call(session, "claim_task", {"task_id": ids[2]})
        await _call(
            session,
            "submit_result",
            {"task_id": ids[2], "result": "SECRET-RESULT-submitted-7f3a9"},
        )
        await _call(session, "claim_task", {"task_id": ids[3]})
        await _call(
            session,
            "submit_result",
            {"task_id": ids[3], "result": "SECRET-RESULT-done-7f3a9"},
        )
        await _call(
            session,
            "post_message",
            {"task_id": ids[3], "body": "SECRET-MESSAGE-done-7f3a9"},
        )

    async def review(session) -> None:
        await _call(session, "review_task", {"task_id": ids[3], "approve": True})
        await _call(session, "cancel_task", {"task_id": ids[4]})

    await _with_session(mcp_url, LEADER, create)
    await _with_session(mcp_url, CODER, work)
    await _with_session(mcp_url, LEADER, review)


def test_status_returns_aggregates_only(tmp_path):
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        asyncio.run(_drive_tasks(mcp_url))
        with httpx.Client(trust_env=False, timeout=5) as http:
            resp = http.get(status_url(mcp_url))
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["board"] == "Roundtable"
        assert body["version"] == "0.1.0"
        assert body["tasks_by_status"] == {
            "open": 1,
            "claimed": 1,
            "submitted": 1,
            "done": 1,
            "cancelled": 1,
        }
        assert body["active_agents"] == 3
        # Server time is a real ISO timestamp, close to now.
        seen = datetime.fromisoformat(body["server_time"])
        assert abs((datetime.now(seen.tzinfo) - seen).total_seconds()) < 120
    finally:
        stop_server(proc)


def test_status_never_returns_board_content(tmp_path):
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        asyncio.run(_drive_tasks(mcp_url))
        with httpx.Client(trust_env=False, timeout=5) as http:
            resp = http.get(status_url(mcp_url))
        assert resp.status_code == 200
        text = resp.text
        for secret in (
            "SECRET-TITLE-open-7f3a9",
            "SECRET-DESC-claimed-7f3a9",
            "SECRET-RESULT-submitted-7f3a9",
            "SECRET-RESULT-done-7f3a9",
            "SECRET-MESSAGE-done-7f3a9",
        ):
            assert secret not in text
        # The response carries exactly the documented aggregate keys.
        assert set(resp.json().keys()) == {
            "ok",
            "board",
            "version",
            "tasks_by_status",
            "active_agents",
            "server_time",
        }
    finally:
        stop_server(proc)


def test_status_needs_no_token_and_ignores_one(tmp_path):
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            plain = http.get(status_url(mcp_url))
            bogus = http.get(
                status_url(mcp_url),
                headers={"Authorization": "Bearer not-a-real-token"},
            )
            real = http.get(
                status_url(mcp_url),
                headers={"Authorization": f"Bearer {LEADER}"},
            )
        assert plain.status_code == 200
        assert bogus.status_code == 200
        assert real.status_code == 200
        # A token changes nothing: the page is public, not gated.
        # (server_time moves between calls, so compare the rest.)
        plain_body = plain.json()
        bogus_body = bogus.json()
        real_body = real.json()
        for body in (plain_body, bogus_body, real_body):
            assert "server_time" in body
            del body["server_time"]
        assert plain_body == bogus_body == real_body
    finally:
        stop_server(proc)


def test_status_post_is_rejected(tmp_path):
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            resp = http.post(status_url(mcp_url), json={})
        assert resp.status_code == 405
    finally:
        stop_server(proc)


def test_status_rate_limit_per_ip(tmp_path):
    # A low limit for the test: 3 status requests per minute per IP.
    proc, mcp_url, db_path = start_server(tmp_path, free_port(), rate_limit=3)
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            codes = [
                http.get(status_url(mcp_url)).status_code for _ in range(4)
            ]
        assert codes == [200, 200, 200, 429]
        with httpx.Client(trust_env=False, timeout=5) as http:
            resp = http.get(status_url(mcp_url))
        assert resp.status_code == 429
        assert "rate" in resp.json().get("error", "").lower()
    finally:
        stop_server(proc)


def test_status_uses_forwarded_ip_behind_a_proxy(tmp_path):
    # Behind nginx the direct peer is the proxy (loopback), so the
    # limit keys on the first X-Forwarded-For entry. Two different
    # forwarded addresses get two different budgets.
    proc, mcp_url, db_path = start_server(tmp_path, free_port(), rate_limit=2)
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            a = [
                http.get(
                    status_url(mcp_url),
                    headers={"X-Forwarded-For": "203.0.113.1"},
                ).status_code
                for _ in range(3)
            ]
            b = http.get(
                status_url(mcp_url),
                headers={"X-Forwarded-For": "203.0.113.2"},
            ).status_code
        assert a == [200, 200, 429]
        assert b == 200
    finally:
        stop_server(proc)


def test_status_writes_no_audit_row(tmp_path):
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        asyncio.run(_drive_tasks(mcp_url))
        conn = sqlite3.connect(db_path)
        before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        with httpx.Client(trust_env=False, timeout=5) as http:
            for _ in range(3):
                assert http.get(status_url(mcp_url)).status_code == 200
        after = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        conn.close()
        assert after == before
    finally:
        stop_server(proc)


def test_mcp_gate_untouched(tmp_path):
    # The new public route must not weaken /mcp: no token -> 401,
    # token + empty body -> 400, exactly as before.
    proc, mcp_url, db_path = start_server(tmp_path, free_port())
    try:
        with httpx.Client(trust_env=False, timeout=5) as http:
            no_token = http.post(mcp_url, json={})
            assert no_token.status_code == 401
            with_token = http.post(
                mcp_url,
                json={},
                headers={"Authorization": f"Bearer {LEADER}"},
            )
            assert with_token.status_code == 400
            status = http.get(status_url(mcp_url))
            assert status.status_code == 200
    finally:
        stop_server(proc)


def test_install_local_script():
    # The script wraps the Shape A quick-start: venv, pip install,
    # agents.json via the Python token pattern (file stays mode
    # 600), run + whoami verification. No literal token anywhere.
    assert "python3 -m venv .venv" in INSTALL
    assert "pip install -e ." in INSTALL
    assert "agents.example.json agents.json" in INSTALL
    assert "secrets.token_urlsafe(48)" in INSTALL
    assert "chmod 600" in INSTALL
    assert "whoami" in INSTALL
    assert "401" in INSTALL
    assert "roundtable.pid" in INSTALL
    assert "loopback" in INSTALL.lower()
    assert not re.search(r"Bearer [A-Za-z0-9_\-]{20,}", INSTALL)


def test_install_local_script_runs(tmp_path):
    # The real run, in a temp copy of the repo: the script sets up
    # a venv, installs, generates tokens, starts the board, verifies
    # it (401 + whoami + /status), and leaves it running. The test
    # stops it via the PID file afterwards.
    copy = tmp_path / "roundtable"
    shutil.copytree(
        REPO,
        copy,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache",
            "roundtable.db*", "agents.json", "roundtable.pid", "roundtable.log",
        ),
    )
    proc = subprocess.run(
        ["bash", "scripts/install-local.sh"],
        cwd=str(copy),
        capture_output=True,
        text=True,
        timeout=300,
    )
    pid_file = copy / "roundtable.pid"
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        agents = json.loads((copy / "agents.json").read_text(encoding="utf-8"))
        tokens = [a["token"] for a in agents["agents"]]
        assert len(tokens) == 3 and len(set(tokens)) == 3
        assert all(len(t) == 64 for t in tokens)  # token_urlsafe(48)
        assert pid_file.exists()
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        with httpx.Client(trust_env=False, timeout=5) as http:
            no_token = http.post(
                "http://127.0.0.1:8000/mcp", json={}
            )
            assert no_token.status_code == 401
            status = http.get("http://127.0.0.1:8000/status")
            assert status.status_code == 200
            assert status.json()["tasks_by_status"]["open"] == 0
        # The whoami verification ran and printed the leader's name;
        # no token was ever printed.
        assert "planner" in proc.stdout
        for token in tokens:
            assert token not in proc.stdout
            assert token not in proc.stderr
    finally:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except (ValueError, ProcessLookupError):
                pass


def test_dockerfile():
    assert "FROM python:3.12-slim" in DOCKERFILE
    assert "VOLUME /data" in DOCKERFILE
    assert "ROUNDTABLE_DB_PATH=/data/roundtable.db" in DOCKERFILE
    assert "ROUNDTABLE_AGENTS_CONFIG=/config/agents.json" in DOCKERFILE
    assert "EXPOSE 8000" in DOCKERFILE
    assert 'CMD ["python", "-m", "roundtable"]' in DOCKERFILE
    # Published on the host's loopback only; never baked-in tokens.
    assert "127.0.0.1:8000:8000" in DOCKERFILE
    for line in DOCKERFILE.splitlines():
        if line.startswith("COPY"):
            assert "agents.json" not in line


def test_dockerignore():
    for entry in ("agents.json", ".env", ".venv", ".git", "roundtable.db"):
        assert entry in DOCKERIGNORE


def test_nginx_exposes_status():
    assert "location = /status" in NGINX
    assert "proxy_pass http://127.0.0.1:8000" in NGINX
    assert "X-Forwarded-For" in NGINX
    # Everything else is still 404, and the old "only /mcp" claim is gone.
    assert "location / {\n        return 404;" in NGINX
    assert "Only the MCP path is exposed" not in NGINX


def test_deploy_status_verification():
    # Part 11 gains the /status check; the guide gains an
    # "Updating to M10" section (backup first, audited commit,
    # nginx site reinstall, verify).
    assert "https://$PUBLIC_NAME/status" in DEPLOY
    assert "Updating to M10" in DEPLOY
    assert "only /mcp is exposed" not in DEPLOY_FLAT
    assert "only /mcp and /status are exposed" in DEPLOY_FLAT


def test_spec_status_rules():
    # SPEC section 7 gains the endpoint's rules: public, aggregates
    # only, rate-limited per IP, no audit row.
    assert "GET /status" in SPEC
    assert "aggregates only" in SPEC_FLAT
    assert "per client IP" in SPEC_FLAT
    assert "no audit row" in SPEC_FLAT
    assert "(M10)" in SPEC_FLAT
    assert "23. (M10)" in SPEC


def test_readme_public_status_section():
    assert "## Public status page" in README
    assert "GET /status" in README
    assert "aggregates only" in README.lower()
    assert "M10 in progress" in README
    assert "176 tests pass" in README
    assert "19 M10 publication" in README


def test_changelog_m10():
    assert "## M10" in CHANGELOG
    assert "No database schema change" in flat(CHANGELOG)
    assert "no new token type" in flat(CHANGELOG)
    assert "/mcp's bearer gate untouched" in flat(CHANGELOG)


def test_m10_scope_guard():
    # No schema change, no new token type, worker untouched, board
    # content never leaves the bearer gate.
    assert "worker service is untouched" in flat(CHANGELOG)
    assert "board content never leaves the bearer gate" in flat(CHANGELOG)


def test_add_agent_shapes_note_status():
    assert "/status" in ADD_AGENT
    assert "shapes b and c" in flat(ADD_AGENT).lower()
