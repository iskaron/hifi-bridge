"""UNIT (real HTTP server on localhost, SIMULATED phone, FAKE LMS): WebUI security, wizard, pages, i18n, accessibility basics."""
import http.client
import json
import os
import re
import urllib.parse
from html.parser import HTMLParser

import pytest
from fakes import FakeLms
from test_service import P1, P2, Lmses, fake_tree

from btbridge.config.schema import SETTINGS
from btbridge.service import ApplianceService
from btbridge.webui import i18n
from btbridge.webui.server import WebUI
from btkit.sim_a2dp import SimulatedA2dpSource

PW = "a good long passphrase"


class Client:
    def __init__(self, port):
        self.port, self.cookies = port, {}

    def req(self, method, path, form=None, headers=None, raw_body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = raw_body
        if form is not None:
            body = urllib.parse.urlencode(form)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                name, val = v.split(";")[0].split("=", 1)
                if "Max-Age=0" in v:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = val
        r.body, r.headers_list = data, r.getheaders()
        c.close()
        return r

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, form=None, csrf=True, **kw):
        form = dict(form or {})
        if csrf:
            form.setdefault("csrf", self.csrf())
        return self.req("POST", path, form=form, **kw)

    def csrf(self):
        m = re.search(r'name="csrf" value="([^"]+)"', self.get("/").body + self.get("/setup/bluetooth").body)
        return m.group(1) if m else ""


@pytest.fixture
def ui(tmp_path):
    lmses = Lmses(); sim = SimulatedA2dpSource()
    svc = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge", lms_factory=lmses,
                           monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000,
                           discover=lambda timeout: [{"address": "10.0.0.5", "JSON": "9000", "NAME": "Living room", "VERS": "9.1.2"}])
    svc.start()
    w = WebUI(svc, "127.0.0.1", 0); port = w.start()
    yield type("U", (), dict(svc=svc, web=w, c=Client(port), port=port, sim=sim, lmses=lmses, code=open(svc.auth.code_file).read().strip()))
    w.stop(); svc.stop()


def setup_account(u):
    r = u.c.post("/setup", {"code": u.code, "password": PW, "confirm": PW}, csrf=False)
    assert r.status == 303 and r.getheader("Location") == "/setup/bluetooth"
    return r


def finish_wizard(u):
    setup_account(u)
    assert u.c.post("/setup/bluetooth", {"device_name": "Wohnzimmer"}).getheader("Location") == "/setup/lyrion"
    assert u.c.post("/setup/lyrion", {"lms_host": "10.0.0.5", "lms_port": "9000"}).getheader("Location") == "/setup/target"
    assert u.c.post("/setup/target", {"target_player": P1}).getheader("Location") == "/setup/finish"
    assert u.c.post("/setup/finish").getheader("Location") == "/"


# ---------------------------------------------------------------- security
def test_fresh_device_sends_everyone_to_setup_with_strict_headers(ui):
    r = ui.c.get("/")
    assert r.status == 303 and r.getheader("Location") == "/setup"
    r = ui.c.get("/setup")
    assert r.status == 200 and 'name="code"' in r.body and 'autocomplete="new-password"' in r.body
    csp = r.getheader("Content-Security-Policy")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert r.getheader("X-Content-Type-Options") == "nosniff" and r.getheader("X-Frame-Options") == "DENY"
    assert r.getheader("Cache-Control") == "no-store" and r.getheader("Referrer-Policy") == "same-origin"
    assert r.getheader("Cross-Origin-Opener-Policy") is None                         # only sent over TLS
    assert ui.c.get("/api/status").status == 401


def test_setup_rejects_wrong_code_and_mismatch_then_sets_a_strict_session_cookie(ui):
    r = ui.c.post("/setup", {"code": "AAAA-BBBB-CCCC", "password": PW, "confirm": PW}, csrf=False)
    assert r.getheader("Location") == "/setup" and "Wrong setup code" in urllib.parse.unquote(ui.c.cookies["bab_flash"])
    r = ui.c.post("/setup", {"code": ui.code, "password": PW, "confirm": PW + "x"}, csrf=False)
    assert not ui.svc.auth.has_password()
    r = setup_account(ui)
    cookie = next(v for k, v in r.headers_list if k == "Set-Cookie" and v.startswith("bab_session="))
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert ui.c.get("/setup").getheader("Location") == "/setup/bluetooth"          # setup cannot be repeated
    assert ui.c.get("/").getheader("Location") == "/setup/bluetooth"               # wizard must be finished first


