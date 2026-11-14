# Audit report — PR #8 "M9: operations & data safety"

- Auditor: ZCode (stand-in Auditor; Claude returns 2026-11-15 and will co-sign)
- Audited: PR imansetia/roundtable#8, branch `m9-operations`, head `5052fb41a54f5d3df005996b7b0fb8e0541a1dae` (one commit, parent `b0835b4` = current `main`)
- Date: 2026-11-14
- Status at audit time: PR open, not merged.

## Verdict: PASS — approved for merge; the timer installs on the VM separately when the Owner wants it

The dangerous part of this milestone — a script that deletes files — is
built with the right discipline: the disk guard runs before anything,
the backup must succeed before retention runs, deletion only ever
touches files matching the script's own date-named pattern, and
anything it cannot date is kept and warned about. Every claim checks
out, including running the real script against real temporary
databases in the tests. No redeploy is needed — no server file changed;
the timer installs on the VM separately (DEPLOY.md, "Backups now run
automatically").

## What I checked

- Fetched `m9-operations`: head `5052fb4` matches the reported hash,
  its parent is the current `main` (`b0835b4`), PR #8 is open and not
  merged (GitHub API). Whole diff read: 9 files, 585 insertions —
  **no file under `roundtable/`, no `worker.py` change**.
- **`deploy/roundtable-backup.sh` read line by line** (this script
  deletes files, so it got the closest look):
  - Order is fail-safe: disk guard → database readable check →
    `sqlite3 .backup` (DEPLOY Step 43's command) → retention. With
    `set -euo pipefail`, retention can only run after a successful
    backup; on any error nothing is deleted and stderr (→ journal)
    says why.
  - Disk guard first: `df --output=avail -B1 /`, below
    `MIN_FREE_BYTES` (default 5 GiB, the M6 "act now" threshold) it
    exits 0 with a loud warning — skip, delete nothing.
  - Retention deletes **only** files whose basename matches
    `roundtable-YYYY-MM-DD.db` exactly. A foreign file (e.g.
    `notes.txt`), a Step 44 `.before-restore` file, and a file with an
    unparseable date (`roundtable-2026-13-45.db`) are never deleted —
    the unparseable one is kept with a warning ("never delete what we
    cannot date"). Files with suffixes (e.g. `roundtable-2026-11-13-
    pre-migration.db`) do not match the pattern and are therefore
    naturally immune to pruning — a useful property worth knowing.
  - Retention implements exactly what the CHANGELOG records: newest
    14 daily files, plus the newest file of each of the last 4 ISO
    weeks (`%G-%V`), everything older deleted; fewer files than that
    → all kept.
- **Units**: `roundtable-backup.timer` (`OnCalendar=daily`,
  `Persistent=true`, `Unit=` pointing at the service, installable) and
  `roundtable-backup.service` (oneshot, no `[Install]` section with the
  reason stated — so it never also runs at boot, on top of the timer).
  Full M3-grade hardening with `ReadWritePaths=/srv/roundtable/backups`
  as the only writable path (reads of the database work read-only
  under `ProtectSystem=strict`, and the process runs as `roundtable`,
  which owns the mode-700 data directory). `MemoryMax=128M`,
  `MemorySwapMax=0`, `OOMScoreAdjust=600` — same correct ordering as
  the worker (backup job dies before the board, before Hermes).
- **tests/test_m9_backup.py** — 12 tests; the backup script is *run
  for real* against temporary databases: creation (mode 600, a usable
  copy), retention (14 daily + 4 weekly), disk-guard skip, fail-safe
  on a missing database, foreign files left alone, plus doc/template
  guards. The Builder's test-first claim was not independently
  re-verified (standing note, as in M7/M8).
- **DEPLOY.md** — the "Backups now run automatically" section covers
  install (including `chmod 750` for the GitHub exec-bit issue, the
  known N7 problem), a test-it-now step, restore (Step 44 unchanged),
  and disabling. Step 43's "no automatic schedule yet" sentence now
  points at the timer. CHANGELOG records the retention decision as an
  Owner decision; SPEC gains the M9 milestone line; README counts 157
  (34+21+3+24+30+5+8+9+11+12) — verified against the suite.
- **Full suite on the exact head**, fresh clone from GitHub at
  `5052fb4`, WSL Ubuntu 24.04 (Python 3.12.3, the VM's version):
  first run 3 failed — all three with `sqlite3: command not found`,
  because the M9 tests run the real script and the **`sqlite3` CLI was
  not installed in the audit environment** (it is a system package, not
  a Python dependency; the VM has it from DEPLOY.md Step 11). After
  installing it, **157 passed** in ~123 s, no flakes. Environment
  issue, not a code issue.

## Notes (not blocking)

- **Manual Step 43 backups share retention.** A manual backup and the
  daily timer write the same filename for the same day
  (`roundtable-YYYY-MM-DD.db` — overwriting is harmless, both are
  consistent snapshots of the same database), and manual backups
  therefore age out under the same retention. A backup the Owner wants
  to keep outside retention can simply be renamed with a suffix
  (`…-pre-migration.db`) — it stops matching the pattern and is never
  pruned. Worth one sentence in DEPLOY.md at the next docs pass.
- **Timer timing:** `OnCalendar=daily` fires at midnight in the
  system's timezone — UTC on the VM, so 07:00 WIB. Harmless; stating
  it in DEPLOY.md would prevent a beginner's "it hasn't run yet"
  confusion.
- A failed backup is loud in the journal but nothing retries it that
  day (the next day's run does). Acceptable for a once-a-day job; the
  test-it-now step covers the first run.
- The M9 tests now depend on the `sqlite3` CLI being installed (the
  VM has it from DEPLOY.md Step 11). Consider one line in README or
  the test file docstring ("tests run deploy/roundtable-backup.sh,
  which needs the sqlite3 CLI") at the next docs pass.

## Next step

1. The Owner merges PR #8 with **"Create a merge commit"**.
2. Muse adds `docs/audits/AUDIT-M9.md` (this file, unchanged) via the
   usual small PR — pre-approved for that file alone.
3. Installing the timer on the VM is a separate, optional step
   (DEPLOY.md "Backups now run automatically") — no code deploy is
   involved; the VM keeps running `a58d786`.
4. Claude's retrospective review on 2026-11-15 covers this report and
   the `audit_log` entries of the `zcode` experiment.
