# Re-audit — Roundtable M7 audit fix (R1)

- Auditor: ZCode (stand-in Auditor while Claude is unavailable until 2026-11-15)
- Audited: PR imansetia/roundtable#4, branch `m7-skills`, head `a58d786c9f6b0730f932fb5cd3bde5ab4c9b1f67` (one commit on top of the audited `2f1a5fb`)
- Date: 2026-11-14

## Verdict: PASS — M7 is approved for merge and deployment

**Audited commit for the update (`AUDITED_COMMIT`): `a58d786c9f6b0730f932fb5cd3bde5ab4c9b1f67`.**

R1 is done exactly as required, and the diff touches only what it
should: `docs/DEPLOY.md` (+47), `tests/test_deploy_config.py` (+23),
`docs/audits/AUDIT-M7.md` (added), `CHANGELOG.md` (+8/−3). No server
code changed.

## What I checked

- Fetched `m7-skills` from GitHub: head `a58d786` matches the reported
  hash, its parent is the audited `2f1a5fb`, and the diff touches
  exactly the four files above — nothing else.
- **DEPLOY.md "Updating to M7"**: back up first (Step 43, "Do not skip
  this"), record the version (Step 45), Step 46 with `$AUDITED_COMMIT`,
  verify (Steps 24 and 41), reconnect both apps — and the rollback trap
  stated plainly: the rebuilt `tasks` table has no `required_skill`
  column, M6 code cannot run on a migrated database, so Step 47 must be
  combined with the backup restore (Step 44). "Undo: Step 47 and Step 44
  together." This is exactly what AUDIT-M7.md R1 asked for, and it cites
  the probe as its evidence.
- **Guard test** `test_m7_updating_section`: pins the section (Step 43,
  Step 46, `AUDITED_COMMIT`, restart, Reconnect, Step 44 + Step 47, and
  the two key phrases "cannot run" / "required_skill"). Passes.
- **AUDIT-M7.md added unchanged**: byte-identical to the report the
  Auditor delivered (compared with `diff`).
- **CHANGELOG**: the earlier "no DEPLOY.md section was added" statement
  is corrected honestly, crediting R1.
- **Full suite on the exact head**, from a fresh `git clone` of GitHub
  at `a58d786`, in WSL Ubuntu 24.04 (Python 3.12.3, the VM's version):
  first run **133 passed, 1 failed** — `test_12_list_filters_and_pagination`
  with a connection error, a live-server test that R1 does not touch
  (docs only). It then passed **3/3 alone** and the whole acceptance
  file **21/21**, and the full suite re-run passed **134/134** in ~148 s.
  Same pattern as the known slow-container flakes (AUDIT-M6-FIXES N4);
  not blocking.

## Notes (not blocking)

- **N1. Stale sentence about the Muse token.** The new section's step 4
  says "Muse needs the new worker token — the old one was rotated after
  the M4 demo", copied from the M5/M6 sections. For M7 it is wrong:
  no token rotation has happened since the M5 deployment, so Muse's
  stored credential stays valid across this restart, and the project's
  own rule is that tokens only move when they must. The Owner should
  **not** re-paste the Muse token when deploying M7 (unless they rotate
  it on purpose, CONNECT.md Step 16). The M5/M6 sections carry the same
  stale sentence; reword all three in the follow-up docs commit:
  "the restart drops the leader's MCP connection (/mcp → Reconnect);
  Muse's stored credential remains valid — reconnect it normally, and
  re-paste a token only after an actual rotation (Step 16)."
- **N2.** This report should be added to `docs/audits/` (with the N1
  rewording) as a small docs-only PR after the merge; the deployment
  uses the audited commit above regardless.

## Next step

1. The Owner merges PR #4 with **"Create a merge commit"**.
2. The Owner deploys with "Updating to M7": back up first (Step 43),
   set `AUDITED_COMMIT=a58d786c9f6b0730f932fb5cd3bde5ab4c9b1f67`,
   Step 46, verify (Steps 24 and 41), reconnect the leader — no token
   handoff needed (N1).
3. Muse opens a small docs PR adding `docs/audits/AUDIT-M7-FIXES.md`
   and the N1 rewording; the Auditor quick-checks it; the Owner merges.
