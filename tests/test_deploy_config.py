"""Tests for the deployment deliverables (M3 audit fixes, AUDIT-M3.md).

These tests read docs/DEPLOY.md and the deploy/ templates and assert
the exact commands and values the audit requires. A guide whose key
commands drift is a broken deployment, so the checks live in the suite
next to the code tests. Each test names the audit item it guards.
"""

from __future__ import annotations

import re
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
    assert "deploy_hook" in step42
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
    assert "AUDIT-M3 D3" not in step41  # the step explains itself, no audit ref
    # And the verification section has the same check.
    assert GUIDE.count("400, not 421") >= 2


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
