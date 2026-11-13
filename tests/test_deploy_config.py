"""Tests for the deployment deliverables (M3 audit fixes, AUDIT-M3.md).

These tests read docs/DEPLOY.md and the deploy/ templates and assert
the exact commands and values the audit requires. A guide whose key
commands drift is a broken deployment, so the checks live in the suite
next to the code tests. Each test names the audit item it guards.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = (REPO / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
SERVICE = (REPO / "deploy" / "roundtable.service").read_text(encoding="utf-8")
NGINX = (REPO / "deploy" / "nginx" / "roundtable.conf").read_text(encoding="utf-8")
NGINX_HTTP = (
    REPO / "deploy" / "nginx" / "roundtable-http-only.conf"
).read_text(encoding="utf-8")


def section(heading: str) -> str:
    """Return the guide text from one 'Step N' heading to the next.

    Step 0 uses a '##' heading; the rest use '###'. The heading must be
    followed by a newline so 'Step 4' never matches 'Step 41'.
    """
    pattern = re.compile(
        r"##+ " + re.escape(heading) + r"\n(.*?)(?=\n### |\n## |\Z)",
        re.DOTALL,
    )
    match = pattern.search(GUIDE)
    assert match, f"guide has no section {heading!r}"
    return match.group(1)


def test_d1_renewal_reloads_nginx():
    step35 = section("Step 35 — Issue the certificate with certbot (webroot)")
    assert '--deploy-hook "systemctl reload nginx"' in step35
    # After renumbering (D3 inserts a step), the renewal check is step 42.
    step42 = section("Step 42 — Check automatic renewal works")
    # certbot stores --deploy-hook as renew_hook in the renewal config
    # (AUDIT-M3-DEPLOY F5); the grep command must use the stored key.
    # (The prose may still mention deploy_hook to explain the trap.)
    assert "renew_hook" in step42
    command_block = step42.split("```bash")[1].split("```")[0]
    assert "renew_hook" in command_block
    assert "deploy_hook" not in command_block
    assert "/etc/letsencrypt/renewal/" in step42


def test_d2_nginx_starts_at_install_and_reload_loads_the_site():
    step11 = section("Step 11 — Install nginx, certbot, git, venv support and sqlite3")
    assert "starts" in step11 and "at once" in step11
    assert "port 80" in step11
    step30 = section("Step 30 — Load the new site (reload, not enable --now)")
    assert "sudo nginx -t && sudo systemctl reload nginx" in step30
    assert "systemctl is-enabled nginx" in step30
    step3 = section("Step 3 — Confirm ports 80, 443 and 8000 are free")
    assert "before Step 11" in step3


def test_d3_authenticated_request_checked_through_nginx():
    step41 = section("Step 41 — Check an authenticated request through nginx")
    assert 'https://"$PUBLIC_NAME"/mcp' in step41
    assert "400, not 421" in step41
    # And the verification section points to the same check (F1).
    assert "Step 41" in part11_outside()


def part11_outside() -> str:
    """The 'From outside the VM' part of Part 11."""
    start = GUIDE.index("### From outside the VM")
    end = GUIDE.index("### Rate limiting is unchanged", start)
    return GUIDE[start:end]


def test_f1_outside_check_needs_no_vm_file():
    outside = part11_outside()
    # Item 8 used to read /srv/roundtable/agents.json with sudo — a file
    # that exists only on the VM, and the Owner's computer is Windows.
    # No outside check may read it; item 8 now points to Step 41.
    assert "open('/srv/roundtable/agents.json')" not in outside
    assert "TOKEN=$(sudo python3" not in outside
    assert "Step 41" in outside
    # PowerShell 5.1 aliases curl to Invoke-WebRequest, so the flags
    # in the outside checks fail there.
    assert "curl.exe" in outside
    assert "POSIX" in outside


def test_f2_restore_loop_exits_zero_without_shm(tmp_path):
    step44 = section("Step 44 — Restore a backup (only if the database is damaged)")
    match = re.search(r"bash -c '([^']*)'", step44)
    assert match, "Step 44 has no move-aside loop"
    script = match.group(1).replace("/srv/roundtable/data", str(tmp_path))
    (tmp_path / "roundtable.db").write_text("db")
    (tmp_path / "roundtable.db-wal").write_text("wal")
    # No -shm file: the loop must still exit 0 (AUDIT-M3-DEPLOY F2).
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "roundtable.db.before-restore").exists()
    assert (tmp_path / "roundtable.db-wal.before-restore").exists()


def test_f3_step31_uses_resolve():
    step31 = section("Step 31 — Check the redirect works locally")
    # The R1 catch-all on port 80 returns 444 for the bare IP, so the
    # check must connect to 127.0.0.1 while sending the public name.
    # (The prose may still mention the bare-IP curl to explain why.)
    commands = "".join(
        block.split("```")[0]
        for block in step31.split("```bash")[1:]
    )
    assert '--resolve "$PUBLIC_NAME:80:127.0.0.1"' in commands
    assert "http://127.0.0.1/" not in commands
    assert "301" in step31 and "404" in step31


def test_f4_sleep_after_reload():
    # nginx returns from reload before the new workers serve, so a
    # check run immediately afterwards can hit the old config.
    for heading in (
        "Step 30 — Load the new site (reload, not enable --now)",
        "Step 39 — Reload nginx (reload, never restart)",
        "Step 48 — Full uninstall (removes only Roundtable's things)",
    ):
        text = section(heading)
        assert "sleep 2" in text, heading
        assert text.index("systemctl reload nginx") < text.index("sleep 2")


def test_f5_renewal_key_everywhere():
    part11 = GUIDE[GUIDE.index("## Part 11") :]
    assert "renew_hook" in part11
    assert "deploy_hook" not in part11
    # The CLI flag itself is still --deploy-hook (Step 35).
    assert '--deploy-hook "systemctl reload nginx"' in GUIDE


def test_f6_step23_expects_uvicorn_line():
    step23 = section("Step 23 — Read the log and confirm the app is listening")
    # The journal shows Uvicorn's line; the app logs no line of its own.
    assert "Uvicorn running on http://127.0.0.1:8000" in step23
    assert "Roundtable M1 listening" not in step23


def test_d4_timeouts_below_azures_idle_timeout():
    read = re.search(r"proxy_read_timeout (\d+)s;", NGINX)
    send = re.search(r"proxy_send_timeout (\d+)s;", NGINX)
    assert read and send
    assert int(read.group(1)) < 240 and int(send.group(1)) < 240
    assert int(read.group(1)) == 180 and int(send.group(1)) == 180
    assert "4-minute" in NGINX or "4 minute" in NGINX


def test_d5_restore_moves_wal_and_shm_aside():
    step44 = section("Step 44 — Restore a backup (only if the database is damaged)")
    assert "roundtable.db-wal" in step44
    assert "roundtable.db-shm" in step44
    assert "before-restore" in step44
    assert "do not delete" in step44.lower()


def test_d6_package_record_and_selective_undo():
    step6 = section("Step 6 — Confirm nginx and certbot are not installed yet")
    assert "roundtable-packages-before.txt" in step6
    step11 = section("Step 11 — Install nginx, certbot, git, venv support and sqlite3")
    assert "only the packages" in step11
    assert "already installed" in step11


def test_d7_audited_commit_checked_out():
    step0 = section("Step 0 — Set the names you will reuse")
    assert "AUDITED_COMMIT" in step0
    step16 = section("Step 16 — Clone the code")
    assert 'checkout "$AUDITED_COMMIT"' in step16
    step46 = section("Step 46 — Update to the audited version")
    assert 'checkout "$AUDITED_COMMIT"' in step46
    # The step's commands must never pull main; mentioning the ban in
    # the prose is fine, so check only the command block.
    command_block = step46.split("```bash")[1].split("```")[0]
    assert "pull" not in command_block


def test_d8_expected_output_is_literally_true():
    step3 = section("Step 3 — Confirm ports 80, 443 and 8000 are free")
    assert "ss -Htln" in step3
    # Process names are only shown with sudo.
    assert GUIDE.count("sudo ss -tlnp") >= 3
    step0 = section("Step 0 — Set the names you will reuse")
    assert 'test -n "$PUBLIC_NAME"' in step0
    assert 'test -n "$EMAIL"' in step0
    assert 'test -n "$AUDITED_COMMIT"' in step0
    step20 = section("Step 20 — Install the EnvironmentFile and set your public name")
    assert "leaves the value empty" in step20


def test_r1_catch_all_servers_and_correct_host_comment():
    assert "ssl_reject_handshake on;" in NGINX
    assert "return 444;" in NGINX
    assert "listen 443 ssl default_server;" in NGINX
    assert "listen 80 default_server;" in NGINX
    assert "return 444;" in NGINX_HTTP
    # The old, wrong claim about $host must be gone.
    assert "nginx's own server name" not in NGINX
    assert "Host header sent by the client" in NGINX


def test_r2_no_swap_for_the_service():
    assert "MemorySwapMax=0" in SERVICE


def test_r3_token_never_on_the_command_line():
    assert "curl -H @-" in GUIDE
    assert "printf 'Authorization: Bearer %s" in GUIDE
    # No step puts the token on curl's command line any more.
    assert "curl " not in GUIDE or '-H "Authorization: Bearer $TOKEN"' not in GUIDE


def test_r5_uninstall_offers_default_site_and_package_removal():
    step48 = section("Step 48 — Full uninstall (removes only Roundtable's things)")
    assert "sites-available/default" in step48
    assert "apt remove" in step48


def test_guide_step_count_matches():
    # Steps 1-48 as "### Step N" headings, plus "## Step 0" setup.
    assert len(re.findall(r"^### Step \d+", GUIDE, re.M)) == 48
    assert "## Step 0" in GUIDE
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "48 steps" in changelog


def test_m5_updating_section():
    # M5: the "Updating to M5" section backs up first, updates with
    # Step 46 to the audited commit, restarts Roundtable only, and
    # reconnects both apps afterwards.
    updating = section("Updating to M5")
    assert "Step 43" in updating  # back up first
    assert "Step 46" in updating
    assert "AUDITED_COMMIT" in updating
    # "restart Roundtable" wraps across two lines in the guide.
    assert "restart roundtable" in " ".join(updating.split()).lower()
    assert "Reconnect" in updating
    assert "scripts/copy-worker-token.sh" in updating
    # R3: if the migration itself failed, restore the backup too.
    assert "Step 44" in updating


def test_r4_git_bash_entry_in_part11():
    # R4: bare `bash` in PowerShell starts WSL, not Git Bash. The
    # outside checks in Part 11 say how to enter Git Bash.
    outside = section("From outside the VM (your own computer, not the VM)")
    assert "bash.exe" in outside
    assert "WSL" in outside


def test_m6_updating_section():
    # M6: the "Updating to M6" section backs up first, updates with
    # Step 46 to the audited commit, restarts Roundtable only,
    # reconnects both apps, and says how the database grows and how
    # to check its size.
    updating = section("Updating to M6")
    assert "Step 43" in updating  # back up first
    assert "Step 46" in updating
    assert "AUDITED_COMMIT" in updating
    assert "restart roundtable" in " ".join(updating.split()).lower()
    assert "Reconnect" in updating
    assert "scripts/copy-worker-token.sh" in updating
    # R3: if the migration itself failed, restore the backup too.
    assert "Step 44" in updating
    # N6: the size check totals .db, -wal and -shm (plain `du -h`
    # on the .db file alone showed 4.0K on the VM).
    # D1: the glob must expand as roundtable, not as azureuser
    # (the data directory is mode 700): run du under sh -c via
    # sudo, in all three size checks in this section.
    assert updating.count(
        "sudo -u roundtable sh -c 'du -ch "
        "/srv/roundtable/data/roundtable.db*'"
    ) == 3
    assert "sudo -u roundtable du -ch" not in updating
    # M6 audit fix F2: the history cap, the disk-space check, and
    # what to do when the cap is reached.
    assert "ROUNDTABLE_HISTORY_MAX_MB" in updating
    assert "df -h /" in updating
    assert "raise" in updating.lower()
    # N3: a weekly disk check, because the history rows themselves
    # are uncapped (only their arguments are).
    assert "Disk check" in updating
    assert "weekly" in updating.lower()
    assert "10 GB" in updating
    assert "5 GB" in updating


def test_m6_history_cap_in_env_example():
    # F2: the cap ships in the EnvironmentFile template, with its
    # default, so the deployed service has it from the first start.
    env_example = (REPO / "deploy" / "roundtable.env.example").read_text(
        encoding="utf-8"
    )
    assert "ROUNDTABLE_HISTORY_MAX_MB=1024" in env_example
