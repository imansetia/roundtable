"""Guards for M9 operations & data safety: the automatic daily
backup (service + timer + script), the disk guard, retention of
backup FILES only, and the DEPLOY.md section. Docs, scripts and
deploy templates only — no server (roundtable/) changes, the
worker service untouched, nothing touches Hermes. The script is
run for real in the tests, against temporary databases.
"""

from __future__ import annotations

import datetime
import os
import re
import sqlite3
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SERVICE = (ROOT / "deploy" / "roundtable-backup.service").read_text(
    encoding="utf-8"
)
TIMER = (ROOT / "deploy" / "roundtable-backup.timer").read_text(
    encoding="utf-8"
)
SCRIPT = ROOT / "deploy" / "roundtable-backup.sh"
SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")


def flat(text: str) -> str:
    """One line per paragraph: prose phrases must not depend on where
    the guide wraps its lines, and blockquote markers must not split
    a phrase. Code blocks are checked on raw text."""
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    return re.sub(r"\s+", " ", text)


DEPLOY_FLAT = flat(DEPLOY)


def section(text: str, heading: str) -> str:
    """Return the text from one '## <heading> ...' to the next '##'."""
    pattern = re.compile(
        r"## " + re.escape(heading) + r"[^\n]*\n(.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"no section {heading!r}"
    return match.group(1)


def _make_db(tmp_path):
    """A tiny database and an empty backup dir, in a temp folder."""
    db_path = tmp_path / "roundtable.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()
    return db_path, backup_dir


def _run(db_path, backup_dir, extra_env=None):
    """Run the backup script against the temp database. The script
    is invoked through bash so the test does not depend on the
    file's executable bit (GitHub snapshots carry none)."""
    env = dict(os.environ)
    env["ROUNDTABLE_BACKUP_DB"] = str(db_path)
    env["ROUNDTABLE_BACKUP_DIR"] = str(backup_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_backup_service_template():
    # The service runs the backup job as the roundtable user. It
    # has no [Install] section on purpose: the timer starts it,
    # and it must never also run once at every boot.
    assert "Type=oneshot" in SERVICE
    assert "User=roundtable" in SERVICE
    assert "ExecStart=/srv/roundtable/backup.sh" in SERVICE
    assert "WantedBy=multi-user.target" not in SERVICE
    assert "MemoryMax=128M" in SERVICE
    assert "OOMScoreAdjust=600" in SERVICE


def test_backup_timer_template():
    # Daily at midnight; Persistent so a missed run (VM off)
    # catches up on the next boot instead of being skipped.
    assert "OnCalendar=daily" in TIMER
    assert "Persistent=true" in TIMER
    assert "Unit=roundtable-backup.service" in TIMER
    assert "WantedBy=timers.target" in TIMER


def test_backup_script_runs_step43_command():
    # The service runs DEPLOY.md Step 43's command: sqlite3
    # .backup as the roundtable user, into /srv/roundtable/backups,
    # mode 600. The paths are the production defaults; the tests
    # override them through the environment.
    assert 'sqlite3 "$DB" ".backup $dest"' in SCRIPT_TEXT
    assert "chmod 600" in SCRIPT_TEXT
    assert "/srv/roundtable/data/roundtable.db" in SCRIPT_TEXT
    assert "/srv/roundtable/backups" in SCRIPT_TEXT


def test_backup_creates_a_usable_backup(tmp_path):
    db_path, backup_dir = _make_db(tmp_path)
    proc = _run(db_path, backup_dir)
    assert proc.returncode == 0, proc.stderr
    backups = sorted(backup_dir.glob("roundtable-*.db"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    # The backup is a usable database with the same rows.
    conn = sqlite3.connect(backups[0])
    assert conn.execute("SELECT v FROM t").fetchone() == ("hello",)
    conn.close()
    assert "wrote" in proc.stdout


def test_backup_retention_keeps_14_daily_and_4_weekly(tmp_path):
    # 40 days of pre-made backup files, then one real run (which
    # adds today's file). Survivors: the newest 14 days, plus at
    # most one file per ISO week for 4 weeks beyond that.
    db_path, backup_dir = _make_db(tmp_path)
    today = datetime.date.today()
    for back in range(1, 41):
        day = today - datetime.timedelta(days=back)
        (backup_dir / f"roundtable-{day.isoformat()}.db").write_text(
            "old", encoding="utf-8"
        )
    proc = _run(db_path, backup_dir)
    assert proc.returncode == 0, proc.stderr
    survivors = sorted(backup_dir.glob("roundtable-*.db"))
    names = {p.name for p in survivors}
    # The newest 14 days all survive (today's file plus 13 older).
    for back in range(14):
        day = today - datetime.timedelta(days=back)
        assert f"roundtable-{day.isoformat()}.db" in names
    # Beyond the daily window: at most 4 weekly files, one per
    # ISO week, and at least one (the weekly keep works).
    weekly = [
        p
        for p in survivors
        if p.name < f"roundtable-{(today - datetime.timedelta(days=13)).isoformat()}.db"
    ]
    assert 1 <= len(weekly) <= 4
    weeks = {
        datetime.date.fromisoformat(p.name[11:21]).isocalendar()[:2]
        for p in weekly
    }
    assert len(weeks) == len(weekly)  # one file per week, at most
    # The oldest file is gone, and nothing foreign was created.
    assert f"roundtable-{(today - datetime.timedelta(days=40)).isoformat()}.db" not in names
    assert len(survivors) <= 18


def test_backup_disk_guard_skips_below_5gb(tmp_path):
    # A minimum no real disk can meet: the run must skip, delete
    # nothing, and say why — loudly, on stderr (the journal).
    db_path, backup_dir = _make_db(tmp_path)
    old = backup_dir / "roundtable-2026-01-01.db"
    old.write_text("keep me", encoding="utf-8")
    proc = _run(
        db_path,
        backup_dir,
        {"ROUNDTABLE_BACKUP_MIN_FREE_BYTES": "999999999999999"},
    )
    assert proc.returncode == 0
    assert "WARNING" in proc.stderr
    assert "skipping" in proc.stderr
    assert sorted(backup_dir.glob("roundtable-*.db")) == [old]


def test_backup_fail_safe_missing_db(tmp_path):
    # A missing database is a failure, not a skip: non-zero exit,
    # an error that says why, and nothing deleted.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "roundtable-2026-01-01.db"
    old.write_text("keep me", encoding="utf-8")
    proc = _run(tmp_path / "no-such.db", backup_dir)
    assert proc.returncode != 0
    assert "ERROR" in proc.stderr
    assert sorted(backup_dir.glob("roundtable-*.db")) == [old]


def test_backup_leaves_foreign_files_alone(tmp_path):
    # Files that are not our dated backups are never deleted:
    # a foreign file, and a file with our name but a date that
    # cannot be parsed (its age is unknown, so it is kept).
    db_path, backup_dir = _make_db(tmp_path)
    foreign = backup_dir / "notes.txt"
    foreign.write_text("not a backup", encoding="utf-8")
    weird = backup_dir / "roundtable-2026-13-45.db"
    weird.write_text("bad date", encoding="utf-8")
    today = datetime.date.today()
    for back in range(1, 21):
        day = today - datetime.timedelta(days=back)
        (backup_dir / f"roundtable-{day.isoformat()}.db").write_text(
            "old", encoding="utf-8"
        )
    proc = _run(db_path, backup_dir)
    assert proc.returncode == 0, proc.stderr
    assert foreign.read_text(encoding="utf-8") == "not a backup"
    assert weird.exists()


def test_deploy_backups_section():
    # The guide's new section: what runs when, where files land,
    # restore is still Step 44, how to disable the timer — and
    # Step 43 no longer says there is no automatic schedule.
    part = section(DEPLOY, "Backups now run automatically")
    part_flat = flat(part)
    assert "roundtable-backup.timer" in part
    assert "OnCalendar=daily" in part or "daily at midnight" in part_flat
    assert "Persistent" in part
    assert "/srv/roundtable/backups" in part
    assert "Step 44" in part
    assert "disable the timer" in part_flat
    assert "5 GB" in part_flat
    assert "open Owner decision" in part_flat  # history pruning
    assert "no automatic schedule yet" not in DEPLOY


def test_changelog_m9_records_retention_decision():
    # The retention policy is recorded as an Owner decision, for
    # backup FILES only; history rows stay append-only.
    assert "## M9" in CHANGELOG
    assert "14 daily" in CHANGELOG
    assert "4 weekly" in CHANGELOG
    assert "append-only" in CHANGELOG
    assert "NEVER touched" in CHANGELOG


def test_m9_scope_guard():
    # No server behaviour change, the worker service untouched,
    # nothing touches Hermes; history-row pruning stays open.
    assert "No server behaviour change" in CHANGELOG
    assert "worker service is untouched" in CHANGELOG
    assert "open Owner decision" in CHANGELOG


def test_readme_status_m9():
    # M9 is merged (PR #8 + the AUDIT-M9 notes PR #9); the status
    # line moved on with M10. The count and the breakdown entry
    # still pin the M9 contribution.
    assert "M9 merged" in README
    assert "179 tests pass" in README
    assert "12 M9 backup" in README
