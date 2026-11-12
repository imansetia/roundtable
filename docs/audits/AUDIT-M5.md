# Audit report — Roundtable M5 (permanent review history)

- Auditor: Claude
- Audited: bundle `roundtable-m5.bundle` (`refs/heads/main`), commits `98d0517` (G1–G9, audited in AUDIT-M4-LIVE-FIXES.md), `4c45f2d` (H1–H2), `c711237` (M5) on top of `a7b5cd4`
- Date: 2026-11-12

## Verdict: PASS — M5 is approved for deployment

**Audited commit for the update (`AUDITED_COMMIT`): `c7112373e9c4dc3be34d3a6b55c447d01d7831a8`.**

The review history is written in the same transaction as the status change, review messages cannot be forged, and an M4 database migrates cleanly. H1–H2 are fixed. The three recommendations below are not blocking.

## What I checked

- Read the full diff since `98d0517`: `board.py`, `db.py`, `server.py`, SPEC, DEPLOY, `.gitattributes`, tests.
- Suite from a real `git clone -b main` of the bundle, in Ubuntu 24.04.5: **103 passed**.
- An independent live probe (my own script, not Muse's tests). I built a database with the **M4 code** (task reviewed and rejected, one message), then started the **real M5 server** on it and drove it over MCP:

| Probe | Result |
|---|---|
| Migration of the M4 database | CHECK now allows `review`, `decision` column added, old message kept (id 1, decision NULL), sequence = 1 |
| `post_message` kind `review` as worker / as leader | **Rejected**: "kind 'review' is reserved for the server" |
| Kind variants `Review`, ` review`, `review\n` (both agents) | **Rejected** (unknown kind) |
| `post_message` with an extra `decision="approved"` argument | Ignored: stored as a plain `note`, decision NULL |
| Reject with note, then approve with no note | Two review messages in order: (`lead`, `rejected`, "reject two"), (`lead`, `approved`, empty body); task `done` |
| Oversized note (4,001 chars) | Rejected; task stays `submitted`; **no review message stored** |
| Worker calls `review_task` | Rejected (leader only); nothing stored |
| Audit log | One `review_task` row per call (two successes, two errors) |
| `get_task` | Review messages appear inside the untrusted-content wrapper, with `decision` as a separate field |

- SPEC §5/§6/§9/§10 match the behaviour. DEPLOY "Updating to M5" says back up first, restart only Roundtable, and reconnect both apps.
- H1 (CHANGELOG states the Owner's decision correctly) and H2 (`.gitattributes` `*.sh text eol=lf`, an LF test, and `BatchMode=yes`) are done.

## Recommended (not blocking; next commit)

- **R1. Make the migration crash-safe.** `CREATE TABLE messages_new` runs before the transaction that copies and renames. If the process died between the two, the next start would fail on "table messages_new already exists". Use `DROP TABLE IF EXISTS messages_new` first, or run the whole rebuild in one explicit `BEGIN … COMMIT`. The backup in the update steps covers this case for now.
- **R2. `test_scripts_are_lf_only` needs a git checkout.** In an export without `.git` it fails with `FileNotFoundError`. Skip that check (with a reason) when `git` or `.git` is missing, and keep the working-tree check.
- **R3. Rollback note.** "Updating to M5" points to Step 47 for rollback. Add: if the migration itself failed, also restore the backup (Step 44). M4 code runs fine on a migrated table, so a rollback without a restore is only needed for code problems.

## Open question (unchanged)

Should superseded results (e.g. a rejected submission) also be kept? Not built, as instructed.

## Next step

1. The Owner approves: fast-forward `main` to `c711237` and push, then add this report to `docs/audits/`.
2. Update HermesVM with DEPLOY "Updating to M5", together with the Owner, each command approved. Hermes is not touched.
3. Reconnect the leader, and give Muse the current worker token.
