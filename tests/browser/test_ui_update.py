"""BROWSER (real Chromium via Playwright) against the lab appliance with the SIMULATED RAUC service (btkit.fake_rauc, `--sim-rauc`):
the update page (slots, trust, previous version), a bundle upload through update.js with upload progress, the installation with live
RAUC progress, the restart prompt and declining it, the backup page with a real download, German and dark desktop layouts.
Every page: axe-core (no serious/critical violation), no console/CSP errors, no horizontal overflow. Runs after test_ui_browser.py (the
wizard creates the owner account there). Screenshots: reports/screenshots/upd-*.png. RAUC itself is simulated here; the real RAUC
service is exercised in tests/ota (scripts/run_ota.sh)."""
import json
import os
import random
import time

from playwright.sync_api import expect
from test_ui_browser import AUDITS, BASE, DESKTOP, MOBILE, axe_ok, no_overflow, shot  # noqa: F401
from test_ui_browser import chromium, pw  # noqa: F401
from test_ui_network import login


def api(page):
    return json.loads(page.evaluate("fetch('/api/update', {credentials: 'same-origin'}).then(r => r.text())"))


def wait_phase(page, phase, timeout=40):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if api(page)["phase"] == phase:
            return
        time.sleep(0.3)
    raise AssertionError(f"update phase {api(page)} != {phase}")


def test_01_update_page_upload_install_and_decline(chromium):  # noqa: F811
    errors = []
    c, page = login(chromium, errors, **MOBILE)
    page.on("dialog", lambda d: d.accept())
    page.goto(BASE + "/settings")
    expect(page.get_by_role("link", name="Software update")).to_be_visible()
    page.goto(BASE + "/settings/update")
    expect(page.locator("#ota-phase")).to_have_text("No update in progress.")
    expect(page.get_by_text("own update CA only")).to_be_visible()
    expect(page.get_by_text("Version 0.2.0 is still kept in slot B")).to_be_visible()
    no_overflow(page); axe_ok(page, "upd-update"); shot(page, "upd-01-update-mobile-light")

    page.locator('input[name="bundle"]').set_input_files({"name": "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", "mimeType": "application/octet-stream",
                                                          "buffer": random.Random(3).randbytes(3 * 1024 * 1024)})
    with page.expect_navigation():
        page.get_by_role("button", name="Upload and check").click()
    expect(page.locator(".flash")).to_contain_text("Bundle 0.4.0 received and verified.")
    expect(page.get_by_role("button", name="Install version 0.4.0")).to_be_visible()
    no_overflow(page); axe_ok(page, "upd-staged"); shot(page, "upd-02-staged-mobile-light")

    with page.expect_navigation():
        page.get_by_role("button", name="Install version 0.4.0").click()
    expect(page.locator("#ota-progress")).to_be_visible()
    first = page.locator("#ota-progress").get_attribute("value")
    page.wait_for_function("(v) => document.getElementById('ota-progress') && document.getElementById('ota-progress').value != v", arg=first,
                           timeout=10000)                                     # update.js moves the bar live
    no_overflow(page); axe_ok(page, "upd-installing"); shot(page, "upd-03-installing-mobile-light")
    expect(page.get_by_role("button", name="Restart now")).to_be_visible(timeout=30000)   # update.js reloads on the phase change
    expect(page.get_by_text("Version 0.4.0 is installed. Restart now?")).to_be_visible()
    no_overflow(page); axe_ok(page, "upd-installed")

    d, dpage = login(chromium, errors, scheme="dark", **DESKTOP)
    dpage.goto(BASE + "/settings/update")
    no_overflow(dpage); axe_ok(dpage, "upd-installed-dark"); shot(dpage, "upd-04-restart-desktop-dark")
    d.close()

    with page.expect_navigation():
        page.get_by_role("button", name="Restart now").click()                # the lab has no restart helper: a clear error
    expect(page.locator(".flash.error")).to_contain_text("cannot restart itself here")
    with page.expect_navigation():
        page.get_by_role("button", name="Keep the current version").click()
    expect(page.get_by_text("Restart declined")).to_be_visible()
    assert api(page)["phase"] == "idle"
    assert not errors, errors
    c.close()


def test_02_backup_page_download_and_german(chromium, tmp_path):  # noqa: F811
    errors = []
    c, page = login(chromium, errors, **MOBILE)
    page.goto(BASE + "/settings/backup")
    expect(page.get_by_text("Include secrets, encrypted with a passphrase")).to_be_visible()
    expect(page.get_by_text("Snapshot of version")).to_be_visible()             # taken before the installation in test_01
    no_overflow(page); axe_ok(page, "upd-backup"); shot(page, "upd-05-backup-mobile-light")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download backup").click()
    path = os.path.join(str(tmp_path), "backup.json")
    dl.value.save_as(path)
    doc = json.load(open(path))
    assert doc["format"] == "hifi-bridge-backup" and doc["secrets"] is None and doc["settings"]["device_name"]
    c.close()

    g, gpage = login(chromium, errors, **DESKTOP)
    gpage.goto(BASE + "/settings/appearance")
    gpage.locator('select[name="language"]').select_option("de")
    with gpage.expect_navigation():
        gpage.get_by_role("button", name="Save").click()
    gpage.goto(BASE + "/settings/update")
    expect(gpage.locator("h1")).to_have_text("Software-Update")
    expect(gpage.get_by_text("Laufende Version")).to_be_visible()
    no_overflow(gpage); axe_ok(gpage, "upd-update-de"); shot(gpage, "upd-06-update-desktop-light-de")
    gpage.goto(BASE + "/settings/backup")
    no_overflow(gpage); axe_ok(gpage, "upd-backup-de")
    gpage.goto(BASE + "/settings/appearance")
    gpage.locator('select[name="language"]').select_option("auto")
    with gpage.expect_navigation():
        gpage.get_by_role("button", name="Speichern").click()
    assert not errors, errors
    g.close()
