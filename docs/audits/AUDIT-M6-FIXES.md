# Re-audit — Roundtable M6 audit fixes

- Auditor: Claude
- Audited: bundle `roundtable-m6 v2.bundle` (`refs/heads/main` = `90d215a`) on top of the audited M6 (`4f15564`)
- Date: 2026-11-12

## Verdict: PASS — M6 is approved for deployment

**Audited commit for the update (`AUDITED_COMMIT`): `90d215ae0875befb026fda6451df6bcc625c500c`.**

F1 and F2 close the disk-flood path. N1 and N2 are stated in SPEC §7 and in the CHANGELOG. `docs/audits/AUDIT-M6.md` is identical to my report.

## What I checked

- Read the diff since `4f15564` (`board.py`, `server.py`, `config.py`, the env example, DEPLOY, SPEC, tests).
- Suite from a real `git clone -b main` of the bundle, in Ubuntu 24.04.5: **115 passed, 1 failed** in one full run. The failing test was `test_worker.py::test_never_prints_or_logs_the_api_key`. It then passed **5/5** when run alone, and the whole worker file passed (20/20). `worker.py` is unchanged since M2, and the leak assertions are deterministic. The failure was the test's 10-second wait under a slow container (the full run took 136 s), not a leak. Not blocking; see N4.
- An independent live probe (my own script, real server):

| Probe | Before (4f15564) | Now (90d215a) |
|---|---|---|
| Flood: 120 × 100 KB `post_message`, limit 60/min | rate-limited rows stored up to 64 KB each (1.77 MB for 27 rows) | 60 size-rejected rows: **2 KB each** (121 KB total); 60 rate-limited rows: **53 bytes each** (`{"omitted": "rate_limited", "original_bytes": 100042}`) |
| Cap `ROUNDTABLE_HISTORY_MAX_MB=1`, 60 legit `create_task` of ~23.7 KB | — | 44 rows stored in full (1,046,100 bytes, just under 1 MiB), then 16 rows `{"omitted": "history_cap", …}`. **0 tool errors**, all 60 tasks created, **one** "History cap reached" warning in the log |

Worst case now: one token flooding through nginx's 10 r/s writes about 2 KB a minute within its 60/min allowance plus about 55 bytes per extra call, roughly 0.2 GB/day of rows, and argument bytes stop at the cap (default 1 GB). That no longer threatens the shared disk in practice, and the documented `df -h /` check covers the rest.

## Notes (not blocking)

- **N3.** Audit rows themselves (time, agent, tool, error, marker) are still uncapped. A month-long flood would add a few GB. The `df -h /` check in "Updating to M6" is the guard; consider a simple disk alert later.
- **N4.** `test_never_prints_or_logs_the_api_key` waits only 10 s for the fake model call. Raise it to 30 s, as other slow-container waits were raised.
- **N5.** The cap counts `LENGTH(arguments)` (characters) from the database at start-up but adds bytes while running, so it is slightly approximate for non-ASCII history. That is fine for a soft cap.

## Next step

1. The Owner approves: bring `main` to `90d215a` (fast-forward now; once branch protection is on, through a pull request that the Owner merges), and add this report to `docs/audits/`.
2. Update HermesVM with "Updating to M6": back up first, set `ROUNDTABLE_HISTORY_MAX_MB` in `/srv/roundtable/roundtable.env` (default 1024 is fine), Step 46 to `90d215a`, restart Roundtable only, and check `df -h /`. Then reconnect both apps.
