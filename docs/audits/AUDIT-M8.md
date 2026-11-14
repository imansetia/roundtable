# Audit report — PR #6 "M8: onboarding & operations"

- Auditor: ZCode (stand-in Auditor; Claude returns 2026-11-15 and will co-sign)
- Audited: PR imansetia/roundtable#6, branch `m8-onboarding`, head `27b52ed70db84cd7793e6a8eb2bc890cf9b1873a` (one commit, parent `ecbe5a1` = current `main`)
- Date: 2026-11-14
- Status at audit time: PR open, not merged — the M7-lesson was applied.

## Verdict: PASS WITH REQUIRED FIXES — do not merge yet

The runbook, Part 0, the worker service template and the scope discipline
are all excellent, and every code-facing claim checks out against the
actual source. One required fix: **the opencode section of
`docs/ADD-AGENT.md` does not match opencode's current documentation** —
its config shape and file name are outdated, so the commands would fail
or silently not load for a beginner following them. Fix R1, push to the
same branch, and report the new head hash.

## What I checked

- Fetched `m8-onboarding`: head `27b52ed` matches the reported hash, its
  parent is the current `main` (`ecbe5a1`), PR #6 is open and not merged
  (GitHub API). Whole diff read: 9 files, 879 insertions — **no file
  under `roundtable/` and no `worker.py` change** (scope guard kept).
- **docs/ADD-AGENT.md** (425 lines) read in full. Its server-behaviour
  claims were verified against the actual source, not taken on trust:
  duplicate agent names are rejected at startup
  (`roundtable/config.py:159`), duplicate tokens and short tokens too;
  `worker.py` really has the `run_forever` reconnect loop
  (`worker.py:526`, reconnect on connection errors at 562–568); the
  expected log line `connected as … (skills: …)` exists
  (`worker.py:235`). Deactivation-vs-rotation semantics match the M1 R3
  behaviour; the fail-fast causes listed (two leaders, duplicates, short
  token, bad JSON) match the config loader.
- **deploy/roundtable-worker.service** — same hardening shape as the
  board's M3 unit (NoNewPrivileges, ProtectSystem=strict with no
  writable path, ProtectHome, PrivateTmp/PrivateDevices, kernel
  protections, LockPersonality, RemoveIPC, empty capability set,
  RestrictAddressFamilies, SystemCallFilter). Memory discipline is
  right for a shared VM: `MemoryMax=128M`, `MemorySwapMax=0` (the M3 R2
  rule), `OOMScoreAdjust=600` — the worker dies before the board (500)
  and Hermes (0). Env file lives outside the checkout, mode 600,
  placeholders only in the shipped template.
- **Part 0 in DEPLOY.md and CONNECT.md** — covers exactly the traps hit
  live on 2026-11-14: `tailscale status` (logged-out state, socket
  permission error → sudo, daemon down → systemctl), finding the VM
  host from the scripts' `VM_HOST` default (`100.75.20.68`, "do not
  guess"), Windows OpenSSH vs Git's ssh (PowerShell's plain `ssh` is
  Windows OpenSSH — a useful addition), bracketed-paste markers. The
  CONNECT.md intro pieces moved into Part 0 as described.
- **tests/test_m8_onboarding.py** — 9 tests guarding the runbook, Part
  0, the templates, the CHANGELOG choice record, a scope guard
  (docs/scripts/templates only) and the README status.
- **CHANGELOG / SPEC / README** — consistent and honest; test counts
  (143 = 134 + 9 M8) verified against the suite.
- **Full suite on the exact head**, fresh `git clone` from GitHub at
  `27b52ed`, WSL Ubuntu 24.04 (Python 3.12.3, the VM's version):
  **143 passed** in ~120 s, first run, no flakes.
- The Builder's test-first claim (the 9 tests failed before the docs
  existed) was not independently re-verified — same note as AUDIT-M7.
- **Sources section verified externally:** the opencode claims were
  checked against <https://opencode.ai/docs/mcp-servers/> (fetched
  2026-11-14) — see R1.

## Required fix

| # | Finding | Evidence | Fix |
|---|---|---|---|
| R1 | **The opencode section is out of date with opencode's current docs.** It (a) nests the server directly under `mcp` (`{"mcp": {"roundtable": …}}`), while the current docs say servers live under `mcp.servers` with a unique name ("V2 does not place server names directly under `mcp`"); (b) calls the file `opencode.json` / `~/.config/opencode/opencode.json`, while the docs' config is `opencode.jsonc` and the global route is `opencode mcp add --global`. A beginner following the section could end up with a server that never loads. | opencode.ai/docs/mcp-servers (fetched 2026-11-14): example config nests under `mcp.servers`, file is `opencode.jsonc`, `--global` for all projects. | Rework the opencode subsection to the documented shape: `{"mcp": {"servers": {"roundtable": {"type": "remote", "url": …, "oauth": false, "headers": {"Authorization": "Bearer {env:ROUNDTABLE_TOKEN}"}}}}}`, file `opencode.jsonc` in the project (or `opencode mcp add --global` for every project), keep the `{env:…}` substitution and `opencode mcp list` verification (both are correct), and keep the advice to prefer the environment variable over a token in the file (the docs recommend the same). |

## Recommended (not blocking)

- **R2 (the Owner's addendum that arrived after the brief was sent):**
  the "deployment shapes + local quick-start" section (A: local laptop /
  B: own VM / C: hosted public) is an approved roadmap item that is not
  in this PR. Adding it as one more docs-only commit on this branch
  would complete the beginner story before merge; or it can lead M10.
  Owner's choice.
- The guide's Step 2 heredoc appends without a duplicate-name guard
  (running it twice leaves two same-name entries until the restart,
  which then fails fast — safe, but a beginner-friendly `if name in
  names` check as in the M7-notes experiment would be gentler).

## Next step

1. Muse fixes R1 on `m8-onboarding` (one commit), reports the new head
   hash.
2. The Auditor re-checks R1 and re-runs the suite on the new head
   (quick).
3. Claude returns 2026-11-15: co-signs this audit, re-audits the
   `zcode` worker experiment from `audit_log`, and the Owner merges PR
   #6 only after the co-signed PASS. Deploy is unaffected — no server
   file changed; the worker service installs separately when wanted.
