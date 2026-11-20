"""UNIT (real HTTP WebUI, real network agent over a UNIX socket, SIMULATED radio, fake clock): the network pages - status, scan list,
join (incl. hidden network), forget, addresses, hotspot and country, the confirm/undo banner, automatic rollback, error messages,
CSRF, no password ever in a page, DE/EN and structural accessibility."""
import json
import os
import re
import shutil
import tempfile
import threading
import urllib.parse

import pytest
from test_webui import A11y, Client, finish_wizard, setup_account

from btbridge.net import netagent as A
from btbridge.net import netclient as C
from btbridge.net import netconfig as N
from btbridge.service import ApplianceService
from btbridge.webui import i18n
from btbridge.webui.server import WebUI
from btkit.sim_net import SimulatedNetworkSystem
from test_service import Lmses, fake_tree

HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}
SECRETS = ("wifi-secret-123", "correct horse battery", "garden-pass-1", "new-hotspot-pass")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def net_ui():
    yield from make_net_ui(finish_wizard)


@pytest.fixture
def setup_ui():
    """Fresh device: owner password set with the setup code, the wizard NOT finished (review N-1)."""
    yield from make_net_ui(setup_account)


def make_net_ui(prepare):
    root = tempfile.mkdtemp(prefix="hw", dir="/tmp")
    data, run = os.path.join(root, "d"), os.path.join(root, "r")
    os.makedirs(run)
    svc = ApplianceService(data, source=None, stream_port=0, advertise_host="bridge", lms_factory=Lmses(), monitor=fake_tree_dir(root),
                           echo=None, auth_iterations=1000)
    N.write_private(os.path.join(data, N.FILE_NAME), N.validate({"country": "DE", "hotspot": HOTSPOT}))
    clock = Clock()
    system = SimulatedNetworkSystem(clock=clock, connect_s=3.0)
    agent = A.Agent(system, data, os.getuid(), run, clock=clock, log=lambda m: None)
    agent.start()
    path = agent.bind(None)
    threading.Thread(target=agent.serve, daemon=True).start()
    svc.network = C.NetworkService(data, C.AgentClient(path), log=svc.logs, secrets=svc.logs.secrets)
    svc.start()
    w = WebUI(svc, "127.0.0.1", 0)
    port = w.start()
    u = type("U", (), dict(svc=svc, web=w, c=Client(port), agent=agent, system=system, clock=clock, data=data,
                           code=open(svc.auth.code_file).read().strip()))
    u.sim = type("S", (), {"connect": lambda self: None})()

    def advance(seconds):
        end = clock.t + seconds
        while clock.t < end:
            clock.t += 1
            agent.tick()
    u.advance = advance
    prepare(u)
    yield u
    w.stop(); svc.stop(); agent.stop()
    shutil.rmtree(root, ignore_errors=True)


def fake_tree_dir(root):
    import pathlib
    p = pathlib.Path(root) / "tree"
    p.mkdir()
    return fake_tree(p)


def flash(u):
    return urllib.parse.unquote(u.c.cookies.get("bab_flash", ""))


def no_secrets(body):
    for s in SECRETS:
        assert s not in body, s


def test_overview_lists_network_and_the_page_shows_the_hotspot_and_scan(net_ui):
    u = net_ui
    assert "Network" in u.c.get("/settings").body and 'href="/settings/network"' in u.c.get("/settings").body
    u.advance(16)                                                  # no Wi-Fi configured, no Ethernet: hotspot after the grace
    page = u.c.get("/settings/network").body
    assert "Setup hotspot active" in page and "http://10.42.0.1/" in page and 'value="Home"' in page
    assert 'value="Cafe Gast"' in page and "not supported" in page and 'value="Versteckt"' not in page     # hidden SSIDs never listed
    assert "/static/network.js" in page and "/static/network.css" in page
    no_secrets(page)
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    assert "Setup hotspot active" in u.c.get("/settings").body                         # summary line


def test_join_pending_confirm_and_promote(net_ui):
    u = net_ui
    u.advance(16)
    r = u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    assert r.status == 303 and r.getheader("Location") == "/settings/network" and "please confirm" in flash(u)
    u.advance(4)
    page = u.c.get("/settings/network").body
    assert "Keep the new network settings?" in page and 'class="net-countdown" data-remaining="' in page and "Wi-Fi connected" in page
    assert "excellent signal" in page and "192.168.1.57/24" in page
    no_secrets(page)
    r = u.c.post("/settings/network/confirm")
    assert "Network settings kept" in flash(u) and N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["ssid"] == "Home"
    assert "Keep the new network settings?" not in u.c.get("/settings/network").body
    u.c.post("/settings/network/confirm")
    assert "no change waiting" in flash(u)


