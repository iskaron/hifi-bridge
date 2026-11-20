"""BROWSER (real Chromium via Playwright) against a second fresh lab appliance ("appliance-setup", docker/compose.yml) started like a card from
the flash tool: setup hotspot + country, no home Wi-Fi, no Ethernet - the hotspot-only first setup through the wizard's network step
(DECISIONS #122). REAL network agent, SIMULATED radio (btkit.sim_net): the browser reaches the device over the lab network all the time, so
"losing the hotspot" is simulated by switching from the device's IP address (standing in for http://10.42.0.1/) to its name in a NEW
browser context without cookies, as a phone that rejoined the home Wi-Fi would. Checks: the explanation before Connect, a wrong password
rolled back with its reason (real 60 s fallback time), retry, Keep after a new login at the new address, the wizard continuing; keyboard,
DE/EN, light/dark, phone/desktop widths, axe-core, no console/CSP errors, no horizontal overflow, no password in any page.
Screenshots: reports/screenshots/wiz-*.png."""
import socket
import time

from playwright.sync_api import expect
from test_ui_browser import AUDITS, DESKTOP, MOBILE, PW, axe_ok, new_page, no_overflow, shot, submit, tab_to  # noqa: F401
from test_ui_browser import chromium, pw  # noqa: F401  (fixtures)

CODE = "K7QM-2XPA-RT9H"                                    # tests/browser/setup-provision.json (the flash tool's owner sheet)
NAME_URL = "http://hifi-setup.local:8080"                  # the device's name after the switch
HOTSPOT = "Hi-Fi Bridge Setup"
SECRETS = ("lab-setup-hotspot", "wifi-secret-123", "not-the-password")
STEP = "/setup/network"


def ip_url() -> str:
    return f"http://{socket.gethostbyname('appliance-setup')}:8080"     # stands in for http://10.42.0.1/ on the hotspot


def no_secrets(page):
    html = page.content()
    for s in SECRETS:
        assert s not in html, s


def wait_for(page, url, text, timeout=90):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        page.goto(url)
        if text in page.content():
            return
        time.sleep(1)
    raise AssertionError(f"{text!r} not on {url} after {timeout} s")


