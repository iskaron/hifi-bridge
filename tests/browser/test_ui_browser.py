"""BROWSER (real Chromium + Firefox via Playwright) against the appliance container (real service + WebUI, SIMULATED phone, REAL lab LMS).
Checks what HTTP tests cannot: KEYBOARD-ONLY setup, focus visibility, skip link, error focus for screen readers, the accessibility tree,
rendering at phone and desktop widths, light/dark, JavaScript live updates, CSP/console errors, horizontal overflow, an automated WCAG
audit (axe-core), and HTTPS with the device's local CA: trusted by WebKit after installing the CA in the (disposable) container's system
store - the documented Linux trust step - and refused by Chromium, whose NSS store does not have it (no exceptions anywhere).
Screenshots: reports/screenshots/. Not a substitute for a manual screen-reader test or real phone browsers."""
import json
import os
import time
import urllib.request

import pytest
from playwright.sync_api import Error as PwError
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://hifi-bridge.local:8080")
HTTPS = os.environ.get("HTTPS_URL", "https://hifi-bridge.local:8443")
SIM = os.environ.get("SIM_URL", "http://appliance:8099")
SHOTS = "/work/reports/screenshots"
PW = "a good long passphrase"
MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
DESKTOP = dict(viewport={"width": 1280, "height": 860})
AUDITS: dict = {}
STATE: dict = {}


def sim(action):
    with urllib.request.urlopen(f"{SIM}/{action}", timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def pw():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def chromium(pw):
    b = pw.chromium.launch(); yield b; b.close()


def new_page(browser, errors, scheme="light", **ctx):
    c = browser.new_context(color_scheme=scheme, locale="en-US", **ctx)
    page = c.new_page()
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return c, page


def no_overflow(page):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], f"horizontal overflow {w} on {page.url}"


def axe_ok(page, name):
    from axe_playwright_python.sync_playwright import Axe
    r = Axe().run(page)
    bad = [v for v in r.response["violations"] if v.get("impact") in ("serious", "critical")]
    AUDITS[name] = [{"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"])} for v in r.response["violations"]]
    assert not bad, (name, [(v["id"], v["impact"], v["help"]) for v in bad])


def shot(page, name):
    page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def submit(page):
    """Press Enter on the focused control and wait for the resulting navigation (PRG redirect)."""
    with page.expect_navigation():
        page.keyboard.press("Enter")


def tab_to(page, selector, max_tabs=40):
    """Reach an element with the Tab key only; asserts it is reachable and that focus is VISIBLE (outline drawn)."""
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        if page.evaluate("(s) => document.activeElement && document.activeElement.matches(s)", selector):
            style = page.evaluate("() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, s.outlineWidth]; }")
            assert style[0] != "none" and style[1] != "0px", f"focus not visible on {selector}: {style}"
            return
    raise AssertionError(f"{selector} not reachable by keyboard on {page.url}")


