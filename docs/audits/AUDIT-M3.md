# Audit report — Roundtable M3

- Auditor: Claude
- Audited: bundle `roundtable-m3.bundle`, commits `20bf593`, `0b2cffc`, `0e7054e`, `471ab46` on top of `0399ab6`
- Date: 2026-11-10

## Verdict: PASS WITH REQUIRED FIXES — do not run the guide yet

The allowed-hosts change is correct and well tested, the service unit is well hardened, and the guide is careful, beginner-friendly and never touches Hermes. However, three defects would break the deployment or take it down later: the certificate renewal never reloads nginx, step 30 does not load the new nginx site, and an authenticated request through nginx is never checked. Fix D1–D8 below before the Owner runs any step on HermesVM.

## What I checked

- Verified the bundle (complete history, builds on `main` = `0399ab6`) and read the full diff, all of `docs/DEPLOY.md` and every file in `deploy/`.
- Read the MCP SDK 2.3.0 `transport_security.py` to confirm how allowed hosts are matched.
- Ran the full suite in a clean Ubuntu 24.04.5 container (Python 3.12.3, the VM's versions): **46 passed, 2 failed**. The 2 failures are timing-sensitive M2 worker tests, unchanged in M3, that passed 3/3 when re-run (see Notes). `tests/test_allowed_hosts.py`: 3 passed.
- Installed nginx 1.24.0 (the VM's version) in that container and ran `nginx -t` on both site files with the real public name; then ran the real server behind the phase-2 site and probed it end to end. Both `nginx -t` runs passed. Through nginx over HTTPS: no token → 401; leader token → **400** (allowed hosts work through the proxy); token sent to the bare IP with `Host: evil.example.com` → 421; other path → 404; HTTP → 301; 300 KB body → 413.
- Compared the guide with the read-only inspection of HermesVM done on 2026-11-10.

## Confirmed good

- `ROUNDTABLE_ALLOWED_HOSTS` adds to the loopback list and keeps protection on; a bare `*` and wildcard hostnames are rejected. In SDK 2.3.0 a `:*` entry only matches `<host>:<port>` by prefix, so even the accepted value `*:*` matches nothing real. The Host check runs after the bearer check, so unauthenticated requests still get 401.
- The service unit: dedicated user, `ProtectSystem=strict` with one writable path, empty capability set, syscall filter, `MemoryMax=200M`, `OOMScoreAdjust=500`, `UMask=0077`.
- Secrets: tokens generated inside Python straight into a 600 file outside the checkout; the EnvironmentFile is 600; nothing secret in the unit or the repo.
- Firewall order: NSG and ufw open only 80/443; the SSH-over-Tailscale rule is checked before and after and never modified; port 22 and 8000 never opened.
- The two packaging fixes found in Muse's live run (venv owned by the operator; explicit `packages` in `pyproject.toml`).
- An empty `$PUBLIC_NAME` fails safe: the server refuses to start with an empty allowed-hosts list, and nginx rejects an empty `server_name`.

## Required fixes (block running the guide)

| # | Finding | Evidence | Fix |
|---|---|---|---|
| D1 | **Renewed certificates are never loaded.** `certbot certonly --webroot` has no installer, so `certbot renew` writes the new files but nginx keeps serving the old certificate from memory. About 90 days after install the site starts failing with an expired certificate. | Step 35 has no deploy hook; step 41's dry run does not detect this. | Add `--deploy-hook "systemctl reload nginx"` to step 35 (certbot stores it in the renewal config), and make step 41 check it with `sudo grep -r deploy_hook /etc/letsencrypt/renewal/` or the `renewal-hooks/deploy/` directory. |
| D2 | **Step 30 does not load the phase-1 site.** On Ubuntu, `apt install nginx` starts nginx straight away with the default site. Step 30's `systemctl enable --now nginx` does nothing to a running nginx, so the default site stays loaded and step 31 gets the welcome page (200) instead of 301. A beginner will stop there. Step 11's text "Nothing is started or configured yet" is also wrong. | Debian/Ubuntu nginx package behaviour. Muse could not install nginx, so this path was never run. | Step 11: say nginx starts at once on port 80 (still blocked by ufw and the NSG at that point). Step 30: `sudo nginx -t && sudo systemctl reload nginx`, then `systemctl is-enabled nginx`. Step 3: check ports before step 11 only, and say so. |
| D3 | **The guide never checks an authenticated request through nginx.** Step 40 and verification item 7 only send requests without a token, which get 401 before the Host check runs. A wrong `ROUNDTABLE_ALLOWED_HOSTS` would therefore pass every check and only show up in M4 as 421. | Steps 25, 40 and Part 11. | Add a step after 40 (and a verification item): read the leader token as in step 25, send it to `https://$PUBLIC_NAME/mcp`, and expect **400, not 421**. |
| D4 | **Azure's 4-minute idle timeout cuts streams silently.** The public IP drops idle TCP flows after 4 minutes without a reset, but `proxy_read_timeout` is 300 s. An idle MCP stream is therefore cut by Azure first, and the client is left on a dead connection instead of seeing a clean close. | Public IP configuration: idle timeout 4 min. `deploy/nginx/roundtable.conf` lines 92–93. | Set `proxy_read_timeout` and `proxy_send_timeout` below 240 s (for example 180 s), explain why in the comment, and document in the guide that MCP clients reconnect the stream after a clean close. |
| D5 | **The restore can corrupt the database.** Step 43 copies the backup over `roundtable.db` but leaves any `roundtable.db-wal` / `-shm` files in place. After a crash (the case the step is for), SQLite would replay the stale WAL onto the restored file. | Step 43. | After `systemctl stop`, move the live `.db`, `.db-wal` and `.db-shm` aside (do not delete them), then copy the backup in as the `roundtable` user. |
| D6 | **The step 11 undo can break Hermes.** `apt remove git sqlite3 python3-venv` would also remove packages that may have been installed before Roundtable and may be used by Hermes or the system. | Step 11 undo; step 6 only checks nginx and certbot. | Step 6: record which of the five packages are already installed. Undo removes only the packages this guide installed, never `git` or `python3-venv` if they were already there. |
| D7 | **Updates deploy unaudited code.** Step 45 runs `git pull` on `main` and goes live with whatever is there; the guide never says which commit was audited. | Steps 16 and 45. | Check out a named, audited commit or tag (the Auditor gives it after approval) in step 16 and in step 45, instead of following `main`. |
| D8 | **Expected output that a beginner will read as failure.** Step 3 says "no output at all", but `ss` always prints a header line. Part 11 item 2 runs `ss -tlnp` without sudo, so the process names (`python`, `nginx`, `hermes`) it expects are not shown. Step 20 says an empty `PUBLIC_NAME` leaves the placeholder; it actually leaves the value empty. | Steps 3, 20; Part 11. | Use `ss -Htln` (no header) in step 3 and `sudo ss -tlnp` wherever process names are expected. In step 0, add `test -n "$PUBLIC_NAME" && test -n "$EMAIL" && echo ok`. Correct the step 20 text. |

## Recommended (not blocking)

- **R1.** The nginx comment "`$host` is nginx's own server name … a client could not forge" is wrong. `$host` is the request's Host header when present. Because this is the only TLS server, any name (or the bare IP) reaches it; the app's 421 still protects it. Fix the comment. Optionally add a catch-all `default_server` on 443 with `ssl_reject_handshake on;` (supported by nginx 1.24) and one on 80 with `return 444;`, so scanners hitting the IP get nothing.
- **R2.** Add `MemorySwapMax=0` (or a small value) to the unit. `MemoryMax` does not cover swap, and this VM is already swapping, so Roundtable could push Hermes's pages out.
- **R3.** Steps 25/40 put the token on curl's command line, where `ps` can show it to other local users for a moment. Pass the header on stdin instead: `printf 'Authorization: Bearer %s\n' "$TOKEN" | curl -H @- ...`.
- **R4.** Lowercase `ROUNDTABLE_ALLOWED_HOSTS` entries when parsing. nginx sends `$host` in lowercase, so an uppercase entry would cause 421.
- **R5.** Uninstall: offer to restore the `default` nginx site link and to remove the nginx/certbot packages that this guide installed.

## Answers to the Builder's open questions

- **DNS label and region:** `roundtable-k7q2.eastasia.cloudapp.azure.com`, already set by the Owner (A record → 20.255.51.158, static IP). Fill it in as the example in step 0, but keep the steps generic.
- **Backups on a timer or manual:** manual for now. A timer can come after M4, once there is board data worth protecting.
- **Hermes health check:** PID, listening on 127.0.0.1:51909, and memory are enough for M3. On the day of the run, the Auditor will also check Hermes's own user-level unit (`systemctl --user status` as `azureuser`) before and after.
- **N2:** unchanged; still for before 1.0.
- **`worker.py` not deployed:** correct; that was the Owner's decision.

## Accepted deviations

The two-file nginx site and the extra paths (`docs/DEPLOY.md`, `deploy/`) not listed in SPEC §11 are accepted.

## Notes

- **pip:** reinstalling from the local checkout with an unchanged version number does replace the package ("Successfully uninstalled roundtable-0.1.0 … Successfully installed"), so steps 45 and 46 need no extra flags.
- **Flaky timing in two M2 worker tests:** in one full run in the container, `test_failed_task_cools_down_then_retries` and `test_exits_cleanly_on_ctrl_c_when_idle` failed. Run on their own they then passed 3 times out of 3. `worker.py` and `tests/test_worker.py` are unchanged in M3. Not blocking. If it happens again, widen their timing margins.
- **Local Windows runs:** these server tests cannot reach localhost on the Auditor's Windows machine (connect timeout, a local environment limit), so every run in this audit was done in Ubuntu 24.04.

## Next step

Fix D1–D8 on top of `471ab46`, update the CHANGELOG under "M3 audit fixes", run the full suite, and run `nginx -t` on both site files if you can. Report back with a bundle. The Owner runs nothing on HermesVM until the re-audit passes; then the Auditor runs the guide with the Owner step by step, followed by Part 11.
