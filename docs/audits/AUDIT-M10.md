# Audit report — PR #10 "M10: publication"

- Auditor: ZCode (stand-in Auditor; Claude returns 2026-11-15 and will co-sign)
- Audited: PR imansetia/roundtable#10, branch `m10-publication`, head `e06625fd151493edfb9e18fad71a60c1e6be0106` (one commit, parent `e24712c` = current `main`)
- Date: 2026-11-14
- Status at audit time: PR open, not merged.

## Verdict: PASS WITH REQUIRED FIXES — do not merge yet

The status page is built exactly as specified — aggregates only, no
board content, per-IP rate limit with anti-spoof XFF handling, the
/mcp gate untouched — and my own live attack probes could not extract
a single byte of board content. But the public endpoint has **no
Host-header (DNS-rebinding) protection**, which on a Shape A local
install lets a malicious webpage read the board's private aggregates.
One small server fix closes it (R1); everything else is approved.
Fix R1, push to the same branch, and report the new head hash.

## What I checked

- Fetched `m10-publication`: head `e06625f` matches the reported hash,
  its parent is the current `main` (`e24712c`, the M9-notes merge), PR
  #10 is open and not merged (GitHub API). Whole diff read: 16 files,
  945 insertions — including the **first `roundtable/` changes since
  M7** (`server.py` +65, `board.py` +19, `config.py` +3).
- **`server.py` / SDK internals** (read from the installed SDK, not
  taken on trust): `/status` is registered via the SDK's own
  `custom_route` extension point — the SDK's docstring states such
  routes "will not require authorization… intended to be public", and
  the lowlevel app appends them as separate Starlette routes beside
  the `/mcp` route, whose handler contains the transport (Host
  validation) and bearer logic. Muse's claim that both protections
  apply to `/mcp` only is **accurate**.
- **`board.public_status()`**: `SELECT status, COUNT(*) … GROUP BY
  status` and `SELECT COUNT(*) FROM agents WHERE active = 1` — counts
  only, under the board lock; no content can leave the method by
  construction.
- **Rate limiting**: a dedicated `status_limiter` (same per-minute
  limit as tool calls), keyed by `_status_client_ip` — X-Forwarded-For
  trusted only when the direct peer is loopback (the nginx proxy);
  a direct client cannot forge its key. Rate-limited calls answer 429
  and write no audit row.
- **Independent live attack probe (my own script, not Muse's tests)** —
  real M10 server on a temp DB, seeded with a task whose title,
  description and result all contain a secret marker:
  - `/status` returns 200 with correct aggregates (1 submitted, 2
    active agents, board name + version + server time);
  - **NO marker leak** — the title, description and result text never
    appear; no agent names; no token fragments; no token bytes in the
    DB/WAL;
  - `POST /status` → 405;
  - `/mcp` regression: no token → 401; with a token the request passes
    the gate (gate untouched);
  - **`Host: evil.example.com` on `/status` → 200** — the DNS-rebinding
    gap, reproduced live (see R1);
  - rate limit per IP: on a dedicated limit-60 server, requests 61–75
    from one XFF IP get **429** while the first 60 got 200, and a
    different XFF IP gets its own bucket (anti-spoof verified: from a
    loopback peer the header is trusted, matching the code);
  - `audit_log`: still exactly the 3 seed tool-call rows — **no rows
    for ~135 status requests**.
- **`deploy/nginx/roundtable.conf`**: `location = /status` mirrors
  `/mcp`'s proxying (Host `$host`, XFF forwarded, unbuffered), shares
  the existing `limit_req zone=mcp` (see R2 note), everything else
  still 404.
- **`scripts/install-local.sh`** read in full: refuses a second board
  on 127.0.0.1:8000, never overwrites an existing `agents.json`,
  generates tokens inside Python, waits for the 401 gate, verifies
  (whoami + `/status`), leaves a PID file and log. The tests run it
  for real against a temp repo copy.
- **`deploy/Dockerfile`** — the 0.0.0.0-in-container + `-p
  127.0.0.1:8000:8000` judgement call is sound (Docker's port mapping
  targets the container interface, so an in-container loopback bind
  would refuse the mapping; the loopback protection lives at the
  publish step). `agents.json` is mounted read-only and never baked
  into the image; the DB lives in a volume. Accepted, recorded in the
  CHANGELOG as required.
- **SPEC §7 item 11**, DEPLOY "Updating to M10" (backup, audited
  commit, nginx site reinstall, verification) + new Part 11 item, and
  the README status/public section are all consistent with the
  behaviour. Guard tests cover SPEC, DEPLOY, README, CHANGELOG, the
  templates, the install script (run for real) and a scope guard.
- **Full suite on the exact head**, fresh clone at `e06625f`, WSL
  Ubuntu 24.04 (Python 3.12.3, the VM's version): **176 passed** in
  ~161 s, first run, no flakes. (Calibration note: a bare urllib POST
  to `/mcp` with a token gets 406 rather than 400 because it sends no
  `Accept` header — the SDK rejects it after the bearer gate; curl's
  default `Accept: */*` passes, so DEPLOY Step 25's expected 400 stays
  correct. Not a defect; a probe-calibration lesson.)

## Required fix

| # | Finding | Evidence | Fix |
|---|---|---|---|
| R1 | **`/status` has no Host-header (DNS-rebinding) protection.** The SDK applies its transport security inside the `/mcp` handler only; custom routes bypass it entirely, and nothing in the handler compensates. On a Shape B/C VM, nginx's `server_name` routing blocks forged Hosts — but on a **Shape A local install** (the beginner path M10 was built for) a malicious webpage can rebind a domain to `127.0.0.1:8000` and read the board's private aggregates (task counts, agent count, board name) from the visitor's own machine, without the protection SPEC §7.5 brags about. | Probe: `GET /status` with `Host: evil.example.com` → **200** with full aggregates. | In the `/status` handler, validate the Host header before answering: accept the bound host/loopback variants and, when configured, `ROUNDTABLE_ALLOWED_HOSTS` entries (reuse the same parsing as `transport_security_settings`); anything else → 404/400 with no body detail. Add a test that a forged Host is rejected and a line in SPEC §7 item 11 saying the endpoint applies the same Host policy as the transport. |

## Recommended (not blocking)

- **R2.** `/status` and `/mcp` share the nginx `limit_req zone=mcp` — an
  unauthenticated `/status` flood competes with legitimate `/mcp`
  traffic in the same zone (both degrade to 503). `/mcp` was already
  publicly floodable, so no new capability is added; still, a dedicated
  zone for `/status` (e.g. `limit_req_zone` 20 r/s) is a one-line
  hardening at the next nginx touch.
- The Builder's test-first claim (19 tests failed before the change)
  was not independently re-verified (standing note, M7/M8/M9).
- A one-line note in DEPLOY.md's M10 section that the backup timer
  (M9) and worker service (M8) install steps are independent of this
  update — the Owner has been keeping the two threads apart correctly.

## Next step

1. Muse fixes R1 on `m10-publication` (one commit, includes the test),
   reports the new head hash.
2. The Auditor re-checks R1 and re-runs the suite on the new head
   (quick).
3. Claude's retrospective review on 2026-11-15; the Owner merges PR #10
   only after the co-signed PASS.
4. **M10 is a server change** — after merge, the VM gets its first
   deploy since M7 via the new "Updating to M10" section (backup →
   `AUDITED_COMMIT` → Step 46 → nginx site reinstall → verification);
   the Auditor provides the step-by-step walkthrough when the audit
   passes.