def test_wrong_password_is_rolled_back_and_explained(net_ui):
    u = net_ui
    u.advance(16)
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "not-the-password"})
    u.advance(61)
    page = u.c.get("/settings/network").body
    assert "Previous network settings restored" in page and "password was not accepted" in page and "Setup hotspot active" in page
    assert not os.path.exists(os.path.join(u.data, N.PENDING_NAME))


def test_unconfirmed_join_shows_the_address_it_had(net_ui):
    u = net_ui
    u.advance(16)
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(181)
    page = u.c.get("/settings/network").body
    assert "not confirmed in time" in page and "192.168.1.57" in page


def test_hidden_network_undo_and_keeping_the_stored_password(net_ui):
    u = net_ui
    u.advance(16)
    u.c.post("/settings/network/wifi", {"ssid": "__other__", "ssid_other": "Versteckt", "password": "hidden-pass-1", "hidden": "on"})
    cfg = u.svc.network.config()
    assert cfg["wifi"]["ssid"] == "Versteckt" and cfg["wifi"]["hidden"] is True
    u.c.post("/settings/network/revert")
    assert "Change undone" in flash(u) and u.svc.network.config()["wifi"] is None
    u.c.post("/settings/network/revert")
    assert "no change waiting" in flash(u)
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"}); u.advance(4); u.c.post("/settings/network/confirm")
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "", "hidden": "on"})            # toggle hidden without retyping
    assert u.svc.network.config()["wifi"]["psk"] == "wifi-secret-123" and u.svc.network.config()["wifi"]["hidden"] is True
    page = u.c.get("/settings/network").body
    assert "Leave empty to keep the stored password" in page and "hidden" in page
    no_secrets(page)


def test_join_errors_name_the_field(net_ui):
    u = net_ui
    u.c.post("/settings/network/wifi", {"ssid": "__other__", "ssid_other": "", "password": "short"})
    f = flash(u)
    assert f.startswith("e:") and "Network name: invalid" in f and "Wi-Fi password: invalid" in f and "short" not in f


def test_forget_wifi(net_ui):
    u = net_ui
    u.system.ethernet_link = True
    u.advance(2)
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})          # Ethernet owner: confirmed at once
    assert "saved" in flash(u) and N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["ssid"] == "Home"
    page = u.c.get("/settings/network").body
    assert "Saved network: “Home”" in page and 'action="/settings/network/forget"' in page and "data-confirm=" in page
    u.c.post("/settings/network/forget")
    assert N.load(os.path.join(u.data, N.FILE_NAME))["wifi"] is None


def test_addresses_page_static_validation_and_pending(net_ui):
    u = net_ui
    u.system.ethernet_link = True
    u.advance(2)
    page = u.c.get("/settings/network/addresses").body
    assert 'name="ethernet_ipv4_method"' in page and "Wi-Fi “" not in page
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    u.c.post("/settings/network/ip", {"iface": "ethernet", "ethernet_ipv4_method": "static", "ethernet_ipv4_address": "10.42.0.5/24",
                                      "ethernet_ipv6_method": "auto"})
    assert "Ethernet IPv4 address: overlaps the hotspot network" in flash(u)
    u.c.post("/settings/network/ip", {"iface": "ethernet", "ethernet_ipv4_method": "static", "ethernet_ipv4_address": "192.168.1.30/24",
                                      "ethernet_ipv4_gateway": "192.168.1.1", "ethernet_ipv4_dns": "192.168.1.1", "ethernet_ipv6_method": "off"})
    assert "please confirm" in flash(u)
    assert u.svc.network.config()["ethernet"]["ipv4"]["address"] == "192.168.1.30/24"
    page = u.c.get("/settings/network/addresses").body
    assert 'value="192.168.1.30/24"' in page
    u.c.post("/settings/network/ip", {"iface": "nonsense"})
    assert flash(u).startswith("e:")
    u.c.post("/settings/network/ip", {"iface": "wifi"})                                             # no Wi-Fi configured
    assert flash(u).startswith("e:")


def test_wifi_addresses_card_when_wifi_is_configured(net_ui):
    u = net_ui
    u.system.ethernet_link = True
    u.advance(2)
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    assert "Wi-Fi “Home”" in u.c.get("/settings/network/addresses").body
    u.c.post("/settings/network/ip", {"iface": "wifi", "wifi_ipv4_method": "dhcp", "wifi_ipv6_method": "static", "wifi_ipv6_address": "2001:db8::9/64"})
    assert u.svc.network.config()["wifi"]["ipv6"]["address"] == "2001:db8::9/64"


