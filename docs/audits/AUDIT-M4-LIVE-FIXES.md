# Audit report — Roundtable M4 live-run fixes (G1–G9)

- Auditor: Claude
- Audited: bundle `roundtable-m4-live-run-fixes.bundle`, commit `98d0517` on top of `a7b5cd4`
- Date: 2026-11-11

## Verdict: PASS WITH TWO SMALL REQUIRED FIXES — fold them into the first commit of M5

G1–G9 match what happened in the live run, and there are no server changes. The helper scripts contain no token and pass `bash -n` and `shellcheck -S warning`. Two things must be fixed before these docs and scripts are used again; neither needs a separate round.

## What I checked

- Verified the bundle (its only ref is `HEAD`, so fetch `HEAD`, not `refs/heads/main`; on top of `a7b5cd4`) and read the full diff.
- Suite in Ubuntu 24.04.5: **95 passed**. shellcheck on `scripts/*.sh`: clean.
- The committed blobs use LF line endings (checked with `git cat-file`).

## Required fixes

| # | Finding | Fix |
|---|---|---|
| H1 | **CHANGELOG states the opposite of the Owner's decision.** The G9 entry says "The Owner decided not to change the board for this", and the open questions still ask whether review notes should be kept. The Owner decided on 2026-11-11 to keep review notes permanently (M5). | Correct the G9 sentence and the open question: review history is M5; keeping superseded results is still an open question. |
| H2 | **The scripts break when run from the Owner's Windows checkout.** The repository has no `.gitattributes`, and the Owner's git uses `core.autocrlf=true`, so `scripts/*.sh` are checked out with CRLF. Bash then fails (`$'\r': command not found`). The Auditor reproduced this: a CRLF export failed `bash -n` with "syntax error near unexpected token `\|`". | Add `.gitattributes` with `*.sh text eol=lf` (and `scripts/** text eol=lf`), and add a test that every `scripts/*.sh` blob is LF-only. |

## Notes (not blocking)

- The scripts call `ssh.exe` without `-o BatchMode=yes`. On a key problem, ssh would ask for a password instead of failing fast. Consider adding it.
- `~/.ssh/azure-hermes` is passed to a native Windows `ssh.exe`; Git Bash converts the path, but the Auditor's own run used `"$USERPROFILE/.ssh/azure-hermes"`. Check the scripts once, live, at the next use.
- `bundle`: create it with `git bundle create … main` so it carries `refs/heads/main`, as before.

## Next step

Do H1–H2 as the first commit of M5, on top of `98d0517`. M5 itself (permanent review history) is in the Owner's separate M5 prompt.
