"""Guards for M8 onboarding & operations: the ADD-AGENT runbook, the
Part 0 "getting connected" sections in DEPLOY.md and CONNECT.md, and
the worker.py always-on service templates. Docs, scripts and deploy
templates only — no server (roundtable/) changes, so these tests read
files, never the server.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADD_AGENT = (ROOT / "docs" / "ADD-AGENT.md").read_text(encoding="utf-8")
DEPLOY = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
CONNECT = (ROOT / "docs" / "CONNECT.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKER_SERVICE = (
    ROOT / "deploy" / "roundtable-worker.service"
).read_text(encoding="utf-8")
WORKER_ENV = (
    ROOT / "deploy" / "roundtable-worker.env.example"
).read_text(encoding="utf-8")


def flat(text: str) -> str:
    """One line per paragraph: prose phrases must not depend on where
    the guide wraps its lines, and blockquote markers must not split
    a phrase. Code blocks are checked on raw text."""
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    return re.sub(r"\s+", " ", text)


ADD_AGENT_FLAT = flat(ADD_AGENT)
DEPLOY_FLAT = flat(DEPLOY)
CONNECT_FLAT = flat(CONNECT)


def section(text: str, heading: str) -> str:
    """Return the text from one '## <heading> ...' to the next '##'.
    The heading is a prefix: 'Part 0' matches
    '## Part 0 — Getting connected ...'."""
    pattern = re.compile(
        r"## " + re.escape(heading) + r"[^\n]*\n(.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"no section {heading!r}"
    return match.group(1)


def test_add_agent_runbook_covers_the_steps():
    # The runbook is the single place a beginner adds an agent:
    # choose the identity, edit agents.json the safe way, restart,
    # hand the token over without showing it, connect the app, verify.
    assert "agents.json" in ADD_AGENT
    assert "secrets.token_urlsafe(48)" in ADD_AGENT  # heredoc pattern
    assert "mode 600" in ADD_AGENT_FLAT
    assert "exactly one leader" in ADD_AGENT_FLAT
    assert "sudo systemctl restart roundtable" in ADD_AGENT
    assert "whoami" in ADD_AGENT
    assert "Part 0" in ADD_AGENT  # connection trouble goes there
    assert "Step 16" in ADD_AGENT  # revocation lives in CONNECT.md
    assert "clipboard" in ADD_AGENT_FLAT
    assert "Clear all" in ADD_AGENT
    assert "mcpServers" in ADD_AGENT  # generic MCP client shape
    assert "opencode" in ADD_AGENT_FLAT
    assert "opencode.jsonc" in ADD_AGENT
    assert "worker.py" in ADD_AGENT
    assert "API-only" in ADD_AGENT_FLAT


def test_add_agent_no_literal_token():
    # A literal token would be "Bearer " + 20 or more token
    # characters. The heredoc pattern (secrets.token_urlsafe)
    # generates tokens, it never prints one; the client examples
    # carry a reference or a placeholder, never a value.
    assert not re.search(r"Bearer [A-Za-z0-9_\-]{20,}", ADD_AGENT)
    assert "Bearer ${ROUNDTABLE_TOKEN}" in ADD_AGENT
    assert "Bearer <" in ADD_AGENT


def test_add_agent_opencode_matches_current_docs():
    # AUDIT-M8 R1: the old section nested the server directly under
    # "mcp" and called the file opencode.json. Current docs
    # (opencode.ai/docs/mcp-servers, checked 2026-11-14) put servers
    # under "mcp.servers", the file is opencode.jsonc, and the global
    # route is "opencode mcp add --global". The {env:} substitution
    # and the "opencode mcp list" check were already right.
    assert '"mcp": {"servers": {"roundtable"' in ADD_AGENT
    assert '"mcp": {"roundtable"' not in ADD_AGENT
    assert "opencode.jsonc" in ADD_AGENT
    assert "opencode mcp add --global" in ADD_AGENT
    assert "opencode mcp list" in ADD_AGENT
    assert "{env:ROUNDTABLE_TOKEN}" in ADD_AGENT


def test_add_agent_deployment_shapes_and_quick_start():
    # AUDIT-M8 R2 (Owner's choice): the deployment-shapes +
    # local quick-start addendum is part of M8.
    assert "Deployment shapes" in ADD_AGENT
    assert "local laptop" in ADD_AGENT_FLAT
    assert "own VM" in ADD_AGENT_FLAT
    assert "hosted public" in ADD_AGENT_FLAT
    assert "agents.example.json" in ADD_AGENT
    assert "python -m roundtable" in ADD_AGENT


def test_deploy_part0_getting_connected():
    # Part 0 sits between Step 0 and Part 1: before any VM check,
    # the Owner must be connected (Tailscale + SSH) and know where
    # the VM is and which ssh to use.
    part0 = section(DEPLOY, "Part 0")
    assert "tailscale status" in flat(part0)
    assert "Logged out" in part0
    assert "tailscaled" in flat(part0)
    assert "VM_HOST" in part0
    assert "leader-token.sh" in part0
    assert "OpenSSH" in part0
    assert "bracketed" in flat(part0)


def test_connect_part0_getting_connected():
    # CONNECT.md Part 0 is the same "getting connected" material
    # for the app side: Git Bash entry, Tailscale, the VM host, and
    # the paste/ssh traps, before Step 1.
    part0 = section(CONNECT, "Part 0")
    assert "bash.exe" in part0  # Git Bash entry, full path
    assert "tailscale status" in flat(part0)
    assert "Logged out" in part0
    assert "VM_HOST" in part0
    assert "OpenSSH" in part0
    assert "bracketed" in flat(part0)


def test_worker_service_template():
    # The always-on worker unit: same shape as roundtable.service
    # (its own user, restart policy, memory cap so Hermes is safe),
    # running worker.py from the checkout with its env file.
    assert "worker.py" in WORKER_SERVICE
    assert "EnvironmentFile=/srv/roundtable/worker.env" in WORKER_SERVICE
    assert "User=roundtable" in WORKER_SERVICE
    assert "Restart=on-failure" in WORKER_SERVICE
    assert "MemoryMax=128M" in WORKER_SERVICE
    assert "OOMScoreAdjust=600" in WORKER_SERVICE
    assert "After=roundtable.service" in WORKER_SERVICE
    assert "Bearer" not in WORKER_SERVICE  # no token in the unit


def test_worker_env_example():
    # The worker env template: every variable worker.py needs,
    # placeholders only — no real token or API key in the file.
    assert "ROUNDTABLE_URL=" in WORKER_ENV
    assert "ROUNDTABLE_TOKEN=<" in WORKER_ENV
    assert "WORKER_MODEL_BASE_URL=" in WORKER_ENV
    assert "WORKER_MODEL_NAME=" in WORKER_ENV
    assert "WORKER_API_KEY=<" in WORKER_ENV
    assert "never commit" in WORKER_ENV.lower()
    assert "sk-" not in WORKER_ENV


def test_changelog_m8_records_the_choice():
    # The VM-vs-Owner-PC decision is recorded where the next
    # reader looks: the CHANGELOG M8 entry, with the reasoning,
    # the alternative, and the API-key placement rules.
    assert "## M8" in CHANGELOG
    assert "Owner PC" in CHANGELOG
    assert "never in the repo" in CHANGELOG
    assert "never in chat" in CHANGELOG


def test_m8_scope_is_docs_scripts_templates_only():
    # M8 changes no server behaviour; the CHANGELOG states the
    # scope, and the Auditor checks the diff. This test pins the
    # stated scope so a later edit cannot quietly widen it.
    assert "Docs, scripts and deploy templates only" in CHANGELOG


def test_readme_status_m8():
    assert "M8 in progress" in README
    assert "145 tests pass" in README
    assert "11 M8 onboarding" in README
