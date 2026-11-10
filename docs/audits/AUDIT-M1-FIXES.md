# Re-audit — Roundtable M1 audit fixes

- Auditor: Claude
- Audited: bundle `roundtable-m1-audit-fixes.bundle`, commits `c921705`, `a3bdedb` on top of the audited M1 (`a3a53ab`)
- Date: 2026-11-10

## Verdict: PASS — M1 is approved

All required fixes R1–R7 and the Q4 decision are done correctly. M1 can be merged into `main`. M2 may start when the Owner says so.

## What I checked

- Read the full diff since `a3a53ab` and the new test file.
- Ran the suite independently: **24 passed**.
- Re-ran my own live probes from the first audit. Every finding is now closed:

| # | Before | Now |
|---|---|---|
| R1 | Duplicate tokens accepted | Server refuses to start |
| R2 | Non-ASCII token -> 500 | 401 |
| R3 | Removed agents still listed | Only active agents listed; their claimed tasks reopen |
| R4 | 50,000-char `required_skill` stored | Rejected over 100 chars |
| R5 | `total: 2` for 0 matching tasks | `total: 0` |
| R6 | Audit `task_id` NULL on create | New task id recorded |
| R7 | Invalid README command | Fixed |
| Q4 | Messages allowed on final tasks | Rejected on `done` / `cancelled` |

- The identity checks from the first audit still hold: a token used inside another agent's session still acts as its own agent.
- Good practice noted: fixes were written test-first, and the M1 database migration has its own test.

## Follow-up (not blocking; do it as the first commit of M2)

**F1. A task pre-assigned to a removed worker gets stuck.** If the leader creates a task with `assignee="coder"` and the Owner then removes `coder` from the config, the task stays `open` with `assignee: coder`, and no other worker can claim it. Verified live. The leader can work around it with cancel + recreate, so nothing is lost, but on startup the server should also clear the assignee of `open` tasks assigned to inactive agents. Add a test.

**Note (no change needed).** R3 builds its `NOT IN (?, ?, ...)` clause with an f-string. Only `?` markers are inserted and every value is still a parameter, so this meets spec 7.4. Keep this pattern the only exception.