def test_01_keyboard_only_setup_with_error_focus_and_skip_link(chromium):
    errors = []
    code = open("/appliance-data/setup-code.txt").read().strip()
    ctx, page = new_page(chromium, errors, **MOBILE)
    page.goto(BASE + "/")
    expect(page).to_have_url(BASE + "/setup")
    page.keyboard.press("Tab")                                                  # first Tab: the skip link
    assert page.evaluate("document.activeElement.className") == "skip"
    no_overflow(page); axe_ok(page, "setup"); shot(page, "01-setup-mobile-light")
    # wrong code first: the error must be announced (role=alert) and receive focus
    tab_to(page, 'input[name="code"]'); page.keyboard.type("AAAA-BBBB-CCCC")
    tab_to(page, 'input[name="password"]'); page.keyboard.type(PW)
    tab_to(page, 'input[name="confirm"]'); page.keyboard.type(PW)
    submit(page)
    assert page.evaluate("document.activeElement.getAttribute('role')") == "alert"
    assert "Wrong setup code" in page.evaluate("document.activeElement.textContent")
    tab_to(page, 'input[name="code"]'); page.keyboard.type(code.lower())
    tab_to(page, 'input[name="password"]'); page.keyboard.type(PW)
    tab_to(page, 'input[name="confirm"]'); page.keyboard.type(PW)
    submit(page)
    expect(page).to_have_url(BASE + "/setup/bluetooth")
    tab_to(page, 'input[name="device_name"]'); page.keyboard.press("Control+A"); page.keyboard.type("Wohnzimmer"); submit(page)
    expect(page).to_have_url(BASE + "/setup/lyrion")
    no_overflow(page); axe_ok(page, "lyrion"); shot(page, "02-lyrion-mobile-light")
    tab_to(page, "form.server button.choice"); submit(page)     # real UDP discovery of the lab LMS
    expect(page).to_have_url(BASE + "/setup/target")
    tab_to(page, 'input[name="target_player"]')
    for _ in range(8):                                                          # arrow keys move within the radio group
        if "lab-player1" in page.evaluate("document.activeElement.closest('label').textContent"):
            break
        page.keyboard.press("ArrowDown")
    page.keyboard.press("Space")
    no_overflow(page); axe_ok(page, "target"); shot(page, "03-target-mobile-light")
    tab_to(page, "button.primary"); submit(page)
    expect(page).to_have_url(BASE + "/setup/finish")
    tab_to(page, 'form[action="/setup/finish"] button'); submit(page)
    expect(page).to_have_url(BASE + "/")
    STATE["storage"] = ctx.storage_state()
    ctx.close()
    assert not errors, errors


def test_02_dashboard_live_updates_accessibility_tree_and_pairing(chromium):
    errors = []
    ctx, page = new_page(chromium, errors, storage_state=STATE["storage"], **MOBILE)
    page.goto(BASE + "/")
    expect(page.locator("#live-headline")).to_have_text("Ready")
    brand = page.locator(".brand").text_content()                              # DOM text (CSS shows the device line in small caps)
    assert "Hi-Fi Bridge" in brand and "Wohnzimmer" in brand                    # product and device name, separately
    no_overflow(page); axe_ok(page, "dashboard-ready"); shot(page, "04-dashboard-ready-mobile-light")
    tree = page.locator("main").aria_snapshot()
    for needle in ('heading "Ready" [level=1]', 'heading "Pair a phone" [level=2]', 'button "Allow pairing for 5 minutes"',
                   'button "Play test sound"'):
        assert needle in tree, (needle, tree)
    assert 'navigation "Main navigation"' in page.locator("header").aria_snapshot()
    marker = page.evaluate("window.__marker = Math.random()")
    sim("connect"); sim("metadata"); sim("play")
    expect(page.locator("#live-headline")).to_have_text("Playing", timeout=15000)
    expect(page.locator("#live-now")).to_have_text("Lab Band - Lab Song", timeout=10000)
    assert page.evaluate("window.__marker") == marker, "page must update live, not reload"
    shot(page, "05-dashboard-playing-mobile-light")
    tab_to(page, 'form[action="/pairing/open"] button'); submit(page)
    cd = page.locator("#live-pairing")
    expect(cd).to_be_visible()
    first = cd.inner_text(); time.sleep(2.2)
    assert cd.inner_text() != first, "countdown should tick"
    shot(page, "06-pairing-open-mobile-light")
    page.get_by_role("button", name="Stop pairing").click()
    ctx.close()
    assert not errors, errors


