#!/usr/bin/env bash
# Fetch the leader (claude) token from the VM into $ROUNDTABLE_TOKEN,
# with no display. Source this script, do not run it:
#
#   source scripts/leader-token.sh [vm-host]
#
# The token never appears on screen, in shell history, or in a file.
# Needs Windows OpenSSH (Git's ssh is refused by this VM) and the key
# ~/.ssh/azure-hermes. The default host is the VM's Tailscale address;
# pass another host as the first argument, or set $VM_HOST.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Source this script, do not run it:" >&2
    echo "  source scripts/leader-token.sh" >&2
    exit 1
fi

VM_HOST="${1:-${VM_HOST:-100.75.20.68}}"

read -r ROUNDTABLE_TOKEN < <(
    /c/Windows/System32/OpenSSH/ssh.exe -i ~/.ssh/azure-hermes \
        "azureuser@${VM_HOST}" \
        "sudo python3 -c \"import json; d={a['name']: a['token'] for a in json.load(open('/srv/roundtable/agents.json'))['agents']}; print(d['claude'])\""
)
export ROUNDTABLE_TOKEN

if [[ -z "${ROUNDTABLE_TOKEN}" ]]; then
    echo "Could not read the leader token from the VM." >&2
    return 1
fi
echo "Leader token loaded into \$ROUNDTABLE_TOKEN (not shown)."
