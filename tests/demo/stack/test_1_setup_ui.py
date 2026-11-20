"""DEMO STACK, part 1 (real Chromium): the owner sets the bridge up exactly as on a device - setup code from the flash tool's owner sheet,
Bluetooth name, Lyrion found by discovery, room "Wohnzimmer" - and opens the pairing window. Real product WebUI, no shortcuts."""
import os

from playwright.sync_api import expect, sync_playwright

from demo_env import BRIDGE, PASSWORD, SHOTS, STATE_DIR, setup_code
from demo_env import CONSOLE as CONSOLE_URL

MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)


def test_owner_setup_and_pairing_window():
    os.makedirs(SHOTS, exist_ok=True); os.makedirs(STATE_DIR, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="de-DE", **MOBILE)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BRIDGE + "/")
        expect(page).to_have_url(BRIDGE + "/setup")
        page.screenshot(path=f"{SHOTS}/demo-01-bridge-setup.png", full_page=True)
        page.fill('input[name="code"]', setup_code())
        page.fill('input[name="password"]', PASSWORD)
        page.fill('input[name="confirm"]', PASSWORD)
        with page.expect_navigation():
            page.press('input[name="confirm"]', "Enter")
        expect(page).to_have_url(BRIDGE + "/setup/bluetooth")
        page.fill('input[name="device_name"]', "Hi-Fi Bridge Wohnzimmer")
        with page.expect_navigation():
            page.press('input[name="device_name"]', "Enter")
        expect(page).to_have_url(BRIDGE + "/setup/lyrion")
        with page.expect_navigation():
            page.locator("form.server button.choice").first.click()            # real UDP discovery of the demo's Lyrion
        expect(page).to_have_url(BRIDGE + "/setup/target")
        page.locator("label", has_text="Wohnzimmer").locator('input[name="target_player"]').check()
        page.screenshot(path=f"{SHOTS}/demo-02-bridge-room.png", full_page=True)
        with page.expect_navigation():
            page.locator("form button.primary").last.click()
        expect(page).to_have_url(BRIDGE + "/setup/finish")
        with page.expect_navigation():
            page.locator('form[action="/setup/finish"] button').click()
        expect(page).to_have_url(BRIDGE + "/")
        page.goto(BRIDGE + "/pairing")
        with page.expect_navigation():
            page.locator('form[action="/pairing/open"] button').click()
        expect(page.locator('form[action="/pairing/close"]')).to_be_visible()
        page.screenshot(path=f"{SHOTS}/demo-03-bridge-pairing-open.png", full_page=True)
        ctx.storage_state(path=f"{STATE_DIR}/owner-session.json")
        b.close()
    assert not errors, errors


def test_network_and_update_pages_run_against_the_simulated_backends():
    """The product's Network and Software update pages work in the demo: the REAL network agent drives a SIMULATED Wi-Fi radio and a
    SIMULATED RAUC answers on the private bus. The pages say nothing about the simulation (the product is unchanged), so the demo
    console labels it; this test proves that the product's pages are connected to the back-ends, not that anything is real."""
    import json
    import re
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="en-US", storage_state=f"{STATE_DIR}/owner-session.json", **MOBILE)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BRIDGE + "/settings")
        expect(page.get_by_role("link", name="Network")).to_be_visible()
        expect(page.get_by_role("link", name="Software update")).to_be_visible()
        net = json.loads(page.evaluate("fetch('/api/network', {credentials: 'same-origin'}).then(r => r.text())"))
        assert net["state"] not in ("agent_unreachable", None), net  # the (simulated-radio) agent answers the bridge's status request
        page.goto(BRIDGE + "/settings/network")
        expect(page.locator("h1")).to_be_visible()
        assert "network service of the device is not reachable" not in page.content()
        page.screenshot(path=f"{SHOTS}/demo-bridge-network-simulated.png", full_page=True)
        page.goto(BRIDGE + "/settings/update")
        expect(page.locator("#ota-phase")).to_have_text("No update in progress.")
        expect(page.get_by_text("Version 0.2.0 is still kept in slot B")).to_be_visible()      # reported by the simulated RAUC
        page.screenshot(path=f"{SHOTS}/demo-bridge-update-simulated.png", full_page=True)
        page.goto(CONSOLE_URL + "/")
        expect(page.locator("#simulated-backends")).to_contain_text(re.compile("simulated|simuliert", re.I))
        b.close()
    assert not errors, errors
