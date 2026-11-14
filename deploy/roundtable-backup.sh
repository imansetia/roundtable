#!/usr/bin/env bash
# Roundtable daily backup (M9). Run by roundtable-backup.service
# (daily, via roundtable-backup.timer) as the roundtable user.
#
# What it does, in order:
#   1. Disk guard: skip (exit 0, loud warning) when less than
#      $MIN_FREE_BYTES is free on / — a backup must never be the
#      thing that fills the disk Hermes shares.
#   2. Back up with DEPLOY.md Step 43's command: sqlite3 .backup,
#      which writes a consistent copy even while the server runs.
#      The new file is mode 600.
#   3. Retention, backup FILES only: keep the newest $KEEP_DAILY
#      daily files, plus the newest file of each of the last
#      $KEEP_WEEKLY ISO weeks; delete older files with our date
#      name. Anything else in the directory is left alone.
#
# Fail safe: on any error, nothing is deleted and the reason is
# printed (it lands in the journal). History rows (audit_log,
# submissions, messages) are append-only and are NEVER touched
# by this script — retention is about backup files only.
#
# All paths and limits come from the environment (defaults are
# the production values) so the suite can run this script
# against a temporary database.

set -euo pipefail

DB="${ROUNDTABLE_BACKUP_DB:-/srv/roundtable/data/roundtable.db}"
DIR="${ROUNDTABLE_BACKUP_DIR:-/srv/roundtable/backups}"
# 5 GiB — DEPLOY.md's "below 5 GB, act now" threshold, as df -h
# reports it (the M6 disk check).
MIN_FREE_BYTES="${ROUNDTABLE_BACKUP_MIN_FREE_BYTES:-5368709120}"
KEEP_DAILY="${ROUNDTABLE_BACKUP_KEEP_DAILY:-14}"
KEEP_WEEKLY="${ROUNDTABLE_BACKUP_KEEP_WEEKLY:-4}"

# 1. Disk guard ------------------------------------------------------------
free_bytes="$(df --output=avail -B1 / | tail -n 1 | tr -d ' ')"
if [ "$free_bytes" -lt "$MIN_FREE_BYTES" ]; then
    echo "roundtable-backup: WARNING: only $free_bytes bytes free on /, below the $MIN_FREE_BYTES-byte minimum — skipping this backup, deleting nothing." >&2
    exit 0
fi

# 2. The database must exist and be readable ------------------------------
if [ ! -r "$DB" ]; then
    echo "roundtable-backup: ERROR: database $DB is missing or unreadable — backup failed, deleting nothing." >&2
    exit 1
fi
mkdir -p "$DIR"

# 3. Back up (DEPLOY.md Step 43) -------------------------------------------
today="$(date +%F)"
dest="$DIR/roundtable-$today.db"
sqlite3 "$DB" ".backup $dest"
chmod 600 "$dest"

# 4. Retention (backup FILES only) -----------------------------------------
# Only files named roundtable-YYYY-MM-DD.db are ever pruned;
# anything else in the directory is left alone. The names sort
# by date, newest first.
mapfile -t files < <(ls "$DIR"/roundtable-*.db 2>/dev/null | sort -r || true)
ours=()
for f in "${files[@]}"; do
    case "${f##*/}" in
        roundtable-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].db) ours+=("$f") ;;
    esac
done
keep=()
declare -A seen_week=()
i=0
for f in "${ours[@]}"; do
    i=$((i + 1))
    if [ "$i" -le "$KEEP_DAILY" ]; then
        keep+=("$f")
        continue
    fi
    base="${f##*/}"
    d="${base#roundtable-}"
    d="${d%.db}"
    week="$(date -d "$d" +%G-%V 2>/dev/null || echo "")"
    if [ -z "$week" ]; then
        # A file with our name but a date we cannot parse: we
        # cannot tell its age, so keep it and say so. Never
        # delete what we cannot date.
        echo "roundtable-backup: WARNING: cannot parse the date in $base — keeping it." >&2
        keep+=("$f")
        continue
    fi
    if [ -z "${seen_week[$week]+x}" ] && [ "${#seen_week[@]}" -lt "$KEEP_WEEKLY" ]; then
        seen_week[$week]=1
        keep+=("$f")
    fi
done
for f in "${ours[@]}"; do
    skip=0
    for k in "${keep[@]}"; do
        if [ "$f" = "$k" ]; then skip=1; break; fi
    done
    if [ "$skip" -eq 0 ]; then
        rm -f -- "$f"
        echo "roundtable-backup: pruned old backup $f" >&2
    fi
done

echo "roundtable-backup: wrote $dest"
