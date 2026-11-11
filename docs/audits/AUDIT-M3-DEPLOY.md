# Deployment record — Roundtable M3 on HermesVM

- Auditor: Claude, with the Owner approving every command
- Deployed: commit `42106be76b86f60bee6f29b2e4c05344002a50a2` (audited in AUDIT-M3-FIXES.md)
- Date: 2026-11-11 (UTC)
- Public endpoint: `https://roundtable-k7q2.eastasia.cloudapp.azure.com/mcp`

## Verdict: DEPLOYED — M3 is complete

All 48 steps of `docs/DEPLOY.md` were run in order over SSH via Tailscale, and every check in Part 11 passed. Hermes was never stopped or restarted. M4 may start.

## Before and after

| | Before (Part 1) | After (Part 11) |
|---|---|---|
| Hermes | PID 342935, `hermes-gateway.service` (user unit) active, 127.0.0.1:51909 | **same PID 342935**, active, same port |
| Available RAM | 285 MiB | 278 MiB |
| Listening publicly | nothing (ufw allowed SSH on tailscale0 only) | nginx on 80/443 only |
| ufw | 22/tcp on tailscale0 | unchanged, plus 80/tcp and 443/tcp (v4 and v6) |
| fail2ban, unattended-upgrades | active | active |

## Part 11 results

On the VM:
- `roundtable.service` active; ~55 MiB (cgroup) / 64 MiB RSS; `MemoryMax=200M`, `MemorySwapMax=0`, `OOMScoreAdjust=500`. nginx: 3 processes at about 9 MiB each.
- App on 127.0.0.1:8000 only; nginx on 0.0.0.0/[::] ports 80 and 443; Hermes on 127.0.0.1:51909.
- Certificate from Let's Encrypt (YE1), valid until 2027-02-09. `certbot.timer` active, `renew_hook = systemctl reload nginx` stored, `certbot renew --dry-run` succeeded.
- First backup `roundtable-2026-11-11.db`, mode 600, owner `roundtable`, `PRAGMA integrity_check` = ok.

From outside (the Owner's computer, over the internet):

| Probe | Result |
|---|---|
| `/mcp` without token | 401 |
| `/mcp` with leader token (from the VM, via the public name: Step 41) | 400, not 421 |
| `/` and other paths | 404 |
| HTTP | 301 to HTTPS |
| Port 8000 | timeout |
| Bare IP on 80 / 443 | connection closed / handshake rejected |
| Port 22 | closed |
| HSTS | `max-age=63072000; includeSubDomains` |
| TLS 1.1 | refused |

## Deviations during the run

- **Email:** the Owner chose to register the Let's Encrypt account with an email address, as the guide says, and approved the Subscriber Agreement (`--agree-tos`). `--non-interactive` was added so certbot could not wait on a prompt.
- **Step 31** was run against the public name (`curl --resolve`) as well as against `127.0.0.1` (see F3).
- **`apt install` also upgraded `libsqlite3-0`** to Ubuntu's current security patch (3.45.1-1ubuntu2.9), because the new `sqlite3` package needs it. The running Hermes process was unaffected; on its next restart it will load the patched library, which has the same upstream version.
- Commands were sent as LF scripts through Windows OpenSSH, because a PowerShell pipe adds a BOM and a trailing CR.

## Follow-ups for the first commit of M4 (none block M3)

- **F1** (from AUDIT-M3-FIXES): Part 11 outside item 8 reads `/srv/roundtable/agents.json`, which exists only on the VM. Point it to Step 41 instead, and say the outside checks need a POSIX shell or `curl.exe` on Windows.
- **F2** (from AUDIT-M3-FIXES): Step 44's move-aside loop exits with status 1 when `-shm` is absent.
- **F3:** Step 31 tests `http://127.0.0.1/`, which the R1 catch-all now rejects (000). Test with `--resolve "$PUBLIC_NAME:80:127.0.0.1" "http://$PUBLIC_NAME/"`, and expect 301 for `/` and 404 for the ACME path. The Auditor missed this in the re-audit.
- **F4:** `systemctl reload nginx` returns before the new workers serve. A check run immediately afterwards hit the old configuration and got 200. Add `sleep 2` after every reload that a check follows (Steps 30 and 39, the uninstall step).
- **F5:** certbot stores `--deploy-hook` as **`renew_hook`** in the renewal config, so `grep deploy_hook` (Step 42, Part 11 item 6, and `tests/test_deploy_config.py`) finds nothing even though the hook is present. Grep for `renew_hook`. This originated in the Auditor's own D1 wording.
- **F6:** Step 23 expects a log line "Roundtable M1 listening on …". The journal shows Uvicorn's "Uvicorn running on http://127.0.0.1:8000" instead. Update the expected text, or make the app log its own line (and drop "M1" from it).

## Notes for the Owner

- The two agent tokens (`planner` = leader, `coder` = worker) are in `/srv/roundtable/agents.json` on the VM. They are needed only in M4; take them out one at a time with the Auditor's guidance, and never paste them into a chat.
- Back up the database before any change (Step 43). Backups stay manual for now.