def test_hotspot_page_rename_keeps_the_password_new_password_and_remove(net_ui):
    u = net_ui
    u.system.ethernet_link = True
    u.advance(2)
    page = u.c.get("/settings/network/hotspot").body
    assert 'Hotspot “Hi-Fi Bridge 4F2A” is configured' in page and '<option value="DE" selected>' in page and 'value=""' in page
    no_secrets(page)
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    u.c.post("/settings/network/hotspot", {"country": "AT", "hotspot_ssid": "Setup Wohnzimmer", "hotspot_password": ""})
    cfg = N.load(os.path.join(u.data, N.FILE_NAME))
    assert cfg["hotspot"] == {"ssid": "Setup Wohnzimmer", "psk": HOTSPOT["psk"]} and cfg["country"] == "AT"
    u.c.post("/settings/network/hotspot", {"country": "AT", "hotspot_ssid": "Setup Wohnzimmer", "hotspot_password": "too-short"})
    assert "Hotspot password: 12 to 63 characters" in flash(u)
    u.c.post("/settings/network/hotspot", {"country": "AT", "hotspot_ssid": "Setup Wohnzimmer", "hotspot_password": "new-hotspot-pass"})
    assert N.load(os.path.join(u.data, N.FILE_NAME))["hotspot"]["psk"] == "new-hotspot-pass"
    no_secrets(u.c.get("/settings/network/hotspot").body)
    u.c.post("/settings/network/hotspot", {"country": "", "hotspot_ssid": "", "hotspot_password": ""})
    assert "Country: please choose a country" in flash(u)
    u.c.post("/settings/network/hotspot/remove")
    assert N.load(os.path.join(u.data, N.FILE_NAME))["hotspot"] is None
    assert "No setup hotspot is configured" in u.c.get("/settings/network/hotspot").body


def test_scan_post_and_api(net_ui):
    u = net_ui
    u.c.post("/settings/network/scan")
    assert "Searching for networks" in flash(u)
    assert 'data-scanning="1"' in u.c.get("/settings/network").body
    api = json.loads(u.c.get("/api/network").body)
    assert api["scan"]["pending"] is True and set(api) == {"state", "trial", "scan"}
    u.agent.tick()
    import time
    for _ in range(50):
        if not json.loads(u.c.get("/api/network").body)["scan"]["pending"]:
            break
        time.sleep(0.02)
    assert json.loads(u.c.get("/api/network").body)["scan"]["age_s"] == 0


def test_posts_need_csrf_and_a_session(net_ui):
    u = net_ui
    r = u.c.post("/settings/network/forget", csrf=False)
    assert r.getheader("Location") == "/login"
    anon = Client(u.web.port)
    assert anon.get("/settings/network").getheader("Location") == "/login" and anon.get("/api/network").status == 401


def test_german_page(net_ui):
    u = net_ui
    u.svc.store.update({"language": "de"})
    u.advance(16)
    page = u.c.get("/settings/network").body
    assert 'lang="de"' in page and "Einrichtungs-Hotspot aktiv" in page and "Netz wählen" in page
    assert "Einrichtungs-Hotspot und Land" in page and "Netzwerk" in u.c.get("/settings").body


def test_unmanaged_installation_shows_addresses_and_no_forms(tmp_path):
    from test_webui import P1  # noqa: F401  (same fixtures as the main WebUI tests)
    svc = ApplianceService(str(tmp_path / "d"), source=None, stream_port=0, lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None,
                           auth_iterations=1000)
    svc.network = C.NetworkService(str(tmp_path / "d"), None, interfaces=lambda: {
        "eth0": {"carrier": True, "mac": "m", "default_route": True, "addresses": [{"address": "172.18.0.5", "prefix": 16}]}})
    svc.start()
    w = WebUI(svc, "127.0.0.1", 0); port = w.start()
    u = type("U", (), dict(svc=svc, c=Client(port), code=open(svc.auth.code_file).read().strip()))
    try:
        finish_wizard(u)
        page = u.c.get("/settings/network").body
        assert "Managed by the host system" in page and "172.18.0.5/16" in page and 'action="/settings/network/wifi"' not in page
        assert u.c.get("/settings/network/addresses").getheader("Location") == "/settings/network"
        assert u.c.get("/settings/network/hotspot").getheader("Location") == "/settings/network"
        u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
        assert "no network service of its own" in flash(u)
        u.c.post("/settings/network/scan")
        assert flash(u).startswith("e:")
        assert "Managed by the host system" in u.c.get("/settings").body
        svc.network = None
        assert "Managed by the host system" in u.c.get("/settings/network").body
        u.c.post("/settings/network/forget")
        assert flash(u).startswith("e:")
    finally:
        w.stop(); svc.stop()


