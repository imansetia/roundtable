"""M11: publish as a public MCP — package identity, CLI, landing page, CI.

Written first (TDD): these tests fail on the M10 tree (no cli.py,
no roundtable-mcp name, no landing page, no CI workflow) and pass
once M11 lands. Owner decision, 2026-11-14. PR into main, do NOT
merge — ZCode audits; Gemini drafts the README landing copy in
parallel; Claude co-signs on 2026-11-15. PyPI upload is OWNER-ONLY.
"""

import json
import os
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
SPEC = (ROOT / "SPEC.md").read_text(encoding="utf-8")
QUICKSTART = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CLI = (ROOT / "roundtable" / "cli.py").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def run_cli(*args: str, cwd: Path | None = None, env: dict | None = None):
    """Run the CLI as `python -m roundtable.cli ...` (no installed
    console script needed). Returns the CompletedProcess."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "roundtable.cli", *args],
        cwd=str(cwd) if cwd else None,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- package identity (pyproject) ----------------------------------------


def test_pyproject_distribution_name():
    assert PYPROJECT["project"]["name"] == "roundtable-mcp"


def test_pyproject_version_matches_package():
    assert PYPROJECT["project"]["version"] == "0.2.0"
    import roundtable

    assert roundtable.__version__ == "0.2.0"


def test_pyproject_license_python_classifiers():
    project = PYPROJECT["project"]
    assert project["license"] == {"text": "MIT"}
    assert project["requires-python"] == ">=3.11"
    classifiers = project["classifiers"]
    assert "License :: OSI Approved :: MIT License" in classifiers
    assert "Programming Language :: Python :: 3" in classifiers
    assert "Programming Language :: Python :: 3.11" in classifiers
    assert project["readme"] == "README.md"
    assert "Shared task board" in project["description"]


def test_pyproject_console_script():
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts["roundtable"] == "roundtable.cli:main"


# --- CLI: init --------------------------------------------------------------


def test_cli_init_creates_agents_json(tmp_path):
    result = run_cli("init", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    agents_path = tmp_path / "agents.json"
    assert agents_path.exists()
    # Mode 600: tokens are never world-readable.
    assert agents_path.stat().st_mode & 0o777 == 0o600
    data = json.loads(agents_path.read_text(encoding="utf-8"))
    agents = data["agents"]
    assert len(agents) == 3
    assert sum(1 for a in agents if a["role"] == "leader") == 1
    tokens = [a["token"] for a in agents]
    assert len(set(tokens)) == 3
    assert all(len(t) == 64 for t in tokens)
    # The wizard prints the connection info — never the tokens.
    assert "http://127.0.0.1:8000/mcp" in result.stdout
    assert "roundtable run" in result.stdout
    for token in tokens:
        assert token not in result.stdout
        assert token not in result.stderr


def test_cli_init_never_overwrites(tmp_path):
    agents_path = tmp_path / "agents.json"
    agents_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    result = run_cli("init", cwd=tmp_path)
    assert result.returncode != 0
    assert agents_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert "already exists" in result.stdout + result.stderr


def test_cli_no_args_prints_usage():
    result = run_cli()
    assert result.returncode != 0
    assert "usage" in (result.stdout + result.stderr).lower()


# --- CLI: run ---------------------------------------------------------------


def test_cli_run_starts_board(tmp_path):
    # init, then run the board and probe it: no token -> 401,
    # /status -> 200. Same defaults as `python -m roundtable`.
    assert run_cli("init", cwd=tmp_path).returncode == 0
    env = {
        "ROUNDTABLE_DB_PATH": str(tmp_path / "roundtable.db"),
        "ROUNDTABLE_AGENTS_CONFIG": str(tmp_path / "agents.json"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "roundtable.cli", "run"],
        cwd=str(tmp_path),
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            # POST, not GET: /mcp only serves POST, and the bearer
            # middleware answers 401 before the app routes it.
            req = urllib.request.Request(
                "http://127.0.0.1:8000/mcp",
                data=b"",
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=2)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    up = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        assert up, "board did not come up (no 401 probe)"
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/status", timeout=5
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["version"] == "0.2.0"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)


# --- README as landing page -------------------------------------------------


def test_readme_landing_pitch():
    assert "shared task board" in README
    assert "Install it in one line" in README
    assert "pip install roundtable-mcp" in README
    # R1: the bare `uvx roundtable-mcp` form only prints usage
    # (the console script takes a subcommand) — the working form
    # names the script after --from.
    assert "uvx --from roundtable-mcp roundtable init" in README
    assert "uvx --from roundtable-mcp roundtable run" in README


def test_no_bare_uvx_form():
    # R1 regression guard: the bare form must not appear anywhere
    # a stranger would copy it from.
    assert "uvx roundtable-mcp" not in README
    assert "uvx roundtable-mcp" not in QUICKSTART
    assert "uvx roundtable-mcp" not in CHANGELOG


def test_readme_client_snippets():
    for client in ["Claude Code", "ZCode", "opencode", "Cursor", "VS Code"]:
        assert client in README
    assert "claude mcp add" in README
    assert "opencode mcp add" in README
    assert ".cursor/mcp.json" in README
    assert ".vscode/mcp.json" in README
    assert "mcpServers" in README


def test_readme_badges():
    assert "ci.yml/badge.svg" in README
    assert "License-MIT" in README
    assert "python-3.11" in README


def test_readme_links_walkthrough_and_tools():
    assert "QUICKSTART.md" in README
    assert "ADD-AGENT.md" in README
    assert "whoami" in README
    assert "create_task" in README


def test_readme_status_m11():
    assert "M11 in progress" in README
    assert "198 tests pass" in README
    assert "19 M11 publication" in README


# --- QUICKSTART, CI, SPEC, CHANGELOG ----------------------------------------


def test_quickstart_front_door():
    assert "pip install roundtable-mcp" in QUICKSTART
    assert "roundtable init" in QUICKSTART
    assert "roundtable run" in QUICKSTART
    assert "First task" in QUICKSTART
    assert "ADD-AGENT.md" in QUICKSTART


def test_ci_workflow():
    assert "ubuntu-latest" in CI
    assert "3.12" in CI
    assert "pytest" in CI
    assert "sqlite3" in CI
    assert "push" in CI
    assert "pull_request" in CI


def test_spec_m11():
    assert "24. (M11)" in SPEC
    assert "(M11)" in SPEC


def test_changelog_m11():
    assert "## M11" in CHANGELOG
    assert "roundtable-mcp" in CHANGELOG
    assert "0.2.0" in CHANGELOG
    assert "v0.2.0" in CHANGELOG
    # Gemini Argon's parallel landing draft (arrives through the
    # Owner) is recorded as a parallel contribution.
    assert "Gemini" in CHANGELOG
    assert "parallel" in CHANGELOG.lower()


def test_m11_scope_guard():
    # No changes to tool behaviour or schema; the server's core
    # logic untouched (the CLI is a wrapper around what already
    # exists); worker service untouched; board content never
    # leaves the bearer gate.
    assert "No changes to tool behaviour or schema" in flat(CHANGELOG)
    assert "core logic untouched" in flat(CHANGELOG)
    assert "worker service is untouched" in flat(CHANGELOG)
    assert "board content never leaves the bearer gate" in flat(CHANGELOG)
    # The CLI really is a wrapper: run delegates to server.main.
    assert "from .server import main" in CLI
