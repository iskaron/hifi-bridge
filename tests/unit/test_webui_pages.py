"""UNIT (real HTTP server on localhost, SIMULATED phone, FAKE LMS): the owner pages in the situations that go wrong - a mistyped setting, an
unreachable Lyrion, a refused adapter, a wrong old password - and the settings that had no page test at all (logging, Bluetooth,
appearance, MQTT controls, stream token). Every refusal must change nothing and tell the owner what to do. Coverage package 7b-3."""
import urllib.parse

import pytest
from test_service import P1
from test_webui import PW, finish_wizard, setup_account, ui  # noqa: F401  (fixture)

from btbridge.lms.client import LmsUnreachable


def flash(c) -> str:
    return urllib.parse.unquote(c.cookies.get("bab_flash", "")).replace("+", " ")


def pw_is(ui, pw: str) -> bool:
    from btbridge.webui.auth import verify_password
    return verify_password(pw, ui.svc.store.secret("admin_password_hash"))


def location(r) -> str:
    return r.getheader("Location")


# ---------------------------------------------------------------- start pages and the language switch
def test_login_and_setup_pages_lead_to_each_other_depending_on_the_device_state(ui):
    assert location(ui.c.get("/login")) == "/setup"                          # no owner password yet: nothing to log in to
    setup_account(ui)
    assert location(ui.c.get("/login")) == "/"                               # already logged in
    ui.c.cookies.clear()
    assert location(ui.c.get("/login")) is None                              # logged out: the login form
    r = ui.c.post("/setup", {"code": ui.code, "password": PW, "confirm": PW}, csrf=False)
    assert location(r) == "/login"                                           # a second setup is not possible


def test_the_language_choice_is_stored_for_the_owner_and_ignores_unknown_languages(ui):
    finish_wizard(ui)
    r = ui.c.post("/lang", {"lang": "en"}, headers={"Referer": f"http://127.0.0.1:{ui.port}/settings"})
    assert location(r) == "/settings" and ui.svc.store.get("language") == "en"
    ui.c.post("/lang", {"lang": "de"})
    assert ui.svc.store.get("language") == "de"
    r = ui.c.post("/lang", {"lang": "tlh"})
    assert ui.svc.store.get("language") == "de" and "tlh" not in str(r.headers_list)               # unchanged, no cookie


def test_a_weak_owner_password_is_refused_with_the_reasons_and_changes_nothing(ui):
    r = ui.c.post("/setup", {"code": ui.code, "password": "short", "confirm": "short"}, csrf=False)
    assert location(r) == "/setup" and not ui.svc.auth.has_password()
    assert "pass" in flash(ui.c).lower()                                      # the text names what is wrong, not a bare "error"


# ---------------------------------------------------------------- wizard refusals
def test_wizard_bluetooth_name_that_is_not_accepted_stays_on_the_step_with_a_message(ui):
    setup_account(ui)
    r = ui.c.post("/setup/bluetooth", {"device_name": "x" * 300})
    assert location(r) == "/setup/bluetooth" and flash(ui.c)
    assert ui.svc.store.get("device_name") != "x" * 300


def test_wizard_lyrion_step_refuses_an_empty_host_and_an_unreachable_server(ui):
    setup_account(ui)
    r = ui.c.post("/setup/lyrion", {"lms_host": "", "lms_port": "9000"})
    assert location(r) == "/setup/lyrion" and ui.svc.store.get("lms_host") == ""
    ui.svc.lms_factory = lambda url: (_ for _ in ()).throw(LmsUnreachable("connection refused"))
    r = ui.c.post("/setup/lyrion", {"lms_host": "10.9.9.9", "lms_port": "9000"})
    assert location(r) == "/setup/lyrion" and "10.9.9.9:9000" in flash(ui.c)
    assert ui.svc.store.get("lms_host") != "10.9.9.9"                          # a server that does not answer is not saved


def test_wizard_player_step_explains_an_unreachable_or_empty_server_instead_of_failing(ui):
    setup_account(ui)
    ui.svc.store.update({"lms_host": "10.0.0.5"})
    ui.svc.list_targets = lambda *a, **k: (_ for _ in ()).throw(LmsUnreachable("down"))
    page = ui.c.get("/setup/target")
    assert page.status == 200 and 'class="error"' in page.body
    ui.svc.list_targets = lambda *a, **k: []
    page = ui.c.get("/setup/target")
    assert page.status == 200 and 'class="muted"' in page.body and 'name="target_player"' not in page.body


def test_choosing_a_player_that_is_not_accepted_stays_on_the_step(ui):
    setup_account(ui)
    r = ui.c.post("/setup/target", {"target_player": "x" * 400})
    assert location(r) == "/setup/target" and ui.svc.store.get("target_player") != "x" * 400
    ui.svc.list_targets = lambda *a, **k: (_ for _ in ()).throw(LmsUnreachable("down"))
    r = ui.c.post("/setup/target", {"target_player": P1})                    # the name lookup may fail: the id is still saved
    assert location(r) == "/setup/finish" and ui.svc.store.get("target_player") == P1