def test_agent_unreachable_and_rejected_change(net_ui, monkeypatch):
    u = net_ui
    monkeypatch.setattr(u.svc.network, "save", lambda new: {"result": "error", "error": "mismatch", "problems": []})
    u.c.post("/settings/network/forget")
    assert "changed meanwhile" in flash(u)

    def down(new):
        raise C.AgentUnavailable("x")
    monkeypatch.setattr(u.svc.network, "save", down)
    u.c.post("/settings/network/forget")
    assert "network service of the device is not reachable" in flash(u)

    def unmanaged(new):
        raise C.NotManaged()
    monkeypatch.setattr(u.svc.network, "save", unmanaged)
    u.c.post("/settings/network/forget")
    assert "no network service of its own" in flash(u)
    monkeypatch.setattr(u.svc.network, "status", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert "Network service not reachable" in u.c.get("/settings").body


@pytest.mark.parametrize("lang,busy,down", [("en", "did not answer in time", "Wait a minute and reload"),
                                            ("de", "nicht rechtzeitig geantwortet", "Warten Sie eine Minute")])
def test_an_agent_without_an_answer_tells_the_owner_what_to_do(net_ui, monkeypatch, lang, busy, down):
    """Review N-3/N-11: a save handed over without an answer is not "not reachable" (the agent may be applying it); every case says
    what to do next."""
    u = net_ui
    u.svc.store.update({"language": lang})

    def no_answer(new):
        raise C.AgentUnavailable("x", sent=True)
    monkeypatch.setattr(u.svc.network, "save", no_answer)
    r = u.c.post("/settings/network/hotspot", {"country": "DE", "hotspot_ssid": "x", "hotspot_password": ""})
    assert r.getheader("Location") == "/settings/network" and busy in flash(u)
    monkeypatch.setattr(u.svc.network, "save", lambda new: (_ for _ in ()).throw(C.AgentUnavailable("x")))
    r = u.c.post("/settings/network/hotspot", {"country": "DE", "hotspot_ssid": "x", "hotspot_password": ""})
    assert r.getheader("Location") == "/settings/network/hotspot" and down in flash(u)
    for action, method in (("confirm", "confirm"), ("revert", "revert")):
        monkeypatch.setattr(u.svc.network, method, lambda: None)
        u.c.post(f"/settings/network/{action}")
        assert flash(u).startswith("e:") and busy in flash(u)
    monkeypatch.setattr(u.svc.network, "status", lambda: {"managed": True, "state": "agent_unreachable", "interfaces": []})
    assert down in u.c.get("/settings/network").body


# ---------------------------------------------------------------- review N-1: network settings before the wizard is finished
def test_hotspot_only_onboarding_reaches_the_network_settings_before_the_wizard_is_finished(setup_ui):
    """Card written without (or with a mistyped) home Wi-Fi password, no Ethernet: the device opens the setup hotspot; the owner set the
    password with the setup code and must be able to join the home Wi-Fi BEFORE the wizard's Lyrion step can succeed."""
    u = setup_ui
    assert not u.svc.store.get("setup_complete")
    u.advance(16)                                                   # no Wi-Fi works, no Ethernet: setup hotspot
    r = u.c.get("/settings/network")
    assert r.status == 200 and "Setup hotspot active" in r.body and 'action="/settings/network/wifi"' in r.body
    assert 'href="/setup/network"' in r.body and "Back to the setup" in r.body and "setup is not finished yet" in r.body
    p = A11y(); p.feed(r.body)
    assert p.h1 == 1 and not p.problems, p.problems
    for sub in ("/settings/network/hotspot", "/settings/network/addresses"):
        assert u.c.get(sub).status == 200, sub
    assert u.c.get("/api/network").status == 200
    r = u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    assert r.getheader("Location") == "/settings/network" and "please confirm" in flash(u)
    u.advance(4)
    page = u.c.get("/setup/bluetooth").body                          # back at the new address: the wizard shows the banner too
    assert "Keep the new network settings?" in page and 'action="/setup/network/confirm"' in page     # Keep stays in the wizard
    u.c.post("/settings/network/confirm")
    assert "Network settings kept" in flash(u) and N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["ssid"] == "Home"
    assert "Keep the new network settings?" not in u.c.get("/setup/bluetooth").body
    for other in ("/settings", "/settings/lyrion", "/settings/update", "/settings/backup", "/", "/diagnostics"):
        assert u.c.get(other).getheader("Location") == "/setup/bluetooth", other           # everything else waits for the wizard
    no_secrets(u.c.get("/settings/network").body)


def test_a_mistyped_provisioned_wifi_password_can_be_fixed_before_the_wizard(setup_ui):
    u = setup_ui
    N.write_private(os.path.join(u.data, N.FILE_NAME), N.validate({"country": "DE", "hotspot": HOTSPOT,
                                                                  "wifi": {"ssid": "Home", "psk": "mistyped-pass"}}))
    u.agent.start()                                                  # the agent (re)reads network.json at its start
    u.advance(70)                                                    # wrong password -> hotspot after the fallback time
    page = u.c.get("/settings/network").body
    assert "Setup hotspot active" in page and "password was not accepted" in page
    u.c.post("/settings/network/wifi", {"ssid": "Home", "password": "wifi-secret-123"})
    u.advance(4)
    u.c.post("/settings/network/confirm")
    assert N.load(os.path.join(u.data, N.FILE_NAME))["wifi"]["psk"] == "wifi-secret-123"


def test_network_settings_before_the_wizard_keep_auth_and_csrf_as_strict(setup_ui):
    u = setup_ui
    r = u.c.post("/settings/network/forget", csrf=False)
    assert r.getheader("Location") == "/login"                        # same CSRF rule as on every other page
    anon = Client(u.web.port)
    assert anon.get("/settings/network").getheader("Location") == "/login" and anon.get("/api/network").status == 401
    assert anon.post("/settings/network/wifi", {"ssid": "Home"}, csrf=False).getheader("Location") == "/login"


def test_a_broken_network_service_never_breaks_the_wizard(setup_ui, monkeypatch):
    u = setup_ui
    monkeypatch.setattr(u.svc.network, "status", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = u.c.get("/setup/bluetooth")
    assert r.status == 200 and "Keep the new network settings?" not in r.body
    u.svc.network = None                                             # unmanaged installation (Docker lab without an agent)
    assert u.c.get("/setup/bluetooth").status == 200


def test_fresh_device_without_owner_password_sends_network_pages_to_the_setup():
    gen = make_net_ui(lambda u: None)
    u = next(gen)
    try:
        assert u.c.get("/settings/network").getheader("Location") == "/setup"           # the setup code comes first, as always
    finally:
        gen.close()


@pytest.mark.parametrize("lang,hint,link", [("en", "Not connected to your home network yet", "Back to the network step"),
                                            ("de", "Noch nicht mit Ihrem Heimnetz verbunden", "Zurück zum Netzwerk-Schritt")])
def test_the_wizards_lyrion_step_points_to_the_network_step(setup_ui, monkeypatch, lang, hint, link):
    u = setup_ui
    u.svc.store.update({"language": lang})
    monkeypatch.setattr(u.svc, "discover_servers", lambda timeout=1.5: [])        # behind the hotspot nothing answers
    page = u.c.get("/setup/lyrion").body
    assert hint in page and f'<a href="/setup/network">{link}</a>' in page             # the wizard's own network step (#122)
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    u.svc.store.update({"setup_complete": True})
    assert u.c.get("/settings/network").status == 200 and "Back to the setup" not in u.c.get("/settings/network").body


def test_every_network_key_exists_in_both_languages():
    import btbridge.webui.views_network as V
    en, de = i18n.strings("en"), i18n.strings("de")
    src = open(V.__file__).read()
    used = set(re.findall(r'"(net\.[a-z0-9_.]+)"', src)) - {"net.field.", "net.err.", "net.state.", "net.signal.", "net.reason.", "net.time.",
                                                            "net.firewall.", "net.security."}
    missing = sorted(k for k in used if k not in en and not k.endswith("."))
    assert not missing, missing
    for prefix, values in (("net.state.", ("connected", "connecting", "probing", "hotspot", "off", "unavailable", "unmanaged", "agent_unreachable")),
                           ("net.reason.", ("wrong_password", "not_found", "rejected", "no_address", "timeout", "not_confirmed", "reverted")),
                           ("net.signal.", ("excellent", "good", "fair", "weak", "unknown")),
                           ("net.security.", ("wpa2", "wpa2/wpa3", "wpa3", "wpa", "enterprise", "wep", "open"))):
        for v in values:
            assert prefix + v in en and prefix + v in de, prefix + v
    assert set(k for k in en if k.startswith("net.")) == set(k for k in de if k.startswith("net."))


def test_signal_words():
    from btbridge.webui.views_network import signal_word
    assert [signal_word(x) for x in (None, -40, -60, -70, -90)] == ["unknown", "excellent", "good", "fair", "weak"]
