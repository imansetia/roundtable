#!/usr/bin/env bash
# Copy the worker (muse) token to the Windows clipboard, with no
# display. Run from Git Bash:
#
#   bash scripts/copy-worker-token.sh [vm-host]
#
# Then paste it into Muse's secure entry card (field "Kunci API"):
# the token only, Muse adds "Bearer" itself. Afterwards clear the
# clipboard history: Win+V, "Clear all".
# Needs Windows OpenSSH (Git's ssh is refused by this VM) and the key
# ~/.ssh/azure-hermes. The default host is the VM's Tailscale address;
# pass another host as the first argument, or set $VM_HOST.

set -euo pipefail

VM_HOST="${1:-${VM_HOST:-100.75.20.68}}"

/c/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes -i ~/.ssh/azure-hermes \
    "azureuser@${VM_HOST}" \
    "sudo python3 -c \"import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['muse'], end='')\"" \
    | clip.exe

echo "Worker token copied to the clipboard (not shown)."
echo "Paste it into Muse's secure card, then clear the clipboard history: Win+V, \"Clear all\"."
