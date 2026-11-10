# Audit report — Roundtable M2

- Auditor: Claude
- Audited: bundle `roundtable-m2.bundle`, commits `6cadaa6`, `0d31528`, `b2c4348` on top of `a3bdedb`
- Date: 2026-11-10

## Verdict: PASS WITH REQUIRED FIXES

F1 is fixed correctly. `worker.py` is clean, readable and well tested: secrets stay in headers, error messages never echo values, the model has no tools (so prompt injection can only affect the result text), and Ctrl+C handling works. But three live probes show the worker can **burn API quota without limit** and **lose tasks permanently**. Those must be fixed before M3.

## What I checked

- Read the full diff since `a3bdedb`, all of `worker.py` and the new tests.
- Ran the suite independently: **37 passed**.
- Ran a real server + a fake model server + the real `worker.py` as a process, for cases the tests do not cover.

## Required fixes (block M3)

Each fix needs a test that fails before and passes after.

| # | Finding | Evidence (live) | Fix |
|---|---|---|---|
| W1 | **The hourly cap does not protect quota when the model fails.** The cap counts only *submitted* tasks. A task whose answer is always too long (or always errors) is released and re-claimed every poll, and every attempt is a paid model call. | `WORKER_MAX_TASKS_PER_HOUR=1`, poll 0.5 s, model always returns 25,000 chars -> **18 model calls in 10 s** (about 6,500/hour). | Count **every model call** toward the hourly cap, not only submits. Also: after a task fails, do not retry that same task for a cool-down (default 15 min), and after 3 failures on one task stop retrying it and post a `help_request` message on it explaining the failure (no secrets, no model output). |
| W2 | **A crashed worker leaves its task claimed forever.** If the process dies mid-task (power loss, laptop sleep, kill), the task stays `claimed` by that worker. Restarting the worker does not recover it, and no tool can. | Kill -9 during a model call -> task `claimed` by `coder`; after restart, still `claimed` by `coder`. | On startup, after `whoami`, list tasks with `status="claimed"` and `assignee=<self>` and release each one, logging the task id. |
| W3 | **Matching tasks can be starved.** The worker reads only the first 50 open tasks. If 50 or more open tasks need other skills, a matching task further down is never seen. | 55 open `rust` tasks, then 1 `python` task -> the python worker never claims it. | Page through all open tasks (`limit` 100, increasing `offset`) until a match is found or the list ends. |
| W4 | **Secrets can be sent over plain HTTP.** Any `http://` URL is accepted for both the model and the board, so a typo sends the API key or board token unencrypted. | Code review: no scheme check on `WORKER_MODEL_BASE_URL` or `ROUNDTABLE_URL`. | Refuse to start unless each URL is `https://`, or `http://` to a loopback host (`127.0.0.1`, `::1`, `localhost`). Fail fast naming the variable. |
| W5 | **`worker.py` needs `httpx` at runtime, but `httpx` is only a dev dependency.** A plain `pip install .` produces a worker that crashes on import. | `pyproject.toml`: `httpx` is under `[project.optional-dependencies] dev` only. | Move `httpx` to the main `dependencies`. |

## Recommended (not blocking)

- Task titles are written to the log as-is. Titles are untrusted; strip control characters (newlines, ANSI escapes) before logging, so a task cannot forge log lines.

## Accepted interpretations

CHANGELOG M2 items 1–7 are accepted as written.

## Next step

Fix W1–W5 on top of `b2c4348`, update CHANGELOG under "M2 audit fixes", run the full suite, and report back with test output and a bundle. Do not start M3.
