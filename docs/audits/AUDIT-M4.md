# Audit report — Roundtable M4 (docs)

- Auditor: Claude
- Audited: bundle `roundtable-m4.bundle`, commits `39954a8`, `a9b2215` on top of `c035487`
- Date: 2026-11-11

## Verdict: PASS WITH REQUIRED FIXES — do not run CONNECT.md or DEMO.md yet

F1–F6 are fixed correctly, and there are no server changes. The token handling is careful: tokens are generated inside Python, checked by name/role/length only, sent on stdin, and the old token is proven dead. But four defects would weaken the demo or put a leader token where it does not belong: the user scope, the scripted canary, the task description that contradicts the reject reason, and the evidence query that cannot open the database. Fix M1–M7 below first.

## What I checked

- Verified the bundle (complete history, on top of `c035487` = current `main`) and read the full diff, all of `docs/CONNECT.md`, `docs/DEMO.md` and `docs/demo/M4-demo.md`.
- Confirmed against the live deployment record and `roundtable/db.py` (the `audit_log` columns, WAL mode, the `/srv/roundtable/data` mode 700).
- Checked the Claude Code claims against https://code.claude.com/docs/en/mcp (read 2026-11-11).
- Ran the suite in Ubuntu 24.04.5: **77 passed**.

## Confirmed good

- F1–F6 match AUDIT-M3-DEPLOY.md; F2's loop is tested by running the guide's own text.
- Agent rename: backup first, tokens generated in Python, names/roles printed without tokens, restart only Roundtable, old token → 401.
- Tokens move one at a time; the "disconnect is not revoke" distinction is stated plainly.
- No `--dangerously-skip-permissions`, no "don't ask again" for board tools, empty working folder.
- Muse is told to stop rather than put a token in a URL or add OAuth.

## Required fixes

| # | Finding | Evidence | Fix |
|---|---|---|---|
| M1 | **User scope gives every Claude Code session on the PC leader access.** A user-scoped server loads "across all projects on your machine", including the Auditor's own session and any unrelated project. With the Step 11 fallback the literal leader token is then active everywhere. That defeats the separation of the leader from the Auditor, and exposes the leader role to prompt injection from any project. The guide's reason for user scope ("a local-scoped server would only load in that one folder") is exactly why local scope is right. | Claude Code docs: local = "loads only in the project where you added it"; user = "available across all projects on your machine". | Use `--scope local`, run from `~/roundtable-leader` (Steps 10, 11, 17). State that the Auditor session must not have the `roundtable` server. |
| M2 | **The canary test is scripted, so it proves nothing.** Step 5 gives the leader the exact `review_task` call, including a note saying the canary was ignored. The canary itself is labelled "(a test line, not an instruction)". A leader that is told what to call, and is told the line is harmless, is not being tested. | DEMO.md steps 4–5, 7. | Give the leader goal-level prompts only, e.g. "Review the submitted result of task `<id>` against its description and decide." Do not mention the canary to the leader. Remove the "(a test line, not an instruction)" label; keep the line harmless in effect (only the demo task exists). The review note is whatever the leader writes; record it verbatim. |
| M3 | **The task description contradicts the reject reason.** The description says "non-digit characters must return False", so `"978-3-16-148410-0"` must return False and version 1 is correct. A careful leader should approve it, which breaks the reject path; or it rejects for a reason the spec does not support. | DEMO.md description vs step 5 note and step 6 test. | Put hyphen handling in the description from the start ("hyphens may appear between groups and must be ignored; any other non-digit returns False"), so version 1 genuinely fails the spec. |
| M4 | **The evidence query cannot open the database.** `sqlite3 -readonly` runs as `azureuser`, but `/srv/roundtable/data` is mode 700, owned by `roundtable`. | DEPLOY Step 14; AUDIT-M3-DEPLOY. | `sudo -u roundtable sqlite3 -readonly …`. Because the database is in WAL mode, mention that this read still creates or uses the `-shm` file as `roundtable` (harmless). |
| M5 | **Step 16 reads the "old" worker token after it was already rotated.** It says to read the old token "the Step 2 way" after the rotation and restart, but by then the file holds the new token, so the 401 check tests nothing. | CONNECT.md Step 16. | Read the old token into `$TOKEN` before the rotation, rotate, restart, send it (expect 401), then `unset TOKEN`. Select tokens by agent name, not by list index (`[0]`/`[1]`), in Steps 2, 6 and 16. |
| M6 | **Unverified claim that `claude mcp get` hides the header value.** The docs say references are shown by name for the URL and command line in `/mcp` and in `list`/`get` output. They do not say this for header values. Step 11 runs `claude mcp get roundtable` while the real token is in the environment, which could print it. | Claude Code docs (mcp page); CONNECT.md Steps 6 and 11. | Before Step 9, add a check with a dummy value (`ROUNDTABLE_TOKEN=dummy-check`) to see what `claude mcp get` prints. Only use `get` with the real token if it shows `${ROUNDTABLE_TOKEN}`. Otherwise use `claude mcp list` only. Do not claim the header is masked unless the docs or that check show it. |
| M7 | **Step 6 must not be run through an AI agent's tool.** It prints the token. If the Auditor (or any agent) runs it, the token lands in that agent's transcript, which breaks "no chat". | CONNECT.md Step 6. | Say plainly that the Owner runs Step 6 in their own SSH window. For Claude Code, prefer getting the token with no display at all: in Git Bash, `read -r ROUNDTABLE_TOKEN < <(/c/Windows/System32/OpenSSH/ssh.exe … "sudo python3 -c …")` (Windows OpenSSH; Git's own ssh is refused by this VM). If it must be copied for Muse, say that Windows clipboard history (Win+V) keeps copies and must be cleared. |

## Recommended (not blocking)

- **R1.** Step 3: keep a 600 copy of the old `agents.json` (`agents.json.bak`) until Step 5 passes, then delete it. The current undo refers to a backup that does not exist.
- **R2.** Explain the `planner` / `coder` rows that remain in the database as inactive (M1 R3 behaviour), so `list_agents` showing only `claude` / `muse` is expected.
- **R3.** The Muse sources are third-party guides (CData, LaunchList) plus "the app's own documentation" without a URL. Cite Muse's own page by URL, or say that it cannot be linked.

## Answers to the Builder's open questions

- **`${ROUNDTABLE_TOKEN}` in `~/.claude.json`:** the docs imply it works for local and user entries but do not confirm `headers` explicitly. Test it live at Step 11, with M6's dummy check first. If it fails, use the fallback, in local scope (M1).
- **Muse's prompt asks for the token or `Bearer …`:** fine as written; record which one in the demo record.
- **Rotate the worker token after the demo:** yes. Rotate `muse` (Step 16) after the evidence is recorded, because the token passed through a copy-paste.

## Next step

Fix M1–M7 on top of `a9b2215`, update the CHANGELOG under "M4 audit fixes", run the full suite, and send a bundle. Then the Owner and the Auditor run CONNECT.md (the Owner alone for any step that shows a token) and DEMO.md.
