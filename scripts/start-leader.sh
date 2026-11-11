#!/usr/bin/env bash
# Start the leader session with only the roundtable MCP server loaded,
# in manual permission mode. Run from Git Bash:
#
#   bash scripts/start-leader.sh
#
# Needs $ROUNDTABLE_TOKEN in this shell first:
#
#   source scripts/leader-token.sh
#
# The token is never on the command line: the config carries only the
# ${ROUNDTABLE_TOKEN} reference, which Claude Code expands itself.
# --strict-mcp-config ignores every other MCP configuration, and
# ENABLE_CLAUDEAI_MCP_SERVERS=false keeps the account's claude.ai
# connectors out of the session.

set -euo pipefail

if [[ -z "${ROUNDTABLE_TOKEN:-}" ]]; then
    echo "ROUNDTABLE_TOKEN is not set. Run first:" >&2
    echo "  source scripts/leader-token.sh" >&2
    exit 1
fi

mkdir -p ~/roundtable-leader
cd ~/roundtable-leader

ENABLE_CLAUDEAI_MCP_SERVERS=false claude --strict-mcp-config \
    --mcp-config '{"mcpServers":{"roundtable":{"type":"http","url":"https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp","headers":{"Authorization":"Bearer ${ROUNDTABLE_TOKEN}"}}}}' \
    --permission-mode default