def test_state_changes_need_csrf_and_same_origin(ui):
    setup_account(ui)
    r = ui.c.post("/setup/bluetooth", {"device_name": "Evil"}, csrf=False)
    assert r.status == 303 and r.getheader("Location") == "/login" and ui.svc.store.get("device_name") == "Hi-Fi Bridge"
    r = ui.c.post("/setup/bluetooth", {"device_name": "Evil"}, headers={"Origin": "http://evil.example"})
    assert r.status == 403 and ui.svc.store.get("device_name") == "Hi-Fi Bridge"
    r = ui.c.post("/setup/bluetooth", {"device_name": "Fine"}, headers={"Origin": f"http://127.0.0.1:{ui.port}"})
    assert r.status == 303 and ui.svc.store.get("device_name") == "Fine"
    # 'Origin: null' (privacy contexts): accepted only when the browser itself says same-origin
    assert ui.c.post("/setup/bluetooth", {"device_name": "Null1"}, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"}).status == 403
    assert ui.c.post("/setup/bluetooth", {"device_name": "Null2"}, headers={"Origin": "null"}).status == 403
    r = ui.c.post("/setup/bluetooth", {"device_name": "Null3"}, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert r.status == 303 and ui.svc.store.get("device_name") == "Null3"
    assert ui.c.post("/login", {"password": "x"}, csrf=False, headers={"Sec-Fetch-Site": "cross-site"}).status == 403   # login CSRF
    ui.c.cookies["bab_session"] = "forged"
    assert ui.c.get("/api/status").status == 401


def test_login_logout_and_rate_limit(ui):
    finish_wizard(ui)
    ui.c.post("/logout")
    assert ui.c.get("/").getheader("Location") == "/login"
    for _ in range(5):
        ui.c.post("/login", {"password": "wrong"}, csrf=False)
    r = ui.c.post("/login", {"password": PW}, csrf=False)
    assert "bab_session" not in ui.c.cookies and "Too many attempts" in urllib.parse.unquote(ui.c.cookies.get("bab_flash", ""))
    ui.svc.auth.limiter.ok("127.0.0.1")
    ui.c.post("/login", {"password": PW}, csrf=False)
    assert ui.c.get("/").status == 200


def test_static_files_and_no_traversal(ui):
    r = ui.c.get("/static/logo.svg")
    assert r.status == 200 and r.getheader("Content-Type") == "image/svg+xml" and "<svg" in r.body
    assert ui.c.get("/static/app.css").getheader("Content-Type").startswith("text/css")
    for bad in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/", "/static/.hidden", "/static/sub/x.css"):
        assert ui.c.get(bad).status == 404, bad
    assert ui.c.get("/nope").status == 404 and ui.c.req("PUT", "/").status in (404, 501)
    assert ui.c.get("/setup/finish", headers={}).status in (303,)
    assert ui.c.req("POST", "/system", form={}).status == 405


def test_body_size_limit(ui):
    setup_account(ui)
    r = ui.c.req("POST", "/setup/bluetooth", raw_body="x" * (70 * 1024), headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status == 400


# ---------------------------------------------------------------- wizard + pages
def test_test_sound_button_works_inside_the_wizard(ui):
    setup_account(ui)
    ui.c.post("/setup/lyrion", {"lms_host": "10.0.0.5", "lms_port": "9000"}); ui.c.post("/setup/target", {"target_player": P1})
    r = ui.c.post("/diagnostics/test", {"back": "/setup/finish"})
    assert r.getheader("Location") == "/setup/finish"                          # not bounced to the wizard start (regression)
    assert ui.c.get("/diagnostics").getheader("Location") == "/setup/bluetooth"  # other pages stay locked until the wizard is done


def test_full_wizard_configures_the_bridge(ui):
    setup_account(ui)
    assert "Living room" in ui.c.get("/setup/lyrion").body                       # discovered server offered
    r = ui.c.post("/setup/lyrion", {"lms_host": "bad host!", "lms_port": "9000"})
    assert r.getheader("Location") == "/setup/lyrion"
    ui.c.post("/setup/bluetooth", {"device_name": "Wohnzimmer"})
    ui.c.post("/setup/lyrion", {"lms_host": "10.0.0.5", "lms_port": "9000"})
    page = ui.c.get("/setup/target").body
    assert "Kitchen" in page and 'type="radio"' in page
    ui.c.post("/setup/target", {"target_player": P1}); ui.c.post("/setup/finish")
    s = ui.svc.store
    assert s.get("setup_complete") and s.get("device_name") == "Wohnzimmer" and s.get("lms_host") == "10.0.0.5"
    assert s.get("target_player") == P1 and s.get("target_label") == "Kitchen" and ui.svc.configured()
    dash = ui.c.get("/").body
    assert "Kitchen" in dash and 'data-live="1"' in dash and "Wohnzimmer" in dash
    assert all(f.commands == [] for f in ui.lmses.by_url.values())              # wizard never controls LMS


def test_dashboard_actions_pairing_audio_and_test_sound(ui):
    finish_wizard(ui)
    ui.c.post("/pairing/open")
    assert ui.sim.pairable and 'id="live-pairing"' in ui.c.get("/").body
    ui.c.post("/pairing/close"); assert not ui.sim.pairable
    ui.c.post("/audio", {"value": "off"}); assert ui.svc.store.get("audio_allowed") is False
    ui.c.post("/audio", {"value": "on"}); assert ui.svc.store.get("audio_allowed") is True
    lms = ui.lmses.by_url["http://10.0.0.5:9000"]
    lms.user_plays(P1, "http://radio/x")
    r = ui.c.post("/diagnostics/test", {"back": "/"})
    assert "refused" in urllib.parse.unquote(ui.c.cookies["bab_flash"]) and lms.commands == []   # busy target: refused, untouched
    assert ui.c.post("/diagnostics/test", {"back": "//evil.example"}).getheader("Location") == "/diagnostics"  # no open redirect


def test_settings_validation_mqtt_secret_and_password_change(ui, tmp_path):
    finish_wizard(ui)
    r = ui.c.post("/settings/lyrion", {"lms_host": "10.0.0.5", "lms_port": "99999"})
    assert "too large" in urllib.parse.unquote(ui.c.cookies["bab_flash"]) and ui.svc.store.get("lms_port") == 9000
    ui.c.post("/settings/mqtt", {"mqtt_host": "broker.lan", "mqtt_port": "1883", "mqtt_username": "u", "mqtt_password": "s3cret-mqtt",
                                 "mqtt_base_topic": "btbridge", "mqtt_discovery_prefix": "homeassistant"})
    assert ui.svc.store.secret("mqtt_password") == "s3cret-mqtt" and ui.svc.store.get("mqtt_enabled") is False
    assert "s3cret-mqtt" not in open(os.path.join(ui.svc.store.dir, "settings.json")).read()
    assert "s3cret-mqtt" not in ui.c.get("/settings").body                      # never echoed back
    ui.c.post("/settings/playback", {"pause_strategy": "keepalive_silence", "stream_format": "wav"})
    assert ui.svc.store.get("override_lms") is False and ui.svc.store.get("icy_metadata") is False      # unchecked boxes
    other = Client(ui.port); other.post("/login", {"password": PW}, csrf=False); assert other.get("/").status == 200
    ui.c.post("/settings/password", {"old": PW, "new": "a different passphrase", "confirm": "a different passphrase"})
    assert ui.c.get("/settings").status == 200 and other.get("/").getheader("Location") == "/login"


def test_pairing_page_lists_labels_and_removes_devices(ui):
    finish_wizard(ui); ui.sim.connect()
    ui.c.post("/devices/label", {"address": "AA:BB:CC:DD:EE:FF", "label": "Gast"})
    assert ui.svc.store.get("device_labels") == {"AA:BB:CC:DD:EE:FF": "Gast"}
    page = ui.c.get("/pairing").body
    assert "sim-phone-1" in page and "data-confirm" in page
    ui.c.post("/devices/remove", {"address": "sim-phone-1"})
    assert ui.svc.paired_devices() == []


def test_api_status_and_diagnostics_download_contain_no_secrets(ui):
    finish_wizard(ui)
    st = json.loads(ui.c.get("/api/status").body)
    assert st["headline"] in ("ready", "lyrion_unreachable", "connected") and st["setup_complete"] is True
    r = ui.c.get("/api/diagnostics")
    assert "attachment" in r.getheader("Content-Disposition")
    for secret in (ui.svc.store.secret("stream_token"), ui.svc.store.secret("admin_password_hash"), ui.svc.store.secret("session_key")):
        assert secret not in r.body and secret not in ui.c.get("/diagnostics").body


def test_output_is_html_escaped(ui):
    finish_wizard(ui)
    ui.svc.store.update({"device_name": '<script>x</script>"'})
    body = ui.c.get("/").body
    assert "<script>x</script>" not in body and "&lt;script&gt;x&lt;/script&gt;&quot;" in body


def test_languages_follow_setting_cookie_and_browser(ui):
    assert "Willkommen" in ui.c.get("/setup", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}).body
    assert "Welcome" in ui.c.get("/setup", headers={"Accept-Language": "fr-FR"}).body
    ui.c.req("POST", "/lang", form={"lang": "de"}, headers={"Referer": f"http://127.0.0.1:{ui.port}/setup"})
    assert "Willkommen" in ui.c.get("/setup").body
    finish_wizard(ui)
    ui.c.post("/settings/appearance", {"language": "en", "theme": "dark"})
    page = ui.c.get("/", headers={"Accept-Language": "de"}).body
    assert 'lang="en"' in page and 'data-theme="dark"' in page and "Pair a phone" in page


# ---------------------------------------------------------------- i18n completeness
def test_dictionaries_have_identical_keys_and_no_empty_strings():
    en, de = i18n.strings("en"), i18n.strings("de")
    assert set(en) == set(de)
    assert all(v.strip() for v in list(en.values()) + list(de.values()))
    for k in en:                                                      # same placeholders in both languages
        assert set(re.findall(r"{(\w+)}", en[k])) == set(re.findall(r"{(\w+)}", de[k])), k


def test_every_key_used_by_the_ui_exists():
    import btbridge.webui.views as V
    en = i18n.strings("en")
    src = open(V.__file__).read()
    literal = set(re.findall(r'\bt\("([a-z0-9_.]+)"', src)) | set(re.findall(r'"((?:nav|dash|wizard|pairing|settings|diag|sys)\.[a-z0-9_.]+)"', src))
    missing = sorted(k for k in literal if k not in en and not k.endswith("."))
    assert not missing, missing
    from btbridge.service import ApplianceService as A
    heads = set(re.findall(r'return "([a-z_]+)"', open(A.__init__.__code__.co_filename).read().split("def _headline")[1].split("def ")[0]))
    for h in heads:
        assert f"headline.{h}" in en and f"headline.{h}.detail" in en, h
    for v in ("connected", "disconnected", "playing", "paused", "idle", "allowed", "blocked", "reachable", "unreachable", "unknown", "none",
              "owned", "lost", "closed", "parked", "streaming", "audio_disabled", "override_disabled", "notification_filter", "lyrion_unreachable"):
        assert f"state.{v}" in en, v
    for key, s in SETTINGS.items():
        assert f"field.{key}" in en, key
        for c in s.choices or ():
            assert f"choice.{key}.{c}" in en, (key, c)
    schema_src = open(SETTINGS["lms_host"].check.__code__.co_filename).read()
    for code in set(re.findall(r'"((?:invalid|too)_[a-z_]+)"', schema_src)) | {"unknown_setting", "invalid"}:
        assert f"error.{code}" in en, code
    for r in ("bridge_session_active", "lyrion_unreachable", "target_busy", "start_failed", "not_configured"):
        assert f"refusal.{r}" in en


# ---------------------------------------------------------------- accessibility basics (structural, not a full audit)
class A11y(HTMLParser):
    def __init__(self):
        super().__init__(); self.problems, self.h1, self.in_label, self.html_lang = [], 0, 0, None
        self.ids_labelled = set(); self.inputs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_lang = a.get("lang")
        if tag == "h1":
            self.h1 += 1
        if tag == "label":
            self.in_label += 1
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden" and "hidden" not in a:
            if not (self.in_label or a.get("aria-label")):
                self.problems.append(f"unlabelled {tag} {a.get('name')}")
        if tag == "img" and "alt" not in a:
            self.problems.append("img without alt")
        if tag == "button" and a.get("aria-pressed") not in (None, "true", "false"):
            self.problems.append("bad aria-pressed")

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1


def test_pages_have_lang_one_h1_and_labelled_controls(ui):
    pages = {"/setup": None}
    p = A11y(); p.feed(ui.c.get("/setup").body)
    assert p.html_lang == "en" and p.h1 == 1 and not p.problems, p.problems
    finish_wizard(ui); ui.sim.connect()
    for path in ("/", "/pairing", "/settings", "/diagnostics", "/system", "/setup/lyrion", "/setup/target", "/setup/finish"):
        p = A11y(); p.feed(ui.c.get(path).body)
        assert p.html_lang in ("en", "de") and p.h1 == 1 and not p.problems, (path, p.problems)