# ---------------------------------------------------------------- dashboard and pairing
def test_a_refused_pairing_close_says_so_and_a_label_for_an_invalid_address_is_refused(ui):
    finish_wizard(ui)
    ui.svc.close_pairing = lambda: False                                       # the adapter refused (retried in the background)
    r = ui.c.post("/pairing/close", {"back": "/pairing"})
    assert location(r) == "/pairing" and flash(ui.c)
    r = ui.c.post("/devices/label", {"address": "not-a-mac", "label": "Anna"})
    assert location(r) == "/pairing" and ui.svc.store.get("device_labels") == {}
    ui.c.post("/devices/label", {"address": "AA:BB:CC:DD:EE:01", "label": "L" * 400})
    assert ui.svc.store.get("device_labels") == {"AA:BB:CC:DD:EE:01": "L" * 40}                    # long labels are shortened, not refused


# ---------------------------------------------------------------- settings that had no page test
def test_logging_bluetooth_appearance_and_mqtt_control_settings_are_saved_and_shown(ui):
    finish_wizard(ui)
    r = ui.c.post("/settings/logging", {"syslog_host": "logs.example", "syslog_port": "6514", "syslog_enabled": "on"})
    assert location(r) == "/settings/logging" and ui.svc.store.get("syslog_host") == "logs.example"
    assert "logs.example" in ui.c.get("/settings/logging").body
    r = ui.c.post("/settings/bluetooth", {"device_name": "Küche", "pairing_seconds": "120"})
    assert location(r) == "/settings/bluetooth" and ui.svc.store.get("device_name") == "Küche"
    assert ui.svc.runtime.pairing.duration == 120.0
    r = ui.c.post("/settings/appearance", {"language": "en", "theme": "dark"})
    assert location(r) == "/settings/appearance" and ui.svc.store.get("theme") == "dark"
    ui.svc.store.update({"mqtt_enabled": True, "mqtt_host": "broker.lan"})
    summary = ui.c.get("/settings").body
    assert "broker.lan" in summary                                             # read-only: no controls enabled yet
    ui.c.post("/settings/mqtt/controls", {"mqtt_allow_pairing": "on", "mqtt_allow_test": "on"})
    assert ui.svc.store.get("mqtt_allow_pairing") and not ui.svc.store.get("mqtt_allow_audio") and ui.svc.store.get("mqtt_allow_test")
    assert "broker.lan" in ui.c.get("/settings").body


def test_a_name_with_a_long_unaccepted_value_keeps_the_old_one(ui):
    finish_wizard(ui)
    old = ui.svc.store.get("device_name")
    r = ui.c.post("/settings/bluetooth", {"device_name": "n" * 300, "pairing_seconds": "120"})
    assert location(r) == "/settings/bluetooth" and ui.svc.store.get("device_name") == old


def test_rotating_the_stream_token_replaces_the_secret(ui):
    finish_wizard(ui)
    old = ui.svc.store.secret("stream_token")
    r = ui.c.post("/settings/token")
    assert location(r) == "/settings/security" and ui.svc.store.secret("stream_token") != old


def test_changing_the_password_needs_a_matching_confirmation_and_the_right_old_password(ui):
    finish_wizard(ui)
    r = ui.c.post("/settings/password", {"old": PW, "new": "another long passphrase", "confirm": "a different one"})
    assert location(r) == "/settings/security" and pw_is(ui, PW)
    r = ui.c.post("/settings/password", {"old": "not the old one", "new": "another long passphrase", "confirm": "another long passphrase"})
    assert location(r) == "/settings/security" and pw_is(ui, PW) and not pw_is(ui, "another long passphrase")


# ---------------------------------------------------------------- diagnostics and local access
def test_the_lyrion_check_button_reports_not_configured_reachable_and_unreachable(ui):
    finish_wizard(ui)
    ui.svc.store.update({"lms_host": ""})
    r = ui.c.post("/diagnostics/check")
    assert location(r) == "/diagnostics" and flash(ui.c)
    ui.svc.store.update({"lms_host": "10.0.0.5", "lms_port": 9000})
    r = ui.c.post("/diagnostics/check")
    assert location(r) == "/diagnostics" and "9.1.2" in flash(ui.c)
    ui.svc.lms_factory = lambda url: (_ for _ in ()).throw(LmsUnreachable("refused"))
    r = ui.c.post("/diagnostics/check")
    assert location(r) == "/diagnostics" and "10.0.0.5:9000" in flash(ui.c)


def test_without_a_local_ca_the_certificate_downloads_and_renewal_are_not_offered(ui):
    finish_wizard(ui)
    assert ui.svc.access is None                                              # plain-HTTP unit rig: no local access manager
    assert ui.c.get("/ca.crt").status == 404 and ui.c.get("/ca.der").status == 404
    r = ui.c.post("/settings/access/renew")
    assert location(r) == "/settings/access" and flash(ui.c)
    r = ui.c.post("/settings/access/tls", {"tls_mode": "local_ca"})
    assert r.status == 303
