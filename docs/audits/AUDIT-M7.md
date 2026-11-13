# Audit report — PR #4 "M7: skills are enforced on the server"

- Auditor: ZCode (stand-in Auditor while Claude is unavailable until 2026-11-15)
- Audited: PR imansetia/roundtable#4, branch `m7-skills`, head `2f1a5fb7a1c991c36aa3c8c14d5ba29f8dc43f96` (one commit, parent `898b49d` = current `main`)
- Date: 2026-11-14

## Verdict: PASS WITH REQUIRED FIXES — do not merge yet

The M7 code is correct and well tested: the server enforces skills, they
cannot be bypassed through arguments, the migration of a real M6 database
keeps every row, and the full suite passes on the exact head. But one
rollback trap is undocumented: **M6 code cannot run on an M7-migrated
database** (probe below), and the branch adds no DEPLOY.md "Updating to
M7" section, so the beginner Owner has nowhere to learn that a rollback
(Step 47) must be combined with a backup restore (Step 44). R1 is a
docs-and-test fix; no code change is requested. Fix R1, push to the same
branch, and report the new head hash.

## What I checked

- Fetched `m7-skills` from GitHub. Head `2f1a5fb` matches the reported
  hash exactly, its parent is `main` (`898b49d`), and PR #4 is open and
  not merged (GitHub API).
- Read the whole diff (`board.py`, `db.py`, `server.py`, `worker.py`,
  `test_m7_skills.py` (new), `test_acceptance.py`, `test_worker.py`,
  `test_m6_activity_history.py`, SPEC, README, CHANGELOG).
- Full suite on the exact head, from a real `git clone` at `2f1a5fb`, in
  WSL Ubuntu 24.04 (Python 3.12.3, the VM's version): **133 passed** in
  ~134 s. (Windows cannot run this suite: 8 failures + 6 errors there are
  environment issues — signal and live-server helpers — the same suite
  passes fully in Ubuntu.)
- An independent live probe (my own script, not Muse's tests). Phase 1
  built a **real database with the M6 code** (a real M6 server over MCP:
  claim → message → submit → reject → re-claim → resubmit, so messages,
  two submissions and a review message existed). Phase 2 started the real
  M7 server on that same database file:

| Probe | Result |
|---|---|
| Migration | `required_skill` → `required_skills`; set skill becomes `["python"]`, unset becomes `[]`; both forms returned by `get_task` |
| History kept | every old audit row byte-identical; both messages; both submissions in order; `integrity_check` ok |
| Enforcement | worker lacking a skill rejected, error names it, task stays open; a partially-matching worker rejected on a multi-skill task; fully-matching worker accepted; migrated task enforced like new ones |
| Bypass attempts | extra `skills` / `required_skills` arguments on `claim_task` rejected (SDK drops them; skills come only from the token); worker `create_task` rejected |
| `claimable_by_me` | leader rejected with a clear error; worker sees exactly the open, unassigned, fully-matched tasks; `false` behaves as before |
| Limits | 11 skills rejected, 10 accepted; 101-char skill rejected, 100 accepted; both spellings rejected; non-list and non-string-element rejected; duplicates and empty strings dropped; warning (not error) when no worker matches; direct assignee lacking a skill rejected, holding all accepted |
| Legacy | old `required_skill` argument still works end to end |
| Audit log | rows record `required_skills` and `claimable_by_me`; no token bytes in the database or WAL |
| Rollback probe | **M6 code (`898b49d`) started on the migrated database: `create_task` and `list_tasks` both fail** (`tasks` has no `required_skill` column) |

- Verified the README test counts against the suite: 133 = 34 (21
  acceptance + 13 audit fixes) + 21 worker + 3 allowed-hosts + 23
  deploy-config + 30 M4 docs + 5 M5 + 8 M6 + 9 M7. Correct.
- Confirmed only the two known `tasks` indexes existed before the rebuild
  and both are recreated (nothing else is dropped); the migration test
  also covers a leftover `tasks_new` table from a crashed run.

## Required fix

| # | Finding | Evidence | Fix |
|---|---|---|---|
| R1 | **The M7 rollback trap is undocumented.** M5 and M6 each added a DEPLOY.md "Updating to M…" section (back up first, Step 46, reconnect apps, and the rollback note). M7 adds none; the CHANGELOG records this as a judgement call. But M7's migration is the one migration after which the previous code cannot run: M6 code fails on a migrated database, so Step 47 (rollback to the previous commit) alone leaves the board broken — `create_task` and `list_tasks` error on every call. The M5 section even says "M4 code runs fine on a migrated table" — the opposite is true for M7, and nothing tells the Owner. | Rollback probe above (M6 code on the migrated snapshot: both tools fail). DEPLOY.md diff in this PR: empty. | Add a DEPLOY.md "Updating to M7" section: back up first (Step 43), record the version (Step 45), Step 46 with the audited commit, verify (Steps 24 and 41), reconnect both apps — and state plainly: **if the service fails or you roll back (Step 47), you must also restore the backup (Step 44), because M6 code cannot run on a migrated database.** Add the guard test in `tests/test_deploy_config.py` (the same pattern the M5 and M6 sections have). |

## Notes (not blocking)

- The `create_task` result gains an optional `warning` key and carries
  `required_skills` next to the legacy `required_skill`. Additive only:
  an existing client that reads the old fields still works (probe:
  legacy argument works end to end). The change is documented in the
  CHANGELOG.
- `claimable_by_me` filters in Python over every row matching the SQL
  filters (no `LIMIT` on the inner fetch). Fine at this board's scale
  (rows are small, paging applies to the filtered list); worth revisiting
  only if a board ever grows to tens of thousands of open tasks.
- Muse's statement that all 16 new tests were run against the M6 code
  first and failed there was not independently re-verified (it would
  need a second full run on M6 with the new tests). The suite result at
  the audited head and the independent probes above are the evidence
  this verdict rests on.
- The commit is again authored by the Owner's GitHub account (Muse's
  connector acts as the Owner) — the same situation as AUDIT-PR3-M6-NOTES
  N8. The reminder stands: branch protection must have "Do not allow
  bypassing the above settings" enabled, or the Owner's own admin rights
  can skip the pull request.
- Probes used only fake test tokens (the repo's own test pattern); no
  real token was requested, printed or stored.

## Next step

1. Muse fixes R1 on `m7-skills` (one commit: DEPLOY.md section + guard
   test), and reports the new head hash.
2. The Auditor re-checks R1 and re-runs the suite on the new head
   (expected quick).
3. The Owner merges PR #4 with **"Create a merge commit"**, then deploys
   following the new "Updating to M7" section — back up first.
