# Audit report — Roundtable M1

- Auditor: Claude
- Audited: bundle `roundtable-m1.bundle`, commits `8ce02a4`, `c06c667`, `a3a53ab` on top of `9766f8f`
- Date: 2026-11-10

## Verdict: PASS WITH REQUIRED FIXES

M1 is solid work. The structure is clean (config / db / board / server), every SQL statement is parameterised, roles are enforced on the server, the claim is atomic, and the CHANGELOG is honest about deviations. Do not start M2 until the required fixes below are done and re-reviewed.

## What I checked

- Read every file in the bundle.
- Ran the test suite independently: **12 passed**.
- Wrote and ran extra probes against a live server for cases the tests do not cover (results below).
- Scanned the package for shell calls, outbound network, string-built SQL, and token logging: none found.
- Scanned the full git history for committed secrets: none found (only fake `tok-test-*` and `REPLACE-ME` values).

### Confirmed good (verified live, not only by reading)

- Identity is taken from each request's token, not from the MCP session. A worker token sent inside the leader's session is still treated as the worker (create_task refused), and the reverse also holds. No privilege escalation through session reuse.
- `.gitignore` now excludes `agents.json`, `*.db`, WAL/SHM files.

## Required fixes (block M2)

Each fix needs a test that fails before the fix and passes after.

| # | Finding | Evidence | Fix |
|---|---|---|---|
| R1 | **Duplicate tokens are accepted.** Two agents with the same token start fine; the first match always wins, so the second agent silently acts as the first. | Probe C: config with two agents sharing one token -> server started. | Reject duplicate tokens at startup in `load_agents_config`. Also reject tokens shorter than 32 characters. |
| R2 | **Non-ASCII bearer token returns 500 instead of 401.** `hmac.compare_digest` raises `TypeError` on non-ASCII `str`. | Probe B: `Authorization: Bearer éé` -> HTTP 500. | Compare as bytes (`token.encode()` vs stored bytes), or catch and return 401. Any malformed header must give 401. |
| R3 | **Agents removed from the config stay in the database and in `list_agents`.** If the Owner replaces the leader, the old leader can still be listed with its old role, so the board can show two leaders. | Probe D: after removing `planner` from config, `list_agents` still lists `planner`. | On startup, delete or mark inactive (`active = 0`) every agent not in the config, and only list active agents. Tasks claimed by a removed agent should go back to `open`. |
| R4 | **`required_skill` has no size limit.** A 50,000-character value was stored. | Probe E. | Cap at 100 characters, rejected not truncated. Same for the `assignee` filter in `list_tasks`. (This gap was in the spec; it is now part of the spec.) |
| R5 | **`list_tasks.total` ignores the filters.** | Probe G: `status="done"` returned 0 tasks but `total: 2`. | Return the count that matches the same filters. (Answers Q3.) |
| R6 | **Audit rows for `create_task` have no `task_id`.** | Probe F: `task_id` is `NULL`. | Log the new task's id on success. |
| R7 | **README quick start command is invalid**: `python3.11+ -m venv .venv`. | README. | Use `python3 -m venv .venv` and state "Python 3.11 or newer" in words. |

## Answers to the Builder's open questions

- **Q1 (reassign tool):** No reassign tool in v1. Cancel and recreate is the intended path. The words "or leader reassigns" in the spec diagram were a spec error; treat `claimed -> open` as worker release only.
- **Q2 (leader claims):** No. The leader never claims or executes. Keep the current rule.
- **Q3 (filtered total):** Filtered total. See R5.
- **Q4 (messages on final tasks):** Reject `post_message` on `done` and `cancelled` tasks with a clear error, and add a test.

## Accepted deviations

CHANGELOG items 1–6 and 8 are accepted as written. Item 7 is settled by Q1.

## Next step

Fix R1–R7 and Q4 on the same branch, update CHANGELOG under a new "M1 audit fixes" heading, run the full suite, and report back with test output. Do not start M2.
