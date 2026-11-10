# Re-audit — Roundtable M2 audit fixes

- Auditor: Claude
- Audited: bundle `roundtable-m2-audit-fixes.bundle`, commits `0da6bc0`, `0136dc7`, `15bf958` on top of `b2c4348`
- Date: 2026-11-10

## Verdict: PASS — M2 is approved

W1–W5 are fixed correctly, and the recommended log sanitizing was done as well. M2 can be merged into `main`. M3 may start when the Owner says so.

## What I checked

- Read the full diff since `b2c4348`.
- Ran the suite independently: **45 passed**.
- Confirmed the three files in `docs/audits/` are byte-identical to the reports I issued.
- Re-ran my live probes (real server + fake model + real `worker.py` process):

| # | Before | Now |
|---|---|---|
| W1 | 18 model calls in 10 s with cap = 1/hour | **1** model call |
| W2 | Task stuck `claimed` after kill -9 and restart | Released on restart, then claimed again and submitted |
| W3 | Matching task behind 55 others never claimed | Claimed and submitted |
| W4 | Any `http://` URL accepted | `http://api.deepseek.com` rejected; `https://` and `http://127.0.0.1` accepted; look-alikes such as `http://localhost.evil.com` and `http://127.0.0.1.nip.io` rejected |
| W5 | `httpx` dev-only | Main dependency |

## Notes for later (not blocking)

- **N1. Never run two worker processes with the same token.** On startup a worker releases every task claimed under its name (W2), so a second process with the same token would release the first one's task mid-work. State this in the README's worker section during M3.
- **N2.** `WORKER_MAX_TASKS_PER_HOUR` now counts model calls, not tasks. The README and `.env.example` explain it, so no rename is needed now; consider `WORKER_MAX_MODEL_CALLS_PER_HOUR` before a 1.0 release.
- **N3.** Failure counts and cool-downs live in memory, so a restart allows up to 3 more attempts per task. These are still bounded by the hourly cap. Accepted.

## Next step

M3 (deployment guide) needs the Auditor to review the real server: firewall, HTTPS, service setup. That review should happen with direct access to the VM.