def test_03_settings_overview_and_sections_on_a_phone(chromium):
    errors = []
    ctx, page = new_page(chromium, errors, storage_state=STATE["storage"], **MOBILE)
    page.goto(BASE + "/settings")
    links = page.locator(".settings-list a")
    assert links.count() == 7
    no_overflow(page); axe_ok(page, "settings"); shot(page, "07-settings-overview-mobile-light")
    for name, path in (("access", "/settings/access"), ("mqtt", "/settings/mqtt"), ("lyrion", "/settings/lyrion")):
        page.goto(BASE + path)
        expect(page.get_by_role("link", name="‹ Settings")).to_be_visible()
        no_overflow(page); axe_ok(page, f"settings-{name}"); shot(page, f"07-settings-{name}-mobile-light")
    mqtt_tree = page.goto(BASE + "/settings/mqtt") and page.locator("main").aria_snapshot()
    assert 'checkbox "Allow opening the pairing window via MQTT"' in mqtt_tree and "[checked]" not in mqtt_tree.split("pairing window via MQTT")[1][:20]
    for path, name in (("/pairing", "pairing"), ("/diagnostics", "diagnostics"), ("/system", "system"), ("/trust", "trust")):
        page.goto(BASE + path); no_overflow(page); axe_ok(page, name); shot(page, f"07-{name}-mobile-light")
    ctx.close()
    assert not errors, errors


def test_04_dark_desktop_layout_and_german(chromium):
    errors = []
    for scheme, size, label in (("dark", MOBILE, "mobile-dark"), ("dark", DESKTOP, "desktop-dark"), ("light", DESKTOP, "desktop-light")):
        c, p = new_page(chromium, errors, scheme=scheme, storage_state=STATE["storage"], **size)
        p.goto(BASE + "/")
        expect(p.locator("#live-headline")).to_have_text("Playing", timeout=10000)
        bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
        assert (bg == "rgb(17, 17, 19)") == (scheme == "dark"), (scheme, bg)
        no_overflow(p); axe_ok(p, f"dashboard-{label}"); shot(p, f"08-dashboard-{label}")
        if label == "desktop-light":
            cards = p.evaluate("[...document.querySelectorAll('.grid.three > .card')].map(c => Math.round(c.getBoundingClientRect().top))")
            assert len(cards) == 4 and len(set(cards[:3])) == 1, cards                    # three cards in one row, system strip below
            p.goto(BASE + "/settings"); shot(p, "09-settings-desktop-light")
            p.goto(BASE + "/diagnostics"); shot(p, "09-diagnostics-desktop-light")
            p.goto(BASE + "/settings/appearance")
            p.get_by_label("Language").select_option("de"); p.get_by_role("button", name="Save").click()
            p.goto(BASE + "/"); expect(p.locator("#live-headline")).to_have_text("Spielt", timeout=10000); shot(p, "10-dashboard-desktop-light-de")
            p.goto(BASE + "/settings/appearance"); p.get_by_label("Sprache").select_option("en"); p.get_by_role("button", name="Speichern").click()
        c.close()
    sim("pause"); sim("disconnect")
    assert not errors, errors


def test_05_https_trusted_after_installing_the_local_ca_refused_without_it(pw, chromium):
    import subprocess
    # Documented Linux trust step (trust page, "Linux" 3): copy the CA into the system store. Done in this disposable container only.
    subprocess.run(["cp", "/appliance-data/certs/ca.crt", "/usr/local/share/ca-certificates/hifi-bridge-local-ca.crt"], check=True)
    subprocess.run(["update-ca-certificates"], check=True, capture_output=True)
    wk = pw.webkit.launch()
    try:
        page = wk.new_page()
        page.goto(HTTPS + "/trust")                                   # NO ignore_https_errors: the installed CA is the only trust
        expect(page.get_by_role("heading", level=1)).to_have_text("Trust this device")
        fp = page.locator("#ca-fp").inner_text()
        assert len(fp) == 95 and fp.count(":") == 31
        shot(page, "11-trust-https-webkit")
    finally:
        wk.close()
    c = chromium.new_context()
    with pytest.raises(PwError) as e:
        c.new_page().goto(HTTPS + "/trust")
    assert "ERR_CERT_AUTHORITY_INVALID" in str(e.value)               # CA not in Chromium's NSS store -> refused, never accepted silently
    c.close()
    with open(f"{SHOTS}/axe-results.json", "w") as fh:
        json.dump(AUDITS, fh, indent=1, sort_keys=True)
