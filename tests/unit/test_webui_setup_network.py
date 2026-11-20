"""UNIT (real HTTP WebUI, real network agent over a UNIX socket, SIMULATED radio, fake clock): the setup wizard's network step
(DECISIONS #122, #137; docs/NETWORK.md section 12) - every state the step can see: setup hotspot, Ethernet online, client Wi-Fi already
working, no Wi-Fi hardware, unmanaged installation, unreachable network service; a trial started from the step, confirmed after a new
login at another address, rolled back with its reason, retried, undone and skipped; resuming after a reload; language switch; the same
auth/CSRF rules as every page; no password in any page, the diagnostics or the log."""
import os
import re
import urllib.parse

import pytest
from test_webui import PW, A11y, Client, setup_account
from test_webui_network import HOTSPOT, SECRETS, flash, make_net_ui, no_secrets

from btbridge.net import netclient as C
from btbridge.net import netconfig as N
from btbridge.service import ApplianceService
from btbridge.webui import i18n
from btbridge.webui.server import WebUI
from test_service import Lmses, fake_tree

STEP = "/setup/network"


@pytest.fixture
def setup_ui():
    """Fresh device with country + hotspot from the card: owner password set with the setup code, the wizard NOT finished."""
    yield from make_net_ui(setup_account)


class Elsewhere(Client):
    """The same browser after the network change: another address (the device's name instead of the hotspot address), no cookie."""

    def req(self, method, path, form=None, headers=None, raw_body=None):
        return super().req(method, path, form=form, headers={"Host": f"localhost:{self.port}", **(headers or {})}, raw_body=raw_body)


def token(c) -> str:
    """The session's CSRF token, read from the network step itself (Client.csrf would open /setup/bluetooth and move the resume point)."""
    m = re.search(r'name="csrf" value="([^"]+)"', c.get(STEP).body)
    assert m
    return m.group(1)


def send(c, path, form=None, **kw):
    """POST with the session's token from the step (Client.post evaluates Client.csrf eagerly, which opens /setup/bluetooth)."""
    return c.post(path, {**(form or {}), "csrf": token(c)}, csrf=False, **kw)


def checked(body):
    p = A11y(); p.feed(body)
    assert p.h1 == 1 and not p.problems, p.problems
    assert 'aria-current=step><span>2</span>Network' in body or 'aria-current=step><span>2</span>Netzwerk' in body
    return body


def hotspot(u):
    u.advance(16)                                                   # no Wi-Fi, no Ethernet: the setup hotspot opens after the grace
    assert u.agent.manager.state == "hotspot"


# ---------------------------------------------------------------- where the step sits, what it shows on the hotspot
def test_the_account_leads_to_the_network_step_which_explains_the_change_before_it_happens(setup_ui):
    u = setup_ui
    hotspot(u)
    page = checked(u.c.get(STEP).body)
    assert "Home network" in page and "setup hotspot “Hi-Fi Bridge 4F2A”" in page
    assert 'action="/setup/network/wifi"' in page and 'value="Home"' in page and 'value="__other__"' in page      # scan list + hidden SSID
    assert 'action="/setup/network/scan"' in page and 'name="password"' in page and 'value=""' in page
    form = page.split('action="/setup/network/wifi"')[1].split("</form>")[0]
    assert "data-confirm" not in form                                  # explained on the page, no dialog (works without JavaScript)
    explain = form.split('class="net-explain"')[1]
    assert explain.index("What happens when you press") < explain.index("<button>")            # read BEFORE pressing Connect
    url = f"http://hifi-bridge.local:{u.web.port}/"
    assert f'<a href="{url}">{url}</a>' in explain and "<strong>hifi-bridge</strong>" in explain  # where to find the device afterwards
    assert "<strong>Hi-Fi Bridge 4F2A</strong> closes" in explain and "http://10.42.0.1/" in explain and "3 minutes" in explain
    assert 'href="/setup/bluetooth">Skip this step</a>' in page and "cannot find a Lyrion server" in page
    assert "/static/network.js" in page and "/static/network.css" in page
    no_secrets(page)


