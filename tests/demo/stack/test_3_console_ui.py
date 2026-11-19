"""DEMO STACK, part 3 (real Chromium): the demo console as a user sees it - phones and audio monitor in light/dark, German/English, phone and
desktop widths; automated WCAG audit (axe-core), CSP/console errors, overflow; the monitor's MEASURED added latency; the owner removing a
phone in the bridge WebUI and the phone noticing it; MQTT configured in the bridge WebUI."""
import json
import os

import math

import pytest
from playwright.sync_api import expect, sync_playwright

from demo_env import BRIDGE, CONSOLE, SHOTS, STATE_DIR, phone, phone_state, save_json, wait

MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
DESKTOP = dict(viewport={"width": 1360, "height": 900})
AUDITS: dict = {}


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    yield b
    b.close()


def page_for(browser, errors, scheme="light", locale="de-DE", state=None, **kw):
    ctx = browser.new_context(color_scheme=scheme, locale=locale, storage_state=state, **kw)
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return ctx, page


def audit(page, name):
    from axe_playwright_python.sync_playwright import Axe
    r = Axe().run(page)
    AUDITS[name] = [{"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"])} for v in r.response["violations"]]
    bad = [(v["id"], v["impact"], v["help"]) for v in r.response["violations"] if v.get("impact") in ("serious", "critical")]
    assert not bad, (name, bad)


def no_overflow(page):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], f"horizontal overflow {w} on {page.url}"


def test_01_console_pages_in_both_themes_languages_and_sizes(browser):
    errors = []
    for scheme, locale, size, tag in (("light", "de-DE", MOBILE, "mobile-light-de"), ("dark", "en-US", DESKTOP, "desktop-dark-en")):
        ctx, page = page_for(browser, errors, scheme, locale, **size)
        for path, name in (("/", "overview"), ("/phones", "phones"), ("/monitor", "monitor")):
            page.goto(CONSOLE + path)
            page.wait_for_timeout(1500)                                     # first /api/state round
            expect(page.locator(".sim").first).to_be_visible()                # every page marks the simulation
            no_overflow(page)
            audit(page, f"{name}-{tag}")
            page.screenshot(path=f"{SHOTS}/demo-{name}-{tag}.png", full_page=True)
        ctx.close()
    assert not errors, errors