def test_01_hotspot_only_setup_through_the_network_step(chromium):  # noqa: F811
    errors = []
    base = ip_url()
    ctx, page = new_page(chromium, errors, **MOBILE)
    page.goto(base + "/")
    expect(page).to_have_url(base + "/setup")
    page.locator('input[name="code"]').fill(CODE)
    page.locator('input[name="password"]').fill(PW)
    page.locator('input[name="confirm"]').fill(PW)
    with page.expect_navigation():
        page.locator('input[name="confirm"]').press("Enter")
    expect(page).to_have_url(base + STEP)                                 # the step right after the owner account
    wait_for(page, base + STEP, "setup hotspot “Hi-Fi Bridge Setup”", timeout=40)       # the hotspot opens ~15 s after start
    explain = page.locator(".net-explain")
    expect(explain).to_contain_text("What happens when you press “Connect”")
    expect(explain.get_by_role("link", name=NAME_URL + "/")).to_be_visible()          # where to find the device afterwards
    expect(explain).to_contain_text(HOTSPOT)
    expect(explain).to_contain_text("http://10.42.0.1/")
    expect(page.get_by_role("link", name="Skip this step")).to_be_visible()
    no_overflow(page); axe_ok(page, "wiz-network-hotspot"); shot(page, "wiz-01-network-hotspot-mobile-light"); no_secrets(page)

    # wrong password: the agent rolls back when the attempt fails (60 s) and the hotspot comes back; the step explains it
    page.get_by_role("radio", name="Home").check()
    page.locator('input[name="password"]').fill("not-the-password")
    tab_to(page, 'form[action="/setup/network/wifi"] button')
    submit(page)                                                          # keyboard: Enter on "Connect"
    wait_for(page, base + STEP, "Previous network settings restored", timeout=100)
    expect(page.locator(".net-trial")).to_contain_text("The Wi-Fi password was not accepted.")
    expect(page.locator(".net-trial")).to_contain_text("hotspot is active again")
    no_overflow(page); axe_ok(page, "wiz-network-failed"); shot(page, "wiz-02-network-failed-mobile-light"); no_secrets(page)

    # retry with the right password; the "hotspot" (this address) is left behind
    page.get_by_role("radio", name="Home").check()
    page.locator('input[name="password"]').fill("wifi-secret-123")
    with page.expect_navigation():
        page.get_by_role("button", name="Connect").click()
    ctx.close()

    # the phone joined the home Wi-Fi and opens the device's name: new address, no cookie -> login -> the wizard waits with "Keep"
    nctx, npage = new_page(chromium, errors, scheme="dark", **MOBILE)
    npage.goto(NAME_URL + "/")
    expect(npage).to_have_url(NAME_URL + "/login")
    npage.locator('input[name="password"]').fill(PW)
    with npage.expect_navigation():
        npage.locator('input[name="password"]').press("Enter")
    expect(npage).to_have_url(NAME_URL + STEP)
    trial = npage.locator(".net-trial")
    expect(trial).to_contain_text("Keep the new network settings?")
    wait_for(npage, NAME_URL + STEP, "The device has joined “Home” as 192.168.1.57", timeout=20)
    first = npage.locator(".net-countdown").inner_text()
    time.sleep(2.2)
    assert npage.locator(".net-countdown").inner_text() != first                  # network.js counts down
    no_overflow(npage); axe_ok(npage, "wiz-network-keep-dark"); shot(npage, "wiz-03-network-keep-mobile-dark"); no_secrets(npage)
    tab_to(npage, 'form[action="/setup/network/confirm"] button'); submit(npage)
    expect(npage).to_have_url(NAME_URL + "/setup/bluetooth")                      # kept: on to the next step
    expect(npage.locator(".flash")).to_contain_text("Network settings kept")
    npage.goto(NAME_URL + STEP)
    expect(npage.locator(".net-online")).to_contain_text("Connected to the Wi-Fi “Home” as 192.168.1.57")
    nctx.storage_state(path="/tmp/wiz-owner.json")
    nctx.close()
    assert not errors, errors


def test_02_the_step_in_german_on_a_desktop_and_light(chromium):  # noqa: F811
    errors = []
    ctx, page = new_page(chromium, errors, storage_state="/tmp/wiz-owner.json", **DESKTOP)
    ctx.add_cookies([{"name": "bab_lang", "value": "de", "url": NAME_URL}])       # this browser's language choice (no owner setting yet)
    page.goto(NAME_URL + STEP)
    expect(page.locator("h1")).to_have_text("Heimnetz")
    expect(page.locator(".net-online")).to_contain_text("Mit dem WLAN „Home“ verbunden als 192.168.1.57")
    page.locator("details.net-more summary").click()                               # "Anderes WLAN verwenden"
    expect(page.locator(".net-explain")).to_contain_text("Was beim Tippen auf „Verbinden“ passiert")
    expect(page.locator(".net-explain")).to_contain_text("verlässt das WLAN Home")
    no_overflow(page); axe_ok(page, "wiz-network-online-de"); shot(page, "wiz-04-network-online-desktop-light-de"); no_secrets(page)
    ctx.close()
    ctx, page = new_page(chromium, errors, storage_state="/tmp/wiz-owner.json", **MOBILE)
    page.goto(NAME_URL + STEP)
    page.keyboard.press("Tab")                                                   # skip link first, then the steps
    assert page.evaluate("document.activeElement.className") == "skip"
    tab_to(page, "details.net-more summary")
    page.keyboard.press("Enter")                                                 # <details> opens from the keyboard (no JavaScript needed)
    expect(page.locator('details.net-more form[action="/setup/network/wifi"]')).to_be_visible()
    no_overflow(page); shot(page, "wiz-05-network-other-wifi-mobile-light")
    ctx.close()
    assert not errors, errors
