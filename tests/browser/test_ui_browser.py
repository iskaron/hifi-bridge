"""BROWSER (real Chromium via Playwright) against the appliance container (real service + WebUI, SIMULATED phone, REAL lab LMS).
Checks what HTTP tests cannot: rendering at phone and desktop widths, light/dark, JavaScript live updates, CSP/console errors,
horizontal overflow, and an automated WCAG audit with axe-core (serious/critical violations fail). Screenshots: reports/screenshots/.
Not a substitute for a manual accessibility review or tests on real phones' browsers."""
import json
import os
import time
import urllib.request

import pytest
from playwright.sync_api import expect, sync_playwright

BASE, SIM = os.environ.get("BASE_URL", "http://appliance:8080"), os.environ.get("SIM_URL", "http://appliance:8099")
SHOTS = "/work/reports/screenshots"
PW = "a good long passphrase"
MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
DESKTOP = dict(viewport={"width": 1280, "height": 860})


def sim(action):
    with urllib.request.urlopen(f"{SIM}/{action}", timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def pw():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, errors, scheme="light", **ctx):
    c = browser.new_context(color_scheme=scheme, locale="en-US", **ctx)
    page = c.new_page()
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return c, page


def no_overflow(page):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], f"horizontal overflow {w} on {page.url}"


def axe_ok(page, name, results):
    from axe_playwright_python.sync_playwright import Axe
    r = Axe().run(page)
    bad = [v for v in r.response["violations"] if v.get("impact") in ("serious", "critical")]
    results[name] = [{"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"])} for v in r.response["violations"]]
    assert not bad, (name, [(v["id"], v["impact"], v["help"]) for v in bad])


def shot(page, name):
    page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def test_owner_journey_in_a_real_browser(pw):
    errors, audits = [], {}
    code = open("/appliance-data/setup-code.txt").read().strip()
    ctx, page = new_page(pw, errors, **MOBILE)
    # 1. setup wizard (phone width)
    page.goto(BASE + "/")
    expect(page).to_have_url(BASE + "/setup")
    expect(page.get_by_role("heading", level=1)).to_have_text("Welcome")
    no_overflow(page); axe_ok(page, "setup", audits); shot(page, "01-setup-mobile-light")
    page.get_by_label("Setup code").fill(code.lower())
    page.get_by_label("New password").fill(PW)
    page.get_by_label("Repeat password").fill(PW)
    page.get_by_role("button", name="Create owner password").click()
    expect(page).to_have_url(BASE + "/setup/bluetooth")
    page.get_by_label("Bluetooth name").fill("Wohnzimmer")
    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(BASE + "/setup/lyrion")
    no_overflow(page); axe_ok(page, "lyrion", audits); shot(page, "02-lyrion-mobile-light")
    discovered = page.locator("form.server button.choice")
    expect(discovered.first).to_be_visible()                   # real UDP discovery of the lab LMS
    discovered.first.click()
    expect(page).to_have_url(BASE + "/setup/target")
    expect(page.get_by_text("lab-player1")).to_be_visible()
    no_overflow(page); axe_ok(page, "target", audits); shot(page, "03-target-mobile-light")
    page.get_by_text("lab-player1").click()
    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(BASE + "/setup/finish")
    page.get_by_role("button", name="Finish setup").click()
    expect(page).to_have_url(BASE + "/")
    # 2. dashboard live updates without reload (JS polling)
    expect(page.locator("#live-headline")).to_have_text("Ready")
    no_overflow(page); axe_ok(page, "dashboard-ready", audits); shot(page, "04-dashboard-ready-mobile-light")
    marker = page.evaluate("window.__marker = Math.random()")
    sim("connect"); sim("metadata"); sim("play")
    expect(page.locator("#live-headline")).to_have_text("Playing", timeout=15000)
    expect(page.locator("#live-now")).to_have_text("Lab Band - Lab Song", timeout=10000)
    assert page.evaluate("window.__marker") == marker, "page must update live, not reload"
    shot(page, "05-dashboard-playing-mobile-light")
    # 3. pairing window with JS countdown
    page.get_by_role("button", name="Allow pairing for 5 minutes").click()
    cd = page.locator("#live-pairing")
    expect(cd).to_be_visible()
    first = cd.inner_text(); time.sleep(2.2)
    assert cd.inner_text() != first, "countdown should tick"
    shot(page, "06-pairing-open-mobile-light")
    page.get_by_role("button", name="Stop pairing").click()
    for path, name in (("/pairing", "pairing"), ("/settings", "settings"), ("/diagnostics", "diagnostics"), ("/system", "system")):
        page.goto(BASE + path); no_overflow(page); axe_ok(page, name, audits); shot(page, f"07-{name}-mobile-light")
    storage = ctx.storage_state()
    ctx.close()
    # 4. dark mode + desktop, same session
    for scheme, size, label in (("dark", MOBILE, "mobile-dark"), ("dark", DESKTOP, "desktop-dark"), ("light", DESKTOP, "desktop-light")):
        c, p = new_page(pw, errors, scheme=scheme, storage_state=storage, **size)
        p.goto(BASE + "/")
        expect(p.locator("#live-headline")).to_have_text("Playing", timeout=10000)
        bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
        assert (bg == "rgb(17, 17, 19)") == (scheme == "dark"), (scheme, bg)
        no_overflow(p); axe_ok(p, f"dashboard-{label}", audits); shot(p, f"08-dashboard-{label}")
        if label == "desktop-light":
            p.goto(BASE + "/settings"); shot(p, "09-settings-desktop-light")
            p.get_by_label("Language").select_option("de"); p.get_by_label("Language").locator("xpath=ancestor::form").get_by_role("button").click()
            p.goto(BASE + "/"); expect(p.locator("#live-headline")).to_have_text("Spielt", timeout=10000); shot(p, "10-dashboard-desktop-light-de")
            p.goto(BASE + "/settings"); p.get_by_label("Sprache").select_option("en")
            p.get_by_label("Sprache").locator("xpath=ancestor::form").get_by_role("button").click()
        c.close()
    sim("pause"); sim("disconnect")
    with open(f"{SHOTS}/axe-results.json", "w") as fh:
        json.dump(audits, fh, indent=1, sort_keys=True)
    assert not errors, errors                                   # no JS errors, no CSP violations
