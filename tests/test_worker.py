"""Tests for worker.py (SPEC section 8, milestone M2).

No test here calls a real model API or needs a real API key: the model
is a small fake OpenAI-compatible server on localhost (see FakeModel),
and every token/key is a fake test value written to tmp dirs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker as worker_mod
from worker import RoundtableWorker, WorkerConfig, WorkerError

REPO = Path(__file__).resolve().parent.parent

LEADER = "leader-test-token-" + "1" * 32
WORKER_TOKEN = "worker-test-token-" + "4" * 32
REVIEWER = "reviewer-test-token-" + "3" * 32
FAKE_KEY = "sk-test-fake-key-DO-NOT-LEAK-12345"

AGENTS_W = {
    "agents": [
        {"name": "planner", "role": "leader", "skills": ["planning"], "token": LEADER},
        {"name": "deepseek", "role": "worker", "skills": ["python"], "token": WORKER_TOKEN},
        {"name": "reviewer", "role": "worker", "skills": ["review"], "token": REVIEWER},
    ]
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(tmp: Path, port: int, agents: dict = AGENTS_W, rate_limit: int = 60):
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


def payload(result):
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


def make_task(url: str, title: str = "Worker test task", **kwargs) -> dict:
    err, data = call(url, LEADER, "create_task", {"title": title, **kwargs})
    assert not err, f"create_task failed: {data}"
    return data["task"]


def get_task(url: str, task_id: int) -> dict:
    err, data = call(url, LEADER, "get_task", {"task_id": task_id})
    assert not err, f"get_task failed: {data}"
    return data["task"]


# -- Fake OpenAI-compatible model server ---------------------------------

class FakeModel:
    """A tiny OpenAI-compatible server: POST /chat/completions only.

    Modes: "ok" (returns ``answer``), "error500", "empty" (no choices),
    "badjson", "slow" (sleeps ``delay`` seconds, then returns ``answer``).
    Every request (headers + JSON body) is recorded in ``requests``.
    """

    def __init__(self, mode: str = "ok", answer: str = "fake answer", delay: float = 0.0):
        self.mode = mode
        self.answer = answer
        self.delay = delay
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = {}
                fake.requests.append(
                    {"headers": dict(self.headers), "body": body}
                )
                if fake.delay:
                    time.sleep(fake.delay)
                try:
                    if fake.mode == "error500":
                        self._send(500, {"error": {"message": "fake model boom"}})
                    elif fake.mode == "badjson":
                        self._send_raw(200, b"this is not json{")
                    elif fake.mode == "empty":
                        self._send(200, {"choices": []})
                    else:
                        self._send(
                            200,
                            {"choices": [{"message": {"role": "assistant", "content": fake.answer}}]},
                        )
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send(self, status: int, obj: dict) -> None:
                self._send_raw(status, json.dumps(obj).encode("utf-8"))

            def _send_raw(self, status: int, raw: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


@pytest.fixture()
def server(tmp_path_factory):
    # Function-scoped on purpose: release tests leave open tasks behind,
    # and a shared board would let a later test claim another test's task.
    tmp = tmp_path_factory.mktemp("rt-worker")
    port = free_port()
    proc, url, db_path = start_server(tmp, port)
    yield {"url": url, "db": tmp / "roundtable.db"}
    stop_server(proc)


@pytest.fixture()
def fake():
    model = FakeModel()
    model.start()
    yield model
    model.stop()


def worker_config(url: str, model_url: str, **overrides) -> WorkerConfig:
    env = {
        "ROUNDTABLE_URL": url,
        "ROUNDTABLE_TOKEN": WORKER_TOKEN,
        "WORKER_MODEL_BASE_URL": model_url,
        "WORKER_MODEL_NAME": "fake-model",
        "WORKER_API_KEY": FAKE_KEY,
        "WORKER_POLL_INTERVAL": "0.1",
        "WORKER_MAX_TASKS_PER_HOUR": "10",
        "WORKER_MODEL_TIMEOUT": "10",
    }
    env.update(overrides)
    return WorkerConfig.from_env(env)


def drive(config: WorkerConfig) -> str:
    """Run one worker poll cycle against the board."""
    async def _run():
        async with RoundtableWorker(config) as w:
            return await w.run_once()

    return asyncio.run(_run())


def drive_many(config: WorkerConfig, n: int, sleeps: list[float] | None = None) -> list[str]:
    """Run ``n`` poll cycles on ONE worker instance (cool-down, failure
    counts and the hourly cap are per-process state, so a fresh instance
    per cycle would never see them). ``sleeps[i]`` is slept after cycle i.
    """
    sleeps = sleeps or []

    async def _run():
        async with RoundtableWorker(config) as w:
            out = []
            for i in range(n):
                out.append(await w.run_once())
                if i < len(sleeps):
                    await asyncio.sleep(sleeps[i])
            return out

    return asyncio.run(_run())


# -- Skill matching, submit, release --------------------------------------

def test_claims_only_tasks_matching_its_skill(server, fake):
    url = server["url"]
    cfg = worker_config(url, fake.url)
    python_task = make_task(url, "Python task", required_skill="python")
    rust_task = make_task(url, "Rust task", required_skill="rust")
    general_task = make_task(url, "General task")  # no skill: matches any worker
    assert drive(cfg) == "submitted"   # python task
    assert drive(cfg) == "submitted"   # general task (rust skipped)
    assert drive(cfg) == "none"        # only the rust task is left
    assert get_task(url, python_task["id"])["status"] == "submitted"
    assert get_task(url, general_task["id"])["status"] == "submitted"
    rust = get_task(url, rust_task["id"])
    assert rust["status"] == "open" and rust["assignee"] is None


def test_does_not_claim_tasks_assigned_to_another_worker(server, fake):
    url = server["url"]
    cfg = worker_config(url, fake.url)
    task = make_task(url, "For reviewer", required_skill="review", assignee="reviewer")
    assert drive(cfg) == "none"
    assert get_task(url, task["id"])["status"] == "open"


def test_submits_the_models_answer(server, fake):
    url = server["url"]
    fake.answer = "5"
    cfg = worker_config(url, fake.url)
    task = make_task(
        url, "Add two numbers", description="What is 2+3? Answer with just the number.",
        required_skill="python",
    )
    assert drive(cfg) == "submitted"
    done = get_task(url, task["id"])
    assert done["status"] == "submitted"
    assert done["result"] == "5"
    # The model got the task as user text, with a system prompt that
    # calls the task text data from another agent.
    assert len(fake.requests) == 1
    messages = fake.requests[0]["body"]["messages"]
    assert messages[0]["role"] == "system"
    assert "another agent" in messages[0]["content"]
    assert "data" in messages[0]["content"]
    assert "Add two numbers" in messages[1]["content"]
    assert "2+3" in messages[1]["content"]
    # ...and the API key did reach the model (in the header, not the log).
    assert fake.requests[0]["headers"].get("Authorization") == f"Bearer {FAKE_KEY}"


def test_releases_task_when_model_call_fails(server, fake):
    url = server["url"]
    fake.mode = "error500"
    cfg = worker_config(url, fake.url)
    task = make_task(url, "Failing model task", required_skill="python")
    assert drive(cfg) == "released"
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None


def test_releases_task_when_model_reply_is_empty(server, fake):
    url = server["url"]
    fake.mode = "empty"
    cfg = worker_config(url, fake.url)
    task = make_task(url, "Empty reply task", required_skill="python")
    assert drive(cfg) == "released"
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None


def test_releases_task_when_answer_is_too_long(server, fake):
    url = server["url"]
    fake.answer = "x" * 20_001  # over the server's 20,000-char result cap
    cfg = worker_config(url, fake.url)
    task = make_task(url, "Too-long answer task", required_skill="python")
    assert drive(cfg) == "released"
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None


def test_respects_max_tasks_per_hour_cap(server, fake):
    url = server["url"]
    cfg = worker_config(url, fake.url, WORKER_MAX_TASKS_PER_HOUR="1")
    first = make_task(url, "Cap task 1", required_skill="python")
    second = make_task(url, "Cap task 2", required_skill="python")
    # One worker instance across both cycles: the cap counts what this
    # process submitted in the last hour, so a fresh instance per cycle
    # would never see it.
    async def _drive_twice():
        async with RoundtableWorker(cfg) as w:
            return await w.run_once(), await w.run_once()

    first_result, second_result = asyncio.run(_drive_twice())
    assert first_result == "submitted"
    assert second_result == "capped"
    assert get_task(url, first["id"])["status"] == "submitted"
    second_now = get_task(url, second["id"])
    assert second_now["status"] == "open" and second_now["assignee"] is None


# -- Config ----------------------------------------------------------------

def test_config_missing_vars_fails_fast():
    with pytest.raises(WorkerError, match="ROUNDTABLE_TOKEN"):
        WorkerConfig.from_env({})
    with pytest.raises(WorkerError, match="WORKER_POLL_INTERVAL"):
        WorkerConfig.from_env(
            {
                "ROUNDTABLE_TOKEN": "x" * 32,
                "WORKER_MODEL_BASE_URL": "http://127.0.0.1:9",
                "WORKER_MODEL_NAME": "m",
                "WORKER_API_KEY": "k",
                "WORKER_POLL_INTERVAL": "0",
            }
        )


def test_worker_exits_nonzero_without_config(tmp_path):
    proc = subprocess.run(
        [sys.executable, "worker.py"],
        cwd=str(REPO),
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "ROUNDTABLE_TOKEN" in proc.stderr


# -- AUDIT-M2: W1 — the cap must count every model call --------------------

def test_cap_counts_every_model_call_not_only_submits(server, fake):
    """A task whose answer is always too long must not burn quota: the
    first failed attempt already uses up a max=1 hourly cap."""
    url = server["url"]
    fake.answer = "x" * 25_000  # always over the 20,000-char result cap
    cfg = worker_config(url, fake.url, WORKER_MAX_TASKS_PER_HOUR="1")
    task = make_task(url, "Always too long", required_skill="python")
    assert drive_many(cfg, 2) == ["released", "capped"]  # no second paid model call
    assert len(fake.requests) == 1
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None


def test_failed_task_cools_down_then_retries(server, fake):
    url = server["url"]
    fake.mode = "error500"
    cfg = worker_config(url, fake.url, WORKER_COOLDOWN_SECONDS="0.2")
    task = make_task(url, "Flaky task", required_skill="python")
    # Released, then in cool-down (nothing to do), then retried after it.
    assert drive_many(cfg, 3, sleeps=[0, 0.3]) == ["released", "none", "released"]
    assert len(fake.requests) == 2
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None


def test_three_failures_posts_help_request_and_stops_retrying(server, fake):
    url = server["url"]
    fake.mode = "error500"
    cfg = worker_config(
        url, fake.url, WORKER_COOLDOWN_SECONDS="0.2", WORKER_MAX_FAILURES="3"
    )
    task = make_task(url, "Always failing task", required_skill="python")
    assert drive_many(cfg, 4, sleeps=[0.3, 0.3, 0.3]) == [
        "released",
        "released",
        "released",
        "none",
    ]
    assert len(fake.requests) == 3
    done = get_task(url, task["id"])
    assert done["status"] == "open" and done["assignee"] is None
    helps = [m for m in done["messages"] if m["kind"] == "help_request"]
    assert len(helps) == 1
    assert "3" in helps[0]["body"]
    assert FAKE_KEY not in helps[0]["body"]
    assert "x" * 100 not in helps[0]["body"]  # no model output in it


# -- AUDIT-M2: W2 — a crashed worker must not leave its task claimed ------

def test_startup_releases_tasks_claimed_before_a_crash(server, fake, caplog):
    url = server["url"]
    task = make_task(url, "Crashed task", required_skill="python")
    err, _ = call(url, WORKER_TOKEN, "claim_task", {"task_id": task["id"]})
    assert not err
    assert get_task(url, task["id"])["status"] == "claimed"
    cfg = worker_config(url, fake.url)

    async def _connect():
        async with RoundtableWorker(cfg) as w:
            return w.name

    with caplog.at_level(logging.INFO, logger="roundtable.worker"):
        assert asyncio.run(_connect()) == "deepseek"
    back = get_task(url, task["id"])
    assert back["status"] == "open" and back["assignee"] is None
    assert str(task["id"]) in caplog.text


# -- AUDIT-M2: W3 — matching tasks must not be starved by paging ----------

def test_pages_past_non_matching_tasks(tmp_path, fake):
    port = free_port()
    proc, url, _ = start_server(tmp_path, port, rate_limit=1000)
    try:
        for i in range(55):  # more than one page of non-matching tasks
            make_task(url, f"Rust task {i}", required_skill="rust")
        target = make_task(url, "Python target", required_skill="python")
        cfg = worker_config(url, fake.url)
        assert drive(cfg) == "submitted"
        assert get_task(url, target["id"])["status"] == "submitted"
    finally:
        stop_server(proc)


# -- AUDIT-M2: W4 — secrets must not travel over plain HTTP ----------------

def test_config_rejects_plain_http_to_non_loopback():
    base = {
        "ROUNDTABLE_TOKEN": "x" * 32,
        "WORKER_MODEL_BASE_URL": "https://api.example.com/v1",
        "WORKER_MODEL_NAME": "m",
        "WORKER_API_KEY": "k",
    }
    # https anywhere is fine, http loopback is fine.
    WorkerConfig.from_env({**base, "ROUNDTABLE_URL": "https://board.example.com/mcp"})
    for ok_url in (
        "http://127.0.0.1:8000/mcp",
        "http://localhost:8000/mcp",
        "http://[::1]:8000/mcp",
    ):
        WorkerConfig.from_env({**base, "ROUNDTABLE_URL": ok_url})
    # http to a non-loopback host must fail fast, naming the variable.
    with pytest.raises(WorkerError, match="ROUNDTABLE_URL"):
        WorkerConfig.from_env({**base, "ROUNDTABLE_URL": "http://192.168.1.1:8000/mcp"})
    with pytest.raises(WorkerError, match="WORKER_MODEL_BASE_URL"):
        WorkerConfig.from_env({**base, "WORKER_MODEL_BASE_URL": "http://api.example.com/v1"})
    with pytest.raises(WorkerError, match="WORKER_MODEL_BASE_URL"):
        WorkerConfig.from_env({**base, "WORKER_MODEL_BASE_URL": "ftp://api.example.com/v1"})


# -- AUDIT-M2: W5 — httpx must be a runtime dependency ---------------------

def test_httpx_is_a_runtime_dependency():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(d.startswith("httpx") for d in deps), (
        "httpx must be a runtime dependency, not dev-only"
    )


# -- AUDIT-M2 recommended: untrusted titles must not forge log lines ------

def test_task_titles_are_sanitized_before_logging(server, fake, caplog):
    from worker import _safe_log_text

    assert _safe_log_text("a\nb\x1b[31mc\x7fd") == "ab[31mcd"
    url = server["url"]
    cfg = worker_config(url, fake.url)
    task = make_task(
        url, "Evil title\nFORGED LOG LINE\n\x1b[31mred", required_skill="python"
    )
    with caplog.at_level(logging.INFO, logger="roundtable.worker"):
        assert drive(cfg) == "submitted"
    assert "\nFORGED LOG LINE\n" not in caplog.text
    assert "\x1b" not in caplog.text
    assert all("\n" not in r.message for r in caplog.records)

def _worker_env(url: str, model_url: str, **overrides) -> dict:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO),
        "ROUNDTABLE_URL": url,
        "ROUNDTABLE_TOKEN": WORKER_TOKEN,
        "WORKER_MODEL_BASE_URL": model_url,
        "WORKER_MODEL_NAME": "fake-model",
        "WORKER_API_KEY": FAKE_KEY,
        "WORKER_POLL_INTERVAL": "0.1",
        "WORKER_MAX_TASKS_PER_HOUR": "10",
        "WORKER_MODEL_TIMEOUT": "10",
    }
    env.update(overrides)
    return env


def _wait_until(fn, timeout: float = 10.0, desc: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {desc}")


def test_exits_cleanly_on_ctrl_c_while_working(tmp_path):
    port = free_port()
    proc, url, _ = start_server(tmp_path, port)
    fake = FakeModel(mode="slow", answer="slow answer", delay=10)
    fake.start()
    try:
        task = make_task(url, "Slow model task", required_skill="python")
        worker_proc = subprocess.Popen(
            [sys.executable, "worker.py"],
            cwd=str(REPO),
            env=_worker_env(url, fake.url),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            # Wait until the worker has claimed the task (model call in flight).
            _wait_until(
                lambda: get_task(url, task["id"])["status"] == "claimed",
                timeout=10,
                desc="worker to claim the task",
            )
            worker_proc.send_signal(signal.SIGINT)  # Ctrl+C
            rc = worker_proc.wait(timeout=15)
            assert rc == 0, f"worker exited {rc}, not cleanly"
            back = get_task(url, task["id"])
            assert back["status"] == "open" and back["assignee"] is None
        finally:
            if worker_proc.poll() is None:
                worker_proc.kill()
                worker_proc.wait(timeout=5)
    finally:
        fake.stop()
        stop_server(proc)


def test_exits_cleanly_on_ctrl_c_when_idle(tmp_path):
    port = free_port()
    proc, url, _ = start_server(tmp_path, port)
    fake = FakeModel()
    fake.start()
    try:
        worker_proc = subprocess.Popen(
            [sys.executable, "worker.py"],
            cwd=str(REPO),
            env=_worker_env(url, fake.url),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            time.sleep(1.5)  # let it connect and poll once
            assert worker_proc.poll() is None
            worker_proc.send_signal(signal.SIGINT)
            assert worker_proc.wait(timeout=10) == 0
        finally:
            if worker_proc.poll() is None:
                worker_proc.kill()
                worker_proc.wait(timeout=5)
    finally:
        fake.stop()
        stop_server(proc)


def test_never_prints_or_logs_the_api_key(tmp_path):
    port = free_port()
    proc, url, _ = start_server(tmp_path, port)
    fake = FakeModel(answer="key test answer")
    fake.start()
    try:
        task = make_task(url, "Key secrecy task", required_skill="python")
        worker_proc = subprocess.Popen(
            [sys.executable, "worker.py"],
            cwd=str(REPO),
            env=_worker_env(url, fake.url),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until(
                lambda: get_task(url, task["id"])["status"] == "submitted",
                timeout=30,
                desc="worker to submit",
            )
            worker_proc.send_signal(signal.SIGINT)
            assert worker_proc.wait(timeout=10) == 0
            out, _ = worker_proc.communicate(timeout=5)
        finally:
            if worker_proc.poll() is None:
                worker_proc.kill()
                worker_proc.wait(timeout=5)
        assert FAKE_KEY not in out, "API key leaked into worker output"
        assert WORKER_TOKEN not in out, "board token leaked into worker output"
        assert fake.requests, "model was never called"
        assert fake.requests[0]["headers"].get("Authorization") == f"Bearer {FAKE_KEY}"
    finally:
        fake.stop()
        stop_server(proc)
