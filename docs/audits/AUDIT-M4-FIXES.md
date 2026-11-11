# Re-audit — Roundtable M4 audit fixes (docs)

- Auditor: Claude
- Audited: bundle `roundtable-m4-audit-fixes.bundle`, commit `2d16244` on top of the audited M4 docs (`a9b2215`)
- Date: 2026-11-11

## Verdict: PASS — CONNECT.md and DEMO.md may be run

M1–M7 are fixed correctly and R1–R3 were taken. There are still no server changes. The docs commit to run from is `2d16244`; the board stays on the deployed `42106be`, which is unchanged.

## What I checked

- Verified the bundle (on top of `a9b2215`) and read the full diff. `docs/audits/AUDIT-M4.md` is identical to my report.
- Ran the suite in Ubuntu 24.04.5: **84 passed**.
- Confirmed that the Auditor's own Claude Code config (`~/.claude.json` on the Owner's PC) has no `roundtable` server.

| # | Fix | Status |
|---|---|---|
| M1 | `--scope local` in `~/roundtable-leader` (add, fallback, remove); Auditor session must not have the server | Fixed |
| M2 | Leader gets goal-level prompts only; canary unlabelled and never mentioned; notes recorded verbatim | Fixed |
| M3 | Description requires hyphens to be ignored, so version 1 genuinely fails | Fixed |
| M4 | Evidence query runs as `roundtable`, `-readonly` | Fixed |
| M5 | Tokens selected by name; Step 16 reads the old token before rotating | Fixed |
| M6 | `claude mcp get` tried with `dummy-check` first; `get` with the real token only if the header is shown by name | Fixed |
| M7 | Step 6 is Owner-only; no-display route for the leader; clipboard-history warning | Fixed |
| R1–R3 | `agents.json.bak` kept until Step 5 then deleted; inactive-row note; Muse doc source stated honestly | Taken |

## Notes for the run (not blocking)

- **N1.** The no-display route's `<vm-host>` needs the key: `/c/Windows/System32/OpenSSH/ssh.exe -i "$USERPROFILE/.ssh/azure-hermes" azureuser@100.75.20.68 …`. The Auditor will give the exact line at that step.
- **N2.** Steps that show or move a token (Step 6, the no-display route, Steps 10 and 14) are run by the Owner. The Auditor runs only the steps that never print a token (Steps 1–5, 16 without its display, the evidence query) and watches the rest.
- **N3.** The demo's leader prompts for creating the task (step 1) may be explicit: that is the Owner giving the leader its job. Only the review prompts (steps 5 and 7) must stay goal-level.

## Next step

1. The Owner approves: fast-forward `main` to `2d16244` and push, then add this report to `docs/audits/`.
2. Run CONNECT.md, then DEMO.md, together, with each VM command approved by the Owner. Then fill in `docs/demo/M4-demo.md`, rotate the `muse` token, and the Auditor writes the M4 record.