def test_a_hotspot_join_is_confirmed_after_a_new_login_at_the_new_address_and_the_wizard_continues(setup_ui):
    u = setup_ui
    hotspot(u)
    r = send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    assert r.status == 303 and r.getheader("Location") == STEP and "please confirm" in flash(u)
    assert u.system.role == "client"                                   # the hotspot is gone: this browser lost the page
    u.advance(4)                                                       # the device joined the home Wi-Fi
    other = Elsewhere(u.web.port)                                      # the phone rejoined the home Wi-Fi, opens the device's name
    assert other.get("/").getheader("Location") == "/login"
    r = other.post("/login", {"password": PW}, csrf=False)
    assert r.getheader("Location") == "/"
    assert other.get("/").getheader("Location") == STEP                # the wizard resumes where the change waits for "Keep"
    page = checked(other.get(STEP).body)
    assert "Keep the new network settings?" in page and "The device has joined “Home” as 192.168.1.57" in page
    assert 'class="net-countdown" data-remaining="' in page and 'action="/setup/network/confirm"' in page
    assert 'href="/setup/bluetooth"' not in page                       # no "continue" before Keep or Undo
    no_secrets(page)
    r = send(other, STEP + "/confirm")
    assert r.getheader("Location") == "/setup/bluetooth" and "Network settings kept" in urllib.parse.unquote(other.cookies["bab_flash"])
    assert N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["ssid"] == "Home"
    assert not os.path.exists(os.path.join(u.data, N.PENDING_NAME))
    page = checked(other.get(STEP).body)                               # coming back to the step: connected, continue
    assert "Connected to the Wi-Fi “Home” as <strong>192.168.1.57</strong>" in page
    assert 'class="button primary" href="/setup/bluetooth"' in page and "Use another Wi-Fi" in page
    assert "<strong>Home</strong> and this page loses its connection" in page          # changing it again would cut this page off


def test_a_wrong_password_rolls_back_shows_the_reason_and_allows_retry_or_skip(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "not-the-password"})
    u.advance(61)                                                      # the attempt fails: rolled back at once, hotspot back
    assert u.agent.manager.state == "hotspot"
    assert u.c.get("/").getheader("Location") == STEP                  # the old hotspot page reloads into the step
    assert not os.path.exists(os.path.join(u.data, N.PENDING_NAME))   # the bridge dropped the rolled-back file at its next status
    page = checked(u.c.get(STEP).body)
    assert "Previous network settings restored" in page and "password was not accepted" in page
    assert "hotspot is active again" in page and 'action="/setup/network/wifi"' in page
    assert 'href="/setup/bluetooth">Skip this step</a>' in page
    r = send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})        # retry
    assert r.getheader("Location") == STEP
    u.advance(4)
    assert "Keep the new network settings?" in u.c.get(STEP).body
    send(u.c, STEP + "/confirm")
    assert N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["psk"] == "wifi-secret-123"


