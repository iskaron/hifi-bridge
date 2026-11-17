"""DEMO STACK, part 1 (real Chromium): the owner sets the bridge up exactly as on a device - setup code from the flash tool's owner sheet,
Bluetooth name, Lyrion found by discovery, room "Wohnzimmer" - and opens the pairing window. Real product WebUI, no shortcuts."""
import os

from playwright.sync_api import expect, sync_playwright

from demo_env import BRIDGE, PASSWORD, SHOTS, STATE_DIR, setup_code

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
