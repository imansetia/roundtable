"""Tests for the ROUNDTABLE_ALLOWED_HOSTS setting (M3).

Background: MCP SDK 2.3.0 enables DNS-rebinding protection with
allowed_hosts limited to loopback, so behind nginx a request whose Host
is the public DNS name is rejected with 421 before any tool runs. The
setting adds public names to the loopback list; it never disables the
protection and never removes loopback.

Probed live while writing these tests: valid token + Host localhost ->
400 (reaches the app); valid token + public Host -> 421; valid token +
evil Host -> 421.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from roundtable.config import Settings  # noqa: E402

LEADER = "leader-test-token-" + "1" * 32
CODER = "coder-test-token-" + "2" * 32

AGENTS = {
    "agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "coder", "role": "worker", "skills": ["python"], "token": CODER},
    ]
}

PUBLIC_HOST = "mylabel.westeurope.cloudapp.azure.com"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(tmp: Path, port: int, extra_env: dict | None = None):
    config = tmp / "agents.json"
    config.write_text(__import__("json").dumps(AGENTS), encoding="utf-8")
    db_path = tmp / "roundtable.db"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO),
            "ROUNDTABLE_HOST": "127.0.0.1",
            "ROUNDTABLE_PORT": str(port),
            "ROUNDTABLE_DB_PATH": str(db_path),
            "ROUNDTABLE_AGENTS_CONFIG": str(config),
            "ROUNDTABLE_RATE_LIMIT_PER_MINUTE": "60",
        }
    )
    if extra_env:
        env.update(extra_env)
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
                return proc, url
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


def post_host(url: str, host: str, token: str | None) -> int:
    headers = {"Content-Type": "application/json", "Host": host}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(trust_env=False, timeout=5) as http:
        return http.post(url, content=b"{}", headers=headers).status_code


def test_public_host_accepted_when_configured(tmp_path):
    port = free_port()
    proc, url = start_server(
        tmp_path, port, {"ROUNDTABLE_ALLOWED_HOSTS": PUBLIC_HOST}
    )
    try:
        # The configured public name reaches the app (400 = valid MCP
        # app, invalid body — not a Host rejection). nginx sends it bare
        # (proxy_set_header Host $host), so the bare form must work.
        assert post_host(url, PUBLIC_HOST, LEADER) == 400
        # Loopback still works: the setting adds to it, never replaces it.
        # Real clients send Host with a port (curl, httpx do).
        assert post_host(url, f"127.0.0.1:{port}", LEADER) == 400
        assert post_host(url, f"localhost:{port}", LEADER) == 400
        # An unlisted host is still rejected with 421 for authenticated
        # requests. Without a token, auth answers first (401) — the Host
        # check runs after auth, inside the MCP app, and still protects
        # every authenticated request.
        assert post_host(url, "evil.example.com", LEADER) == 421
        assert post_host(url, "evil.example.com", None) == 401
        # Auth still runs first for the public host: no token -> 401.
        assert post_host(url, PUBLIC_HOST, None) == 401
    finally:
        stop_server(proc)


def test_public_host_rejected_without_the_setting(tmp_path):
    port = free_port()
    proc, url = start_server(tmp_path, port)
    try:
        assert post_host(url, PUBLIC_HOST, LEADER) == 421
        assert post_host(url, f"127.0.0.1:{port}", LEADER) == 400
    finally:
        stop_server(proc)


def test_allowed_hosts_parsing(monkeypatch):
    monkeypatch.delenv("ROUNDTABLE_ALLOWED_HOSTS", raising=False)
    assert Settings.from_env().allowed_hosts is None

    monkeypatch.setenv("ROUNDTABLE_ALLOWED_HOSTS", PUBLIC_HOST)
    hosts = Settings.from_env().allowed_hosts
    assert hosts is not None
    # A bare hostname is accepted with and without a port.
    assert PUBLIC_HOST in hosts
    assert f"{PUBLIC_HOST}:*" in hosts

    monkeypatch.setenv(
        "ROUNDTABLE_ALLOWED_HOSTS", f"  {PUBLIC_HOST} , other.example.com:*  "
    )
    hosts = Settings.from_env().allowed_hosts
    assert hosts is not None
    assert PUBLIC_HOST in hosts and "other.example.com:*" in hosts

    # A bare "*" would allow any host: never accepted.
    monkeypatch.setenv("ROUNDTABLE_ALLOWED_HOSTS", "*")
    with pytest.raises(ValueError, match="ROUNDTABLE_ALLOWED_HOSTS"):
        Settings.from_env()
    monkeypatch.setenv("ROUNDTABLE_ALLOWED_HOSTS", "example.com, *")
    with pytest.raises(ValueError, match="ROUNDTABLE_ALLOWED_HOSTS"):
        Settings.from_env()
    # Not a hostname at all.
    monkeypatch.setenv("ROUNDTABLE_ALLOWED_HOSTS", "example.com/path")
    with pytest.raises(ValueError, match="ROUNDTABLE_ALLOWED_HOSTS"):
        Settings.from_env()
    monkeypatch.setenv("ROUNDTABLE_ALLOWED_HOSTS", "   ")
    with pytest.raises(ValueError, match="ROUNDTABLE_ALLOWED_HOSTS"):
        Settings.from_env()
