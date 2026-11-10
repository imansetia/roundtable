# CHANGELOG

One entry per milestone, including deviations from SPEC.md (section 12).

## M2 audit fixes (2026-11-10, AUDIT-M2.md)

Auditor verdict was PASS WITH REQUIRED FIXES. Fixes W1–W5 were applied
on top of `b2c4348`, plus the report's recommended (non-blocking) log
sanitizing. Each fix has a test in `tests/test_worker.py` that failed
before the fix and passes after (45 tests total, all passing). The
auditor's reports are kept unchanged under `docs/audits/`.

- **W1:** The hourly cap now counts **every model call**, not only
  submits — a task whose answer is always too long (or always errors)
  can no longer burn quota on endless retries. After a task fails it
  is not retried for a cool-down (`WORKER_COOLDOWN_SECONDS`, default
  900 s / 15 min); after `WORKER_MAX_FAILURES` failures (default 3)
  the worker stops retrying it and posts a `help_request` message on
  it explaining the failure (no secrets, no model output). Failure
  state is per-process (in-memory); a restart retries a task up to 3
  more times, still bounded by the hourly cap.
- **W2:** On startup, after `whoami`, the worker lists tasks with
  `status="claimed"` and `assignee=<self>` and releases each one,
  logging the task id — a worker killed mid-task no longer leaves its
  task claimed forever.
- **W3:** `run_once` pages through all open tasks (`limit` 100,
  increasing `offset`) until a match is found or the list ends, so a
  matching task behind 50+ non-matching ones is no longer starved.
- **W4:** `WorkerConfig.from_env` refuses to start unless
  `ROUNDTABLE_URL` and `WORKER_MODEL_BASE_URL` are `https://`, or
  `http://` to a loopback host (`127.0.0.1`, `::1`, `localhost`) —
  failing fast naming the variable. `http://127.0.0.1` test and local
  URLs still work.
- **W5:** `httpx` moved from dev-only to the main `dependencies` in
  `pyproject.toml`, so a plain `pip install .` produces a working
  `worker.py`.
- **Recommended:** task titles are stripped of control characters
  (newlines, ANSI escapes) via `_safe_log_text` before logging, so an
  untrusted title cannot forge log lines.

## M2 — worker.py for API-only models (2026-11-10)

Built on `a3bdedb` (M1 approved and merged). First commit fixed
AUDIT-M1-FIXES **F1**: on startup the server now also clears the
assignee of `open` tasks assigned to agents removed from the config
(R3 already reopened their `claimed` tasks); test fails before, passes
after.

`worker.py` (SPEC section 8): connects to the board as an MCP client
with its own bearer token (same `streamable_http_client` + `ClientSession`
pattern as the tests), learns its name and skills from `whoami`, polls
for open tasks matching its skill, claims one, sends it to an
OpenAI-compatible `POST /chat/completions` endpoint, and submits the
answer as the result. Config is by environment variables only
(`ROUNDTABLE_URL`, `ROUNDTABLE_TOKEN`, `WORKER_MODEL_BASE_URL`,
`WORKER_MODEL_NAME`, `WORKER_API_KEY`, `WORKER_POLL_INTERVAL`,
`WORKER_MAX_TASKS_PER_HOUR`, `WORKER_MODEL_TIMEOUT`); missing required
variables fail fast with the variable named. The system prompt tells
the model the task text was written by another agent and is data, not
instructions. On any failure after claiming (model error, empty answer,
answer over the 20,000-char result cap, submit error, interruption)
the task is released back to `open`. Ctrl+C / SIGTERM stops the loop,
releases a claimed task and exits 0. The board token and model API key
travel only in `Authorization` headers and are never printed or logged;
the model answer is never logged either.

Tests: 12 new tests in `tests/test_worker.py` (37 total, all passing)
against a real server and a small fake OpenAI-compatible server on
localhost — no real model API, no real API key. Covered: claims only
matching-skill tasks (and never another worker's assigned task),
submits the model's answer, releases on model failure / empty reply /
too-long answer, respects the max-tasks-per-hour cap, exits 0 on Ctrl+C
while working and while idle, never prints the API key (while the fake
server confirms it did receive it in the header), and fails fast on
missing config.

Deviations / interpretations (SPEC section 12):

1. **Skill match:** a task with no `required_skill` has no skill
   requirement, so any worker may claim it. A task whose required
   skill is set must be one of the worker's skills.
2. **Hourly cap** counts submitted tasks in a rolling 1-hour window;
   when reached the worker waits instead of claiming more (it does not
   exit).
3. **Too-long answer:** the worker releases the task instead of
   submitting a truncated answer (the server rejects over-limit results
   rather than truncating).
4. **Ctrl+C during a model call:** the call is cancelled and the
   claimed task released; the worker does not wait for the model to
   finish. A stop while idle just closes the session.
5. **Env var names** are the Builder's choice — the spec lists the
   settings but not their names (see README / `.env.example`).
6. **MCP teardown must not be shielded:** `disconnect()` calls the
   SDK's `__aexit__` methods directly. Wrapping them in
   `asyncio.shield` makes the SDK's own task-group cancellation land
   on the shield's inner task as `CancelledError`.
7. The worker only ever calls `whoami`, `list_tasks`, `claim_task`,
   `submit_result` and `release_task`.

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
