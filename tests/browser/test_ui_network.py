"""BROWSER (real Chromium via Playwright) against the lab appliance with the REAL network agent and the SIMULATED radio (btkit.sim_net):
setting country and setup hotspot, the hotspot fallback, the Wi-Fi scan list, joining a network with the confirm/undo banner and its live
countdown, the address page (fixed-address fields shown only when needed), DE/EN, light/dark, phone/desktop, an automated WCAG audit
(axe-core), no console/CSP errors, no horizontal overflow, and no password anywhere in a page. Runs after test_ui_browser.py (the wizard
creates the owner account there). Screenshots: reports/screenshots/net-*.png."""
import json
import os
import time
import urllib.request

from playwright.sync_api import expect
from test_ui_browser import AUDITS, BASE, DESKTOP, MOBILE, PW, axe_ok, new_page, no_overflow, shot  # noqa: F401  (fixtures below)
from test_ui_browser import chromium, pw  # noqa: F401

NET = os.environ.get("NET_URL", "http://appliance:8098")
HOTSPOT_PASS = "lab-hotspot-pass"
WIFI_PASS = "wifi-secret-123"


def login(browser, errors, **ctx):
    c, page = new_page(browser, errors, **ctx)
    page.goto(BASE + "/login")
    page.locator('input[name="password"]').fill(PW)
    with page.expect_navigation():
        page.locator('input[name="password"]').press("Enter")
    return c, page


def api(page):
    return json.loads(page.evaluate("fetch('/api/network', {credentials: 'same-origin'}).then(r => r.text())"))


def wait_state(page, state, timeout=40):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if api(page)["state"] == state:
            return
        time.sleep(0.5)
    raise AssertionError(f"network state {api(page)} != {state}")


def no_passwords(page):
    html = page.content()
    assert HOTSPOT_PASS not in html and WIFI_PASS not in html


def test_01_country_and_hotspot_then_fallback_and_scan_list(chromium):  # noqa: F811
    urllib.request.urlopen(NET + "/ethernet/off", timeout=5).read()
    errors = []
    ctx, page = login(chromium, errors, **MOBILE)
    page.goto(BASE + "/settings")
    expect(page.get_by_role("link", name="Network")).to_be_visible()
    page.goto(BASE + "/settings/network/hotspot")
    expect(page.locator("h1")).to_have_text("Setup hotspot and country")
    assert "Germany (DE)" in page.locator('select[name="country"]').inner_text()          # names from Intl.DisplayNames (network.js)
    page.locator('select[name="country"]').select_option("DE")
    page.locator('input[name="hotspot_ssid"]').fill("Hi-Fi Bridge Lab")
    page.locator('input[name="hotspot_password"]').fill(HOTSPOT_PASS)
    with page.expect_navigation():
        page.get_by_role("button", name="Save").first.click()
    expect(page.locator(".flash")).to_contain_text("saved")
    no_overflow(page); axe_ok(page, "net-hotspot"); shot(page, "net-04-hotspot-mobile-light"); no_passwords(page)
    wait_state(page, "hotspot")                                                            # no uplink: the hotspot opens after ~15 s
    page.goto(BASE + "/settings/network")
    expect(page.get_by_text("Setup hotspot active").first).to_be_visible()
    expect(page.get_by_role("radio", name="Home")).to_be_visible()
    expect(page.get_by_role("radio", name="Cafe Gast")).to_be_disabled()                  # open network: not supported
    no_overflow(page); axe_ok(page, "net-network-hotspot"); shot(page, "net-01-network-hotspot-mobile-light"); no_passwords(page)
    ctx.close()
    assert not errors, errors


def test_02_join_with_confirmation_banner_and_live_countdown(chromium):  # noqa: F811
    errors = []
    ctx, page = login(chromium, errors, **MOBILE)
    page.goto(BASE + "/settings/network")
    page.on("dialog", lambda d: d.accept())                                                # the "hotspot will close" confirmation
    page.get_by_role("radio", name="Home").check()
    page.locator('input[name="password"]').fill(WIFI_PASS)
    with page.expect_navigation():
        page.get_by_role("button", name="Connect").click()
    banner = page.locator(".net-trial")
    expect(banner).to_contain_text("Keep the new network settings?")
    first = page.locator(".net-countdown").inner_text()
    time.sleep(2.2)
    assert page.locator(".net-countdown").inner_text() != first                            # network.js counts down
    wait_state(page, "connected", timeout=20)
    page.reload()
    expect(page.get_by_text("Wi-Fi connected").first).to_be_visible()
    no_overflow(page); axe_ok(page, "net-trial"); no_passwords(page)
    dctx, dpage = login(chromium, errors, scheme="dark", **MOBILE)
    dpage.goto(BASE + "/settings/network")
    expect(dpage.locator(".net-trial")).to_be_visible()
    axe_ok(dpage, "net-trial-dark"); shot(dpage, "net-02-network-trial-mobile-dark")
    dctx.close()
    with page.expect_navigation():
        page.get_by_role("button", name="Keep settings").click()
    expect(page.locator(".flash")).to_contain_text("Network settings kept")
    expect(page.locator(".net-trial")).to_have_count(0)
    shot(page, "net-03-network-connected-mobile-light")
    ctx.close()
    assert not errors, errors


def test_03_addresses_page_shows_fixed_fields_only_when_needed(chromium):  # noqa: F811
    errors = []
    ctx, page = login(chromium, errors, **MOBILE)
    page.goto(BASE + "/settings/network/addresses")
    field = page.locator('input[name="ethernet_ipv4_address"]')
    expect(field).to_be_hidden()                                                            # DHCP: no fixed-address fields
    page.locator('select[name="ethernet_ipv4_method"]').select_option("static")
    expect(field).to_be_visible()
    no_overflow(page); axe_ok(page, "net-addresses"); shot(page, "net-05-addresses-mobile-light")
    ctx.close()
    assert not errors, errors


def test_04_desktop_dark_and_german(chromium):  # noqa: F811
    errors = []
    ctx, page = login(chromium, errors, scheme="dark", **DESKTOP)
    page.goto(BASE + "/settings/network/hotspot")
    no_overflow(page); axe_ok(page, "net-hotspot-desktop-dark"); shot(page, "net-06-hotspot-desktop-dark"); no_passwords(page)
    page.goto(BASE + "/settings/appearance"); page.get_by_label("Language").select_option("de")
    with page.expect_navigation():
        page.get_by_role("button", name="Save").click()
    try:
        lctx, lpage = login(chromium, errors, **DESKTOP)
        lpage.goto(BASE + "/settings/network")
        expect(lpage.locator("h1")).to_have_text("Netzwerk")
        expect(lpage.get_by_text("WLAN verbunden").first).to_be_visible()
        no_overflow(lpage); axe_ok(lpage, "net-network-desktop-de"); shot(lpage, "net-07-network-desktop-light-de"); no_passwords(lpage)
        lpage.goto(BASE + "/settings/network/hotspot")
        assert "Deutschland (DE)" in lpage.locator('select[name="country"]').inner_text()
        lctx.close()
    finally:
        page.goto(BASE + "/settings/appearance"); page.get_by_label("Sprache").select_option("en")
        with page.expect_navigation():
            page.get_by_role("button", name="Speichern").click()
    ctx.close()
    assert not errors, errors