def test_02_phone_controls_in_the_browser(browser):
    errors = []
    ctx, page = page_for(browser, errors, "light", "de-DE", **DESKTOP)
    page.goto(CONSOLE + "/phones")
    anna = page.locator('[data-phone="a"]')
    if phone_state("a")["connected"]:                                      # start from "not connected" whatever part 2 left behind
        anna.get_by_role("button", name="Trennen").click()
        wait(lambda: not phone_state("a")["connected"], 10, what="phone A disconnected via the UI")
    anna.get_by_role("button", name="Verbinden").click()
    wait(lambda: phone_state("a")["connected"], 15, what="phone A connected via the UI")
    anna.get_by_role("button", name="Play").click()
    wait(lambda: phone_state("a")["status"] == "playing", 10, what="phone A playing via the UI")
    page.wait_for_timeout(2500)
    expect(anna.locator(".dot.on")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/demo-04-phones-playing.png", full_page=True)
    ctx.close()
    assert not errors, errors


@pytest.fixture(scope="module")
def local_console():
    """http://console:8100 is not a secure context (no AudioWorklet). The demo user opens http://127.0.0.1:<port>, which is. Recreate
    that here: a local forward inside the browser container with the demo's own gateway module, then use http://localhost:18101."""
    import subprocess
    import sys
    import time
    proc = subprocess.Popen([sys.executable, "-m", "hifidemo.gateway", "18101=console:8100"])
    time.sleep(1.0)
    yield "http://localhost:18101"
    proc.terminate()


@pytest.mark.parametrize("engine", ["AudioWorklet", "ScriptProcessor"])
def test_03_audio_monitor_plays_the_room_and_measures_its_latency(browser, local_console, engine):
    errors = []
    ctx, page = page_for(browser, errors, "light", "de-DE", **DESKTOP)
    page.goto((local_console if engine == "AudioWorklet" else CONSOLE) + "/monitor")
    page.get_by_role("radio", name="Wohnzimmer").check()
    page.get_by_role("button", name="Zuhören").click()
    # the console's strict CSP (no 'unsafe-eval') also blocks Playwright's string predicates, so poll via evaluate()
    wait(lambda: page.evaluate("() => window.monitorStats.latencyMs !== null && window.monitorStats.frames > 44100 * 4"), 30,
         what="audio in the browser monitor")
    # loudness at the browser's output (after listening level and soft limiter): an analyser on the last node before the speakers
    page.evaluate("() => { const a = M.ctx.createAnalyser(); a.fftSize = 2048; M.limiter.connect(a); window.__outLevels = []; "
                  "setInterval(() => { const d = new Float32Array(2048); a.getFloatTimeDomainData(d); let s = 0, p = 0; "
                  "for (const v of d) { s += v * v; p = Math.max(p, Math.abs(v)); } window.__outLevels.push([s / d.length, p]); }, 100); }")
    gain_default = page.evaluate("() => M.gain.gain.value")
    samples = []
    for _ in range(10):
        page.wait_for_timeout(1000)
        samples.append(page.evaluate("({...window.monitorStats})"))
    assert samples[-1]["engine"] == engine
    lv = page.evaluate("() => window.__outLevels")
    mean_sq = sum(a for a, _ in lv) / len(lv) if lv else 0.0
    out = {"rms_dbfs": round(10 * math.log10(max(mean_sq, 1e-12)), 1), "peak": round(max(p for _, p in lv), 3), "default_gain": gain_default}
    if engine == "AudioWorklet":
        page.screenshot(path=f"{SHOTS}/demo-05-monitor-listening.png", full_page=True)
    lat = [s["latencyMs"] for s in samples if s["latencyMs"] is not None]
    result = {"label": "MEASURED in headless Chromium in Docker (no sound card: output latency as reported by the browser)",
              "latency_ms": {"min": min(lat), "median": sorted(lat)[len(lat) // 2], "max": max(lat)},
              "buffer_ms_last": samples[-1]["bufferMs"], "output_ms_reported": samples[-1]["outputMs"],
              "underruns": samples[-1]["underruns"], "peak": round(samples[-1]["peak"], 3), "browser_output": out}
    result["engine"] = engine
    save_json(f"{STATE_DIR}/demo_monitor_latency_{engine}.json", result)
    assert samples[-1]["peak"] > 0.005                                      # real audio arrived (behind Lyrion and Squeezelite)
    assert 50 < result["latency_ms"]["median"] < 1500, result
    assert out["default_gain"] == 4                                          # the demo's default listening level (docs/DEMO.md "Loudness")
    assert out["rms_dbfs"] > -35, out                                         # audible at the speakers (it was about -36 dBFS or less before)
    assert out["peak"] <= 0.99, out                                           # the soft limiter keeps a high listening level from clipping
    ctx.close()
    assert not [e for e in errors if "ScriptProcessorNode is deprecated" not in e], errors     # expected only on the fallback path


def test_04_owner_removes_a_phone_and_the_phone_learns_it(browser):
    errors = []
    ctx, page = page_for(browser, errors, "light", "en-US", state=f"{STATE_DIR}/owner-session.json", **DESKTOP)
    page.goto(BRIDGE + "/pairing")
    page.screenshot(path=f"{SHOTS}/demo-06-bridge-paired-phones.png", full_page=True)
    row = page.locator("form[action='/devices/remove']").filter(has=page.locator("input[value='02:B2:00:00:00:02']"))
    page.on("dialog", lambda d: d.accept())
    with page.expect_navigation():
        row.locator("button").click()
    expect(page.get_by_text("Bens Smartphone")).to_have_count(0)
    r = phone("b", "connect")
    assert r == {"ok": False, "message": "pair_again"}                      # the phone must pair again inside a window
    ctx.close()


def test_05_mqtt_configured_in_the_bridge_webui(browser):
    creds = dict(line.strip().split("=", 1) for line in open("/mqtt-creds/credentials.env") if "=" in line)
    errors = []
    ctx, page = page_for(browser, errors, "light", "en-US", state=f"{STATE_DIR}/owner-session.json", **DESKTOP)
    page.goto(BRIDGE + "/settings/mqtt")
    page.locator('input[name="mqtt_enabled"]').check()
    page.fill('input[name="mqtt_host"]', "mosquitto")
    page.fill('input[name="mqtt_port"]', "1883")
    page.fill('input[name="mqtt_username"]', creds["HIFI_BRIDGE_USER"])
    page.fill('input[name="mqtt_password"]', creds["HIFI_BRIDGE_PASSWORD"])
    with page.expect_navigation():
        page.press('input[name="mqtt_password"]', "Enter")
    expect(page.locator('input[name="mqtt_enabled"]')).to_be_checked()        # saved; discovery on the broker: scripts/test_demo.sh step 5
    page.screenshot(path=f"{SHOTS}/demo-07-bridge-mqtt.png", full_page=True)
    ctx.close()
    assert not errors, errors


def test_06_track_titles_reach_lyrion_after_the_owner_enables_icy(browser):
    from demo_env import lms_status
    errors = []
    ctx, page = page_for(browser, errors, "light", "en-US", state=f"{STATE_DIR}/owner-session.json", **DESKTOP)
    page.goto(BRIDGE + "/settings/playback")
    page.locator('input[name="icy_metadata"]').check()
    with page.expect_navigation():
        page.locator('form[action="/settings/playback"] button').first.click()
    ctx.close()
    if phone_state("a")["status"] != "playing":
        phone("a", "play")
    phone("a", "next")
    title = phone_state("a")["track"]["title"]
    wait(lambda: title in str(lms_status("Wohnzimmer")), 30, what=f"title {title!r} in Lyrion")


def test_99_audit_summary():
    save_json(f"{STATE_DIR}/demo_axe.json", AUDITS)