def test_skipping_after_a_failure_continues_the_wizard_and_keeps_the_hotspot(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Nowhere", "password": "whatever-123", "hidden": "on"})
    u.advance(61)
    assert "network was not found" in u.c.get(STEP).body
    assert u.c.get("/setup/bluetooth").status == 200 and u.agent.manager.state == "hotspot"
    assert u.c.get("/").getheader("Location") == "/setup/bluetooth"   # resumes at the step shown last, not at the failure again
    assert N.load(os.path.join(u.data, N.FILE_NAME)).get("wifi") is None


def test_unconfirmed_change_returns_the_hotspot_and_names_the_address_it_had(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(181)                                                     # nobody pressed Keep within 3 minutes
    page = u.c.get(STEP).body
    assert "not confirmed in time" in page and "192.168.1.57" in page and "hotspot is active again" in page


def test_undo_from_the_step(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(4)
    r = send(u.c, STEP + "/revert")
    assert r.getheader("Location") == STEP and "Change undone" in flash(u)
    u.advance(1)
    page = u.c.get(STEP).body
    assert "The change was undone" in page and u.agent.manager.state == "hotspot"
    r = send(u.c, STEP + "/confirm")
    assert r.getheader("Location") == STEP and "no change waiting" in flash(u)          # nothing to keep: stays on the step


def test_the_keep_banner_of_other_wizard_pages_stays_in_the_wizard(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(4)
    page = u.c.get("/setup/lyrion").body
    assert "Keep the new network settings?" in page and 'action="/setup/network/confirm"' in page
    assert u.c.get("/").getheader("Location") == STEP                  # pending change: the resume point is the network step


# ---------------------------------------------------------------- already online: no Wi-Fi steps
def test_ethernet_online_is_a_short_confirmation_with_optional_wifi(setup_ui):
    u = setup_ui
    u.system.ethernet_link = True
    u.advance(20)
    assert u.agent.manager.state == "off"                              # Ethernet works: no hotspot
    page = checked(u.c.get(STEP).body)
    assert "Connected via Ethernet as <strong>192.168.1.20, fd00:1::20</strong>." in page
    assert 'class="button primary" href="/setup/bluetooth">Continue</a>' in page
    more = page.split('<details class="net-more">')[1].split("</details>")[0]
    assert "<summary>Set up Wi-Fi anyway</summary>" in more and 'action="/setup/network/wifi"' in more
    assert "Ethernet connection stays as it is" in more and "loses its connection" not in more
    r = send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    assert r.getheader("Location") == STEP and "Network settings saved" in flash(u)    # no effect on the path in use: no trial
    page = u.c.get(STEP).body
    assert "Wi-Fi network: Connecting to Wi-Fi … · Home" in page
    u.advance(4)
    page = u.c.get(STEP).body
    assert "Connected via Ethernet" in page and "Connected to the Wi-Fi “Home” as <strong>192.168.1.57</strong>" in page
    no_secrets(page)


def test_a_working_client_wifi_from_the_card_needs_no_wifi_step(setup_ui):
    u = setup_ui
    N.write_private(os.path.join(u.data, N.FILE_NAME), N.validate({"country": "DE", "hotspot": HOTSPOT,
                                                                  "wifi": {"ssid": "Home", "psk": "wifi-secret-123"}}))
    u.agent.start()
    u.advance(4)
    page = checked(u.c.get(STEP).body)
    assert "Connected to the Wi-Fi “Home” as <strong>192.168.1.57</strong>" in page and "<summary>Use another Wi-Fi</summary>" in page
    assert 'class="button primary" href="/setup/bluetooth"' in page
    assert "Saved network: “Home”" in page and 'action="/settings/network/forget"' not in page    # nothing to forget in the wizard
    no_secrets(page)


def test_a_card_with_a_mistyped_wifi_password_shows_the_problem_and_the_hotspot(setup_ui):
    u = setup_ui
    N.write_private(os.path.join(u.data, N.FILE_NAME), N.validate({"country": "DE", "hotspot": HOTSPOT,
                                                                  "wifi": {"ssid": "Home", "psk": "mistyped-pass"}}))
    u.agent.start()
    u.advance(10)
    page = u.c.get(STEP).body
    assert "trying to join the Wi-Fi “Home”" in page and "Setup hotspot: opens in" in page
    u.advance(60)
    page = u.c.get(STEP).body
    assert "setup hotspot “Hi-Fi Bridge 4F2A”" in page and "Last problem: The Wi-Fi password was not accepted." in page
    no_secrets(page)
    assert "mistyped-pass" not in page


def test_no_wifi_hardware_offers_only_ethernet_and_skip(setup_ui):
    u = setup_ui
    u.system.wlan = None
    u.advance(2)
    page = checked(u.c.get(STEP).body)
    assert "No Wi-Fi hardware was found" in page and 'action="/setup/network/wifi"' not in page
    assert 'href="/setup/bluetooth">Skip this step</a>' in page


# ---------------------------------------------------------------- no network service / unmanaged installations
def test_unmanaged_installations_continue_without_a_wifi_form(tmp_path):
    svc = ApplianceService(str(tmp_path / "d"), source=None, stream_port=0, lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None,
                           auth_iterations=1000)
    svc.start()
    w = WebUI(svc, "127.0.0.1", 0); port = w.start()
    u = type("U", (), dict(svc=svc, c=Client(port), code=open(svc.auth.code_file).read().strip()))
    try:
        setup_account(u)
        page = checked(u.c.get(STEP).body)                             # no network object at all (old lab, tests)
        main = page.split('<main id="main">')[1].split("</main>")[0]
        assert "managed by the host system" in main and 'href="/setup/bluetooth">Continue</a>' in main and "<form" not in main
        svc.network = C.NetworkService(str(tmp_path / "d"), None, interfaces=lambda: {
            "eth0": {"carrier": True, "mac": "m", "default_route": True,
                     "addresses": [{"address": "172.18.0.5", "prefix": 16}, {"address": "fe80::1", "prefix": 64}]}})
        page = checked(u.c.get(STEP).body)                             # unmanaged NetworkService: the host's addresses
        assert "Connected to the network as <strong>172.18.0.5</strong> (managed by the host system)" in page and "fe80" not in page
        assert "Set up Wi-Fi anyway" not in page
    finally:
        w.stop(); svc.stop()


def test_an_unreachable_network_service_never_breaks_the_step_or_the_resume(setup_ui, monkeypatch):
    u = setup_ui
    monkeypatch.setattr(u.svc.network, "status", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = u.c.get(STEP)
    assert r.status == 200 and "network service of the device is not reachable" in r.body and "Skip this step" in r.body
    assert u.c.get("/").getheader("Location") == STEP
    monkeypatch.setattr(u.svc.network, "status", lambda: {"managed": False, "state": "agent_unreachable", "interfaces": [
        {"name": "eth0", "default_route": True, "addresses": ["10.0.0.9/24"]}]})
    page = u.c.get(STEP).body
    assert "not reachable" in page and "10.0.0.9" not in page                         # claims no connection it cannot check


def test_an_agent_without_an_answer_keeps_the_owner_on_the_step(setup_ui, monkeypatch):
    u = setup_ui
    hotspot(u)
    csrf = token(u.c)
    monkeypatch.setattr(u.svc.network, "save", lambda new: (_ for _ in ()).throw(C.AgentUnavailable("x", sent=True)))
    r = u.c.post(STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123", "csrf": csrf}, csrf=False)
    assert r.getheader("Location") == STEP and "did not answer in time" in flash(u)
    monkeypatch.setattr(u.svc.network, "save", lambda new: (_ for _ in ()).throw(C.AgentUnavailable("x")))
    r = u.c.post(STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123", "csrf": csrf}, csrf=False)
    assert r.getheader("Location") == STEP and "not reachable" in flash(u)
    r = u.c.post(STEP + "/wifi", {"ssid": "", "password": "x", "csrf": csrf}, csrf=False)
    assert r.getheader("Location") == STEP and flash(u).startswith("e:")


# ---------------------------------------------------------------- resume, reload, language
def test_the_wizard_resumes_at_the_step_shown_last(setup_ui):
    u = setup_ui
    assert u.c.get("/").getheader("Location") == STEP                  # nothing shown yet: the network step
    assert u.c.get("/setup").getheader("Location") == STEP
    u.c.get("/setup/lyrion")
    assert u.c.get("/").getheader("Location") == "/setup/lyrion"
    assert u.c.get("/settings").getheader("Location") == "/setup/lyrion"
    assert 'href="/setup/lyrion"' in u.c.get("/settings/network").body               # "back to the setup" goes there too
    other = Elsewhere(u.web.port)
    other.post("/login", {"password": PW}, csrf=False)
    assert other.get("/").getheader("Location") == "/setup/lyrion"   # also from another address after a new login


def test_language_switch_in_the_middle_of_the_step(setup_ui):
    u = setup_ui
    hotspot(u)
    assert "Home network" in u.c.get(STEP).body
    r = u.c.post("/lang", {"lang": "de"}, csrf=False, headers={"Referer": f"http://127.0.0.1:{u.web.port}{STEP}"})
    assert r.getheader("Location") == STEP
    page = checked(u.c.get(STEP).body)
    assert 'lang="de"' in page and "Heimnetz" in page and "Was beim Tippen auf „Verbinden“ passiert" in page
    assert "Einrichtungs-Hotspot „Hi-Fi Bridge 4F2A“" in page and "Diesen Schritt überspringen" in page
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(4)
    page = checked(u.c.get(STEP).body)
    assert "Einstellungen behalten" in page
    assert "Das Gerät ist mit „Home“ verbunden als 192.168.1.57" in page


# ---------------------------------------------------------------- security
def test_the_step_keeps_the_auth_csrf_and_setup_code_rules(setup_ui):
    u = setup_ui
    hotspot(u)
    anon = Client(u.web.port)
    assert anon.get(STEP).getheader("Location") == "/login"
    for action in ("/wifi", "/scan", "/confirm", "/revert"):
        assert anon.post(STEP + action, {"ssid": "Home", "password": "wifi-secret-123"}, csrf=False).getheader("Location") == "/login"
        r = u.c.post(STEP + action, {"ssid": "Home", "password": "wifi-secret-123"}, csrf=False)
        assert r.getheader("Location") == "/login", action                                   # owner session, but no CSRF token
        r = send(u.c, STEP + action, {"ssid": "Home", "password": "wifi-secret-123"},
                     headers={"Origin": "http://evil.example"})
        assert r.status == 403, action
    assert not os.path.exists(os.path.join(u.data, N.PENDING_NAME)) and u.agent.manager.state == "hotspot"


def test_a_fresh_device_sends_the_step_to_the_setup_code_first():
    gen = make_net_ui(lambda u: None)
    u = next(gen)
    try:
        assert u.c.get(STEP).getheader("Location") == "/setup"
        assert u.c.post(STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"}, csrf=False).getheader("Location") == "/login"
        assert not os.path.exists(os.path.join(u.data, N.PENDING_NAME))
    finally:
        gen.close()


def test_no_password_in_pages_pending_state_diagnostics_or_log(setup_ui):
    u = setup_ui
    hotspot(u)
    send(u.c, STEP + "/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(4)
    for path in (STEP, "/setup/bluetooth", "/setup/lyrion", "/settings/network", "/api/network"):
        no_secrets(u.c.get(path).body)
    pending = open(os.path.join(u.data, N.PENDING_NAME), "rb").read()          # the existing trial file (0600) holds it, as before
    assert oct(os.stat(os.path.join(u.data, N.PENDING_NAME)).st_mode & 0o777) == "0o600" and b"wifi-secret-123" in pending
    bundle = str(u.svc.diagnostics_bundle())
    for s in SECRETS:
        assert s not in bundle
    log = "\n".join(m for _, m in u.svc.logs.recent(1000))
    assert "network: change" in log                                    # the trial was logged - without the password
    for s in SECRETS:
        assert s not in log


def test_every_wizard_network_key_exists_in_both_languages():
    import btbridge.webui.views_setup_network as V
    en, de = i18n.strings("en"), i18n.strings("de")
    used = set(re.findall(r'"((?:wizard|net)\.[a-z0-9_.]+)"', open(V.__file__).read()))
    used |= {f"wizard.net.online.{k}" for k in ("ethernet", "wifi", "host")} | {"wizard.net.wifi_anyway.help", "wizard.net.wifi_other.help"}
    used = {k for k in used if not k.endswith(".")}
    missing = sorted(k for k in used if k not in en or k not in de)
    assert not missing, missing
    assert set(k for k in en if k.startswith("wizard.")) == set(k for k in de if k.startswith("wizard."))


def test_the_step_asks_for_a_wifi_list_once_when_there_is_none(setup_ui):
    import time
    u = setup_ui
    u.advance(1)                                                       # radio off, no scan yet (the hotspot would bring one along)
    page = u.c.get(STEP).body
    assert 'data-scanning="1"' in page and "Searching for networks" in page                # network.js reloads when it is done
    for _ in range(50):
        u.advance(1)
        if 'value="Home"' in u.c.get(STEP).body:
            break
        time.sleep(0.05)
    page = u.c.get(STEP).body
    assert 'value="Home"' in page and 'data-scanning="1"' not in page
    assert sum(1 for c in u.system.calls if c[0] == "scan") == 1        # asked once, not on every page load
