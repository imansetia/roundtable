# CHANGELOG

One entry per milestone, including deviations from SPEC.md (section 12).

## M1 audit fixes (2026-11-10, AUDIT-M1.md)

Auditor verdict was PASS WITH REQUIRED FIXES. Fixes R1–R7 and the Q4
decision were applied on top of the M1 commits, on the same branch.
No M2 work was started. Each fix has a test in
`tests/test_audit_fixes.py` that failed before the fix and passes after
(24 tests total, all passing).

- **R1:** `load_agents_config` now rejects duplicate tokens and tokens
  shorter than 32 characters at startup. A shared token previously let
  the second agent silently act as the first.
- **R2:** Bearer tokens are compared as bytes (`token.encode()`), and any
  malformed/undecodable header returns 401. A non-ASCII bearer token
  previously raised `TypeError` in `hmac.compare_digest` -> HTTP 500.
- **R3:** The `agents` table has an `active` flag (with a migration for
  databases created by M1, which lack the column). On startup, agents
  missing from the config are marked inactive, `list_agents` shows
  active agents only, and tasks claimed by a removed agent return to
  `open` (assignee cleared). Submitted tasks stay submitted so the
  leader can still review the existing result.
- **R4:** `required_skill` is capped at 100 characters, rejected not
  truncated; same cap for the `assignee` filter in `list_tasks`. Per the
  Auditor this gap is now part of the spec.
- **R5:** `list_tasks.total` is now the count matching the same filters
  (answers Q3: filtered total).
- **R6:** `create_task` audit rows now carry the new task's id (taken
  from the returned task in the server's audit wrapper).
- **R7:** README quick start now uses `python3 -m venv .venv` and states
  "Python 3.11 or newer" in words.
- **Q4:** `post_message` is rejected with a clear error on `done` and
  `cancelled` tasks.

Open questions from M1, now resolved by the Auditor: Q1 — no reassign
tool in v1, cancel + recreate is the intended path (the spec diagram
words were a spec error); Q2 — the leader never claims or executes;
Q3 — filtered total (R5); Q4 — reject messages on final tasks (above).

## M1 — Server runs locally, all tools, all acceptance tests pass (2026-11-10)

Built: `roundtable/` server package (config, SQLite board, MCP server),
`tests/test_acceptance.py` (12 tests), `pyproject.toml`, `.env.example`,
`agents.example.json`, README. Verified with the official MCP Python SDK
v2.3.0 over Streamable HTTP on `127.0.0.1`. Test result: **12 passed**.

Deviations / interpretations (spec section 12 requires listing these):

1. **Agents config format is not defined by the spec** (section 4 says only
   "a config file written by the Owner"). Chosen: `agents.json`, an object
   with an `agents` list of `{name, role, skills, token}`. Documented in
   README.md and `agents.example.json`; startup fails fast unless there
   is exactly one leader.
2. **Rate limit counts tool calls only**, not HTTP handshake / `initialize` /
   `list_tools` requests (section 7.3 says "per token", without saying what
   counts). Counting protocol overhead would make the limit depend on the
   client's session behaviour.
3. **`review_note` capped at 4,000 chars** (like a message). The spec caps
   title/description/message/result (section 7.2) but gives no limit for
   review notes; an unbounded field was left open rather than silently
   accepted without bound.
4. **`create_task` rejects an assignee who is the leader.** Only workers
   can claim (section 6), so assigning a task to the leader would create
   a task nobody can claim. Rejected with a clear error instead.
5. **A task created with an `assignee` can only be claimed by that
   worker**; an unassigned task can be claimed by any worker. The spec
   says the assignee is assigned "directly" without defining the claim
   rule; this is the reading that makes direct assignment meaningful.
6. **On reject, the assignee is cleared** when the task returns to `open`,
   so it can be claimed again (by anyone). The spec says only
   "rejected -> open".
7. **No reassign tool.** The state diagram (section 5) mentions "leader
   reassigns" as a claimed -> open path, but section 6 defines no such
   tool, so M1 does not invent one. A leader can cancel a claimed task
   and create a replacement. Open question for the Owner/Auditor — see
   below.
8. **SDK version pinned: `mcp[cli]==2.3.0`.** The spec says to check the
   SDK's current docs rather than rely on remembered APIs; v2 renamed
   `FastMCP` to `MCPServer` and the old import fails. Tool results are
   returned as JSON text (in SDK 2.3.0 a `dict` return serialises to text
   content, not `structured_content`); tests parse the JSON text.

Open questions (for the Owner/Auditor, M1 review) — all resolved in the
"M1 audit fixes" entry above:

- Q1: Should there be a leader-only `reassign_task` tool (claimed ->
  open with a new assignee), or is cancel + recreate the intended path?
  **Resolved: no reassign tool in v1.**
- Q2: Should the leader be allowed to claim a task directly (e.g. for
  small fixes), or is leader-never-executes the intended model?
  **Resolved: leader never claims.**
- Q3: `list_tasks` returns `total` over all tasks (not filtered count).
  Is a filtered total wanted, or is the current behaviour fine?
  **Resolved: filtered total (R5).**
- Q4: Should `post_message` be allowed on `done`/`cancelled` tasks, or
  restricted to open/claimed/submitted ones? M1 allows it on any task.
  **Resolved: reject on done/cancelled (Q4 fix above).**
