#!/usr/bin/env bash
# Roundtable local install (M10): the Shape A quick-start from
# docs/ADD-AGENT.md, as one script. From the repo root:
#   bash scripts/install-local.sh
#
# It creates .venv, installs the package, generates tokens in
# agents.json (never overwriting an existing one — the tokens are
# yours alone, never commit them), starts the board in the
# background, verifies it (no token -> 401, whoami with the
# leader token, GET /status -> 200), and leaves it running.
# Stop it with:  kill "$(cat roundtable.pid)"
#
# Loopback only — no TLS, no nginx. HTTPS stays a shape-B
# concern (docs/ADD-AGENT.md). Nothing here touches Hermes.

set -euo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || {
    echo "install-local: python3 is required (3.11 or newer)" >&2
    exit 1
}

# If a board is already answering on 127.0.0.1:8000, refuse to
# start a second one: two servers would fight over the port and
# the database. A board answers 401 to a token-less POST (its
# bearer gate); a connection failure means nothing is running.
if [ "$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d '{}' 2>/dev/null || echo 000)" = "401" ]; then
    echo "install-local: a board is already running on 127.0.0.1:8000" >&2
    echo "install-local: stop it first (kill \"\$(cat roundtable.pid)\") and re-run" >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo "install-local: creating .venv"
    python3 -m venv .venv
fi
echo "install-local: installing roundtable"
.venv/bin/pip install -e .

if [ ! -f agents.json ]; then
    cp agents.example.json agents.json
    # Tokens are generated inside Python, never typed and never
    # echoed: the same pattern as ADD-AGENT.md Step 2.
    .venv/bin/python - <<'PYEOF'
import json, secrets
path = "agents.json"
with open(path, encoding="utf-8") as f:
    agents = json.load(f)
for a in agents["agents"]:
    a["token"] = secrets.token_urlsafe(48)
with open(path, "w", encoding="utf-8") as f:
    json.dump(agents, f, indent=2)
print("tokens generated — agents.json is yours alone, never commit it")
PYEOF
    chmod 600 agents.json
else
    echo "install-local: keeping existing agents.json (tokens untouched)"
fi

echo "install-local: starting the board"
nohup .venv/bin/python -m roundtable > roundtable.log 2>&1 &
echo $! > roundtable.pid

# Wait until the server answers (up to 20 s): it answers 401 to
# a token-less POST once it is up.
deadline=$((SECONDS + 20))
while :; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d '{}' 2>/dev/null || echo 000)
    if [ "$code" = "401" ]; then
        break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "install-local: the board did not start in 20 s — see roundtable.log" >&2
        exit 1
    fi
    sleep 0.5
done

# Verify: no token -> 401 (the bearer gate works).
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -d '{}')
if [ "$code" != "401" ]; then
    echo "install-local: expected 401 without a token, got $code — see roundtable.log" >&2
    exit 1
fi

# Verify: whoami with the leader token (read inside Python, never
# printed), and the public status page.
.venv/bin/python - <<'PYEOF'
import asyncio, json
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    agents = json.load(open("agents.json", encoding="utf-8"))["agents"]
    leader = next(a for a in agents if a["role"] == "leader")
    url = "http://127.0.0.1:8000/mcp"
    headers = {"Authorization": f"Bearer {leader['token']}"}
    async with httpx.AsyncClient(headers=headers, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("whoami", {})
                print("whoami:", result.content[0].text)

asyncio.run(main())
PYEOF

code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/status)
if [ "$code" != "200" ]; then
    echo "install-local: expected 200 from /status, got $code — see roundtable.log" >&2
    exit 1
fi

echo "install-local: Roundtable is running on http://127.0.0.1:8000/mcp"
echo "install-local: public status page: http://127.0.0.1:8000/status"
echo "install-local: PID file: roundtable.pid — stop with: kill \"\$(cat roundtable.pid)\""
echo "install-local: log: roundtable.log"
