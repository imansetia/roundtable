# Audit report — Roundtable M6 (complete activity history)

- Auditor: Claude
- Audited: bundle `roundtable-m6.bundle` (`refs/heads/main` = `4f15564`), commits `26c9ba1` (R1–R4), `4f15564` (M6) on top of `c848e63`
- Date: 2026-11-12

## Verdict: PASS WITH REQUIRED FIXES — do not deploy yet

R1–R4 are fixed correctly. The history works as specified: every submission is kept, every call is logged with its arguments and error, no token is stored, and an M5 database migrates cleanly. But the new call log can be used to **fill the VM's disk within about a day**, and that disk is shared with Hermes. Fix F1–F2, then re-audit.

## What I checked

- Read the full diff since `c848e63`.
- Suite from a real `git clone -b main` of the bundle, in Ubuntu 24.04.5: **113 passed**.
- An independent live probe. I built a database with the **M5 code**, started the **real M6 server** on it (rate limit 60/min) and drove it over MCP:

| Probe | Result |
|---|---|
| Migration of the M5 database | `audit_log` gains `arguments`, `error`; `submissions` created; all old rows kept |
| Two submissions on one task (the first containing a canary line) | Both kept, in order, in `get_task` `submissions`, under the untrusted-content warning |
| Forged `post_message` kind `review`; worker `cancel_task` | Rejected; audit rows hold the arguments and the error text |
| 100 KB message body | Rejected (board data is never truncated); audit row stores 65,515 bytes with `truncated: true, original_bytes: 100042` |
| Both tokens searched in the raw database and WAL bytes | **Not present** |
| Flood: 80 calls with a 100 KB body, in one session | 27 rate-limited, **80 audit rows**; the 27 rate-limited rows alone stored **1.77 MB** of arguments |

## Required fixes

| # | Finding | Evidence | Fix |
|---|---|---|---|
| F1 | **Rejected calls store the maximum payload, even when rate-limited.** A call refused by the rate limiter, or rejected for size, still writes up to 64 KB of arguments. nginx allows 10 requests/s per IP, and the app's 60/min limit does not stop the logging, so one token can write about **35 MB/min, roughly 50 GB/day**. The VM has 53 GB free on the disk that Hermes also uses. That breaks the Owner's rule that Roundtable must not disturb Hermes. | Probe flood above; `run_tool` calls `limiter.check()` inside the same `try` whose `finally` logs the arguments. | Rate-limited calls: log the row with **no arguments**, only a marker such as `{"omitted": "rate_limited", "original_bytes": N}`. Calls rejected for size: keep a short prefix (e.g. 2 KB) plus the marker, not 64 KB. Successful calls keep their full arguments (≤ 64 KB) as now. |
| F2 | **No ceiling on history growth.** Even within the rate limit, a misbehaving agent can add several GB a day of legitimate-size arguments, and nothing stops it before the disk is full. | Design; DEPLOY says only "megabytes per month". | Add a configurable cap, e.g. `ROUNDTABLE_HISTORY_MAX_MB` (default 1024). Above it, audit rows keep agent, tool, task, outcome and error, but store an `{"omitted": "history_cap"}` marker instead of arguments, and the server logs a warning once. Tools never fail because of the cap. Document the setting, a disk-space check (`df -h /` next to `du -h` of the database), and what to do when the cap is reached. Test both F1 and F2. |

## Recommended (not blocking)

- **N1.** Calls that the MCP SDK rejects before `run_tool` (missing or wrong-typed arguments) still leave no audit row. This is a pre-existing gap since M1. If the Owner's "everything" should cover these too, it needs an SDK-level hook; otherwise state the limit in SPEC §7.
- **N2.** Arguments are logged from each tool's own parameter list, so extra arguments an agent sends (e.g. `decision`) are dropped by the SDK and not recorded. State this in the CHANGELOG.

## Answers

- **Separate `submissions` table instead of a message kind:** accepted. It is the right call (20,000-char results, writer limited to `submit_result`, no second rebuild of `messages`).
- **Test-helper pipe fix** (server output to a log file instead of an unread pipe): accepted; it also explains the earlier "flaky" worker tests.

## Next step

Fix F1–F2 on top of `4f15564`, with tests, CHANGELOG "M6 audit fixes", the full suite, and a bundle (`refs/heads/main`). Do not push. After the re-audit, the VM is updated with "Updating to M6".
