# Audit report — PR-to-be "M11: publish as a public MCP"

- Auditor: ZCode (stand-in Auditor; Claude returns 2026-11-15 and will co-sign)
- Audited: branch `m11-publication`, pushed head `ebd984a` (one commit, parent `cfbd62c` = current `main`), plus the CI file `bundles/ci.yml` supplied through the Owner for the pre-approved addition
- Date: 2026-11-14
- Status at audit time: **no PR opened yet, deliberately** — the pushed branch lacks `.github/workflows/ci.yml` (the Builder's GitHub app lacks the Workflows permission; two push routes → 403, diagnosed by the Builder). This audit therefore covers the pushed head **plus** the pre-approved `ci.yml` addition, and the resulting tree was proven green end to end.

## Verdict: PASS WITH REQUIRED FIXES — one docs fix (R1); the missing CI file is resolved by an Owner action, pre-approved below

The package is real and works like an ecosystem MCP server: built, twine-checked, installed from the wheel into a clean venv, and driven end to end (`init` → refuses overwrite → `run` → `/status` 200, `/mcp` 401). The README landing is accurate — all five client snippets verified against their official docs. Two things stand between this and merge: R1 (a misleading `uvx` command) and the mechanical addition of the CI file (Owner action, pre-approved).

## What I checked

- Fetched `m11-publication` from GitHub: pushed head `ebd984a` matches the Builder's description, parent = current `main` (`cfbd62c`), and the branch **does not contain `.github/workflows/ci.yml`** (verified by tree listing) — matching the Builder's 403 diagnosis exactly. No PR opened.
- Whole diff read (`pyproject.toml`, `roundtable/__init__.py`, new `roundtable/cli.py`, README, new `QUICKSTART.md`, SPEC, CHANGELOG, 4 test files). No changes to tool behaviour or schema; `roundtable.cli` is a thin wrapper delegating `run` to `roundtable.server.main`.
- **Package identity**: distribution renamed `roundtable-mcp`, version `0.2.0`, MIT license, `requires-python >= 3.11`, classifiers, project URLs. `roundtable` (plain) confirmed TAKEN on PyPI; `roundtable-mcp` confirmed AVAILABLE (checked 2026-11-14).
- **CLI audited line by line + run for real**:
  - `roundtable init` writes `agents.json` (planner/coder/reviewer, exactly one leader) with `secrets.token_urlsafe(48)` tokens, mode 600, never echoes a token, prints connection info with a **placeholder** (not the token);
  - refuses to overwrite an existing file;
  - the generated config carries an extra `"_comment"` key — **verified tolerated** by `load_agents_config` (it reads `raw["agents"]` only);
  - `roundtable run` delegates to `roundtable.server.main` (identical defaults);
  - **exit codes correct**: refusal → 2, bare usage → 2 (verified via a script file after the first readings — `0` — were traced to **wsl.exe command-mangling artifacts**, not the CLI; `main()` verifiably returns 2 and `sys.exit(main())` honours it);
  - bare `roundtable` no longer starts the board (a breaking change vs 0.1.0) — **documented in the CHANGELOG**.
- **Build & artifact**: from the audited tree, `python -m build` produced sdist + wheel; `twine check` PASSED both. The wheel installed into a **clean venv**; `roundtable init` / `run` smoke-tested end to end (`/status` 200, `/mcp` 401). Wheel contents scanned: no env/secret files.
- **README landing**: badges, 3-sentence pitch, one-line install, 5 client snippets — **each verified against its official docs**: Claude Code (`--mcp-config`/headers — consistent with M4/M8 verified knowledge), opencode (`opencode mcp add --global`, `mcp.servers` — verified M10), Cursor (`.cursor/mcp.json`, `mcpServers`, no `type` for remote, `${env:VAR}` in headers — verified against cursor.com/docs 2026-11-14), VS Code (`.vscode/mcp.json` with `servers`, `type: http`, `inputs` promptString + `password: true` — verified against code.visualstudio.com 2026-11-14, including the honest warning that `inputs` don't forward to Copilot sessions).
- **CI file** (`bundles/ci.yml`, supplied through the Owner): `ubuntu-latest`, Python 3.12, pinned actions (`checkout@v4`, `setup-python@v5`), conditional `sqlite3` install (the M9/M10 lesson, applied), `pip install -e ".[dev]"`, `pytest -q`. Clean; no secrets; pull_request runs are fork-safe (no secrets defined).
- **CHANGELOG**: honest — records the rename, the version bump, the bare-`roundtable` behaviour change, the Owner-only PyPI step, and Gemini Argon's parallel landing contribution. **SPEC**: M11 milestone line added.
- **Suite**: at the pushed head the suite cannot even collect (`test_ci_workflow` reads the missing file — exactly why the Builder withheld the PR). After adding the pre-approved `ci.yml`: **197 passed** in ~197 s (34+21+3+24+30+5+8+9+11+12+22+18 = 197, matching the README). The Builder's fail-before claim was not independently re-verified (standing note).

## Required fix

| # | Finding | Evidence | Fix |
|---|---|---|---|
| R1 | **The `uvx` invocation is misleading.** README's Install section (and the CHANGELOG entry) offer `uvx roundtable-mcp` as the no-install alternative. The package's only console entry point is `roundtable`, so `uvx roundtable-mcp` runs it **bare** — printing usage and exiting without starting anything. A beginner following that line concludes the tool is broken. | `roundtable-mcp`'s `entry_points.txt`: one script, `roundtable`; bare invocation prints usage (verified live). | Use the correct form everywhere the phrase appears (README Install, CHANGELOG): `uvx --from roundtable-mcp roundtable init` and `uvx --from roundtable-mcp roundtable run` (or drop the `uvx` alternative — but the corrected form is genuinely useful). |

## Pre-approved Owner actions (no further audit round needed)

| # | Action | Status |
|---|---|---|
| P1 | **Add `.github/workflows/ci.yml` to the pushed branch** — the file is exactly `bundles/ci.yml`, audited clean above. Recommended over granting the Muse app the Workflows permission (least privilege; workflow files are executable-on-runners — keep that write in the Owner's hands). After this commit the suite passes 197 (verified live at audit head `71e1b9c` = `ebd984a` + this file). | Auditor executed the exact addition locally: **197 passed**. |
| P2 | **Add `docs/audits/AUDIT-M9.md`, `AUDIT-M10.md`, `AUDIT-M10-FIXES.md`, and (after this report) `AUDIT-M11.md` unchanged** — the M10 notes PR never happened, so four audit reports are missing from `docs/audits/`. Standing pre-approval pattern applies: any head differing from the audited one only by these unchanged files needs no further audit round. | |
| P3 | **PyPI upload (after merge + Claude co-sign)**: Owner-only — create the PyPI account/token, then from a clean checkout of the merged tag: `python -m build` and `twine upload dist/*`. The v0.2.0 tag should be pushed at merge time. | |

## Recommended (not blocking)

- **R3.** Add a build+check step to CI (`python -m build` + `twine check dist/*`) so packaging regressions surface on every push, not only at release time.
- **R4.** One sentence in the README's Install section noting that `roundtable` bare no longer starts the board (0.1.0 behaviour) — for anyone upgrading from a manual 0.1.0 install.

## Next step

1. **Owner**: add `ci.yml` to `m11-publication` (P1 commands below), push.
2. **Muse**: fix R1, optionally open the audit-reports PR (P2), and open PR #11 from the final head; report the head hash.
3. **Auditor**: re-check R1 + the final head (quick), then **Claude co-signs on 2026-11-15**; the Owner merges.
4. **Owner publishes to PyPI** (P3) from the merged tag, then the v0.2.0 release note lands — and No. 001 on botchan.tech can link to a live package.

P1 commands (Git Bash, from the repo folder):

```bash
git fetch origin
git checkout m11-publication
mkdir -p .github/workflows
cp ../roundtable-kiriman/bundles/ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "M11: add CI workflow (added by Owner - Muse app lacks the Workflows permission)"
git push origin m11-publication
```
