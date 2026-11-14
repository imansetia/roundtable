# Re-audit — Roundtable M10 audit fixes (R1 + R2)

- Auditor: ZCode (stand-in Auditor; Claude returns 2026-11-15 and will co-sign)
- Audited: PR imansetia/roundtable#10, branch `m10-publication`, head `6e8beb84fa0816a16e46a46500502320e95df362` (one commit on top of the audited `e06625f`)
- Date: 2026-11-14

## Verdict: PASS — approved for merge after Claude's co-sign (2026-11-15)

Both fixes are implemented correctly and verified live. **M10 is a
server change** — after the merge, the VM gets its first deploy since
M7 via the new "Updating to M10" section (backup → audited commit →
nginx site reinstall → verification).

## What I checked

- Fetched `m10-publication`: head `6e8beb8` matches the reported hash,
  its parent is the audited `e06625f`, PR #10 still open and not merged
  (GitHub API). The commit touches 9 files — server.py (the R1 fix),
  nginx conf (R2), SPEC/README/DEPLOY, tests — and does NOT yet include
  `docs/audits/AUDIT-M10.md` (goes in the usual small PR; pre-approved
  for that file alone, per the standing pattern).
- **R1 (Host policy on /status)** — implementation read line by line:
  - `_allowed_status_hosts(settings)` builds the accepted list from the
    same `LOOPBACK_HOSTS` (`127.0.0.1:*`, `localhost:*`, `[::1]:*`) plus
    `settings.allowed_hosts` — the **same list the transport builds**
    (`transport_security_settings`, verified in source), so the claim
    "same Host policy as the transport" holds.
  - `_host_allowed` is case-insensitive, rejects a missing header, and
    `:*` patterns match both the bare host and `host:<port>` — so a
    browser's `Host: 127.0.0.1:8000` works while
    `evil.example.com:443` does not. (Edge: bare vs port forms — the
    SDK's own default rejects bare loopback Hosts; /status accepts
    them. Bare loopback can only come from the same machine, so this
    is safe; noted for completeness.)
  - The check runs **before** the aggregates and **before** the rate
    limit: rejected Hosts consume no budget.
  - A rejected Host is answered **404 `{"error":"not found"}`** with no
    detail — exactly as claimed.
- **Independent live probe (my own script v2)** — two real servers:
  - Server 1 (no `ROUNDTABLE_ALLOWED_HOSTS`): `evil.example.com`,
    a LAN IP, and an attacker domain with port → **404, body exactly
    `{"error":"not found"}`, no marker, no detail** (14/14 behavioural
    passes; three probe-assertion failures were the Auditor's own
    string-matching bug — the probe compared against pretty-printed
    JSON with a space, the server emits compact JSON);
    `Host: 127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` and
    the default Host → 200; content-leak regression clean (no marker,
    no agent names); 70 rejected-Host requests consumed **no**
    rate-limit budget (a following valid request still 200); `/mcp`
    gate regression clean (401 without token, with token passes).
  - Server 2 (`ROUNDTABLE_ALLOWED_HOSTS=example.com`): `example.com` →
    **200**, `evil.example.com` → 404, loopback → 200 — the shared-list
    behaviour proven live.
- **R2 (own nginx zone)**: `limit_req_zone $binary_remote_addr
  zone=status:10m rate=20r/s;` defined at http level; the `/status`
  location uses `zone=status burst=20 nodelay`; the `/mcp` location
  keeps `zone=mcp` — a flood on the unauthenticated page can no longer
  crowd out `/mcp` traffic.
- **SPEC §7 item 11 / README / DEPLOY**: item 11 now says the endpoint
  "applies the same Host policy as the transport… 404 with no detail";
  DEPLOY's "Updating to M10" states the M9 backup timer and M8 worker
  service are independent of this update (not installed, not changed).
- **Suite on the exact head**, fresh checkout at `6e8beb8`, WSL Ubuntu
  24.04 (Python 3.12.3, the VM's version): **179 passed** in ~172 s
  (22 M10 tests = 19 + 3 new: forged-host rejection, allowed-hosts
  acceptance, nginx zone). The Builder's fail-before claim for the new
  tests was not independently re-verified (standing note).

## Next step

1. Claude's retrospective review on 2026-11-15 covers this report and
   the previous ones; the Owner merges PR #10 only after the co-signed
   PASS.
2. **After merge: the VM gets its first deploy since M7.** Follow the
   new "Updating to M10" section: back up (Step 43), record the
   version (Step 45), set `AUDITED_COMMIT=6e8beb84fa0816a16e46a46500502320e95df362`,
   run Step 46, **reinstall the nginx site and reload** (the conf now
   exposes /status), verify Steps 24/41 + the new Part 11 item
   (`/status` 200, forged Host 404), reconnect the leader. The
   Auditor provides the step-by-step walkthrough on request.
3. Muse adds `docs/audits/AUDIT-M10-FIXES.md` (this file, unchanged)
   via the usual small PR — pre-approved for that file alone.
