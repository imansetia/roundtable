# Audit report — PR #3 "M6 notes (N3–N6)"

- Auditor: Claude
- Audited: PR imansetia/roundtable#3, branch `chore/m6-notes`, head `d16c2ee8b698208f32499f7c202843b3838b2ae9` (one commit, parent `33c2f75` = current `main`)
- Date: 2026-11-13

## Verdict: CHANGES REQUESTED — one guide bug; do not merge yet

N4 and N5 are correct. N3 is the right routine. But the N6 command does not work as written on the VM, and it now appears in every size check, including the new weekly routine. Fix D1, push to the same branch, and report the new head hash.

## What I checked

- Fetched `chore/m6-notes` from GitHub. Head `d16c2ee` matches the reported hash, its parent is `main` (`33c2f75`), and PR #3 is open and not merged. `main` reports `protected: true` on the GitHub API.
- Read the whole diff (`board.py`, `test_worker.py`, `test_m6_activity_history.py`, `test_deploy_config.py`, DEPLOY, CHANGELOG).
- N5: confirmed in SQLite that `LENGTH(CAST(x AS BLOB))` counts bytes (10 × "€" → 30) and that NULL rows sum to 0.
- **The full suite could not be re-run by the Auditor this time.** The Auditor's container network is IPv6-only right now (NAT64 on the mobile link), so apt and PyPI were unreachable. The code change is one SQL expression and one test timeout. The suite will be re-run on the fixed head before merge.

## Required fix

| # | Finding | Fix |
|---|---|---|
| D1 | **`sudo -u roundtable du -ch /srv/roundtable/data/roundtable.db*` fails on the VM.** The `*` is expanded by `azureuser`'s shell before `sudo` runs, but `azureuser` cannot read `/srv/roundtable/data` (mode 700, owner `roundtable`). The glob therefore matches nothing, `du` receives the literal `roundtable.db*`, and it reports "No such file or directory". | Let the service user's shell expand it: `sudo -u roundtable sh -c 'du -ch /srv/roundtable/data/roundtable.db*'`. Apply this everywhere (Updating to M6, Disk check), and update the guard in `tests/test_deploy_config.py`. |

## Notes (not blocking)

- **N7. The scripts lost their executable bit** (`100755 → 100644` for all three `scripts/*.sh`). This is a side effect of uploading through the connector. The guide always runs them with `bash …` or `source …`, so nothing breaks, but it is an unintended change. Restore the mode when a git CLI push is possible (`git update-index --chmod=+x scripts/*.sh`), or state it in the CHANGELOG.
- **N8. The commit is authored by the Owner's GitHub account** (`imansetia`), because Muse's GitHub connector acts as the Owner. Branch protection must therefore have **"Do not allow bypassing the above settings"** enabled, or the Owner's own admin rights (and so Muse) can skip the pull request. The Owner should confirm that setting. In the PR body or the CHANGELOG, say which commits Muse made through the connector.
- The single-commit squash of Muse's two local commits (`87ba664`, `20b9377`) is acceptable; the Auditor reviews the pushed head.

## Next step

Muse fixes D1 on `chore/m6-notes` (a new commit on the same branch), and reports the new head hash. The Auditor fetches it, re-runs the suite, and checks D1. The Owner then merges PR #3 with **"Create a merge commit"**.
