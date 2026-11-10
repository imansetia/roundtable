# Re-audit — Roundtable M3 audit fixes

- Auditor: Claude
- Audited: bundle `roundtable-m3-audit-fixes.bundle`, commits `ef72f31`, `42106be` on top of the audited M3 (`471ab46`)
- Date: 2026-11-11

## Verdict: PASS — M3 is approved

D1–D8 are fixed correctly, and R1–R5 were all taken. The guide may now be run on HermesVM, by the Owner with the Auditor, step by step.

**Audited commit for `AUDITED_COMMIT` (Step 0): `42106be76b86f60bee6f29b2e4c05344002a50a2`.** It must be on GitHub before Step 16. Fast-forward `main` to it (it builds directly on `0399ab6`) and push.

## What I checked

- Verified the bundle (complete history, on top of `471ab46`) and read the full diff. `docs/audits/AUDIT-M3.md` is identical to my report.
- Ran the suite in a clean Ubuntu 24.04.5 container (Python 3.12.3): **61 passed**. The two timing-sensitive M2 worker tests passed this time.
- Ran `nginx -t` with nginx 1.24.0 (the VM's version) on both site files, filled in with the real public name: **both pass**, with no placeholder left.
- Ran the real server behind the phase-2 site, with `ROUNDTABLE_ALLOWED_HOSTS` written in mixed case (to test R4), and probed it over HTTPS:

| Probe | Result |
|---|---|
| No token | 401 |
| Leader token, header passed on stdin (`curl -H @-`) | **400** (not 421) |
| Other path | 404 |
| HTTP | 301 to HTTPS |
| ACME challenge path on port 80 | 404, served and not redirected |
| Bare IP on port 80 | connection closed, no response (444) |
| Bare IP / wrong SNI on 443 | TLS handshake rejected (curl exit 35) |
| 300 KB body | 413 |
| Proxy timeouts | 180 s, below Azure's 240 s |

- Ran the restore move-aside loop: the `.db` and `.db-wal` files are moved to `.before-restore`, and a missing `-shm` is skipped.

| # | Fix | Status |
|---|---|---|
| D1 | `--deploy-hook "systemctl reload nginx"` at issue time, checked in Step 42 and Part 11 | Fixed |
| D2 | Step 11 says nginx starts at once; Step 30 runs `nginx -t` then reload | Fixed |
| D3 | Step 41: authenticated request through nginx, expects 400 not 421 | Fixed, verified live |
| D4 | Timeouts 180 s, with a comment explaining why | Fixed |
| D5 | `.db`, `-wal` and `-shm` moved aside before restore, never deleted | Fixed, verified |
| D6 | Package record in Step 6; undo removes only new packages | Fixed |
| D7 | `$AUDITED_COMMIT` checked out in Steps 16 and 46; no `git pull` | Fixed |
| D8 | `ss -Htln`, `sudo ss -tlnp`, `test -n` guard, Step 20 text | Fixed |
| R1–R5 | Catch-all servers, `MemorySwapMax=0`, token on stdin, lowercase hosts, fuller uninstall | All taken, R1 and R4 verified live |

## Follow-ups (not blocking; first commit of M4)

- **F1. Part 11 "From outside the VM" item 8 cannot run as written.** It reads `/srv/roundtable/agents.json` with `sudo`, a file that exists only on the VM, and the Owner's computer is Windows. There, PowerShell 5.1 treats `curl` as an alias for `Invoke-WebRequest`, so the `-w`, `-s` and `-o` flags in items 7–12 fail too. On the day of the run, the Auditor will run item 8 on the VM (it is the same check as Step 41) and items 7 and 9–12 with `curl.exe` / Git Bash. For M4: drop item 8 from the outside list (point to Step 41 instead), and say that the outside checks need a POSIX shell or `curl.exe` on Windows.
- **F2.** The restore loop in Step 44 exits with status 1 when `-shm` does not exist. The next command still runs, because the lines are separate, so nothing breaks. Append `; true` or use `if` for a clean exit code.

## Next step

1. The Owner approves: fast-forward `main` to `42106be` and push (the Auditor can do this with the Owner's approval), then add this report to `docs/audits/`.
2. Run the guide on HermesVM: the Owner and Auditor together, one step at a time, each command approved by the Owner. Record Hermes's PID and its user-level unit status first.
3. After Part 11 passes, M3 is complete and M4 may start.
