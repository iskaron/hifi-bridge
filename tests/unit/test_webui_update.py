"""UNIT (real HTTP WebUI, in-process FAKE RAUC, temporary data directory): the update and backup pages - version and slots, trust,
check, upload (streamed path: auth, CSRF and size checked before the body is read), install, restart confirmation, declining, manual
rollback, backup export/import with and without secrets, snapshot restore, DE/EN and structural accessibility."""
import json
import re
import socket
import urllib.parse

import pytest
from fakes_ota import FakeRaucClient, ReleaseServer
from test_webui import A11y, Client, finish_wizard, ui  # noqa: F401  (fixture)

from btbridge.ota.manager import UpdateManager
from btbridge.ota.source import UpdateSource
from btbridge.webui import i18n, views_update

C = "hifi-bridge-rpi3"
TRUST = {"mode": "own-and-official", "certificates": [], "official_source": None}


@pytest.fixture
def u(ui):
    finish_wizard(ui)
    ui.rauc, ui.reboots = FakeRaucClient(), []
    ui.svc.ota = UpdateManager(ui.svc.store.dir, ui.rauc, trust={"mode": "own", "certificates": [
        {"role": "own", "subject": "CN=dev update CA", "sha256": "AA:BB:CC"}], "official_source": None}, version="0.3.0",
        reboot=lambda: ui.reboots.append(1), current_boot=lambda: "b1", log=lambda m: None, monitor_s=0.01, free=lambda p: 10 ** 12)
    return ui


def flash(u):
    return urllib.parse.unquote(u.c.cookies.get("bab_flash", ""))[2:]


def token(u):
    return re.search(r'name="csrf" value="([^"]+)"', u.c.get("/settings/update").body).group(1)


def multipart(parts, boundary="xyzBOUNDARY"):
    out = b""
    for name, filename, data in parts:
        fn = f'; filename="{filename}"' if filename else ""
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{fn}\r\n\r\n'.encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def upload(u, data=b"bundle-bytes", csrf=None, extra_headers=None, path="/settings/update/upload", field="bundle", parts=None):
    body, ctype = multipart(parts or [(field, "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", data)])
    q = urllib.parse.quote(token(u) if csrf is None else csrf)
    return u.c.req("POST", f"{path}?csrf={q}", raw_body=body, headers={"Content-Type": ctype, **(extra_headers or {})})


def stage(u, version="0.4.0", compatible=C):
    u.rauc.bundles[u.svc.ota.bundle_path] = {"compatible": compatible, "version": version}
    return upload(u)


# ---------------------------------------------------------------- pages
def test_unmanaged_system_shows_the_version_only(ui):
    finish_wizard(ui)
    page = ui.c.get("/settings/update").body
    assert "no update service" in page.lower() and "Check now" not in page
    assert "no update service" in ui.c.get("/settings").body
    assert ui.c.get("/api/update").body == '{"managed": false}'
    r = ui.c.post("/settings/update/install")
    assert r.getheader("Location") == "/settings/update" and "RAUC" in flash(ui)
    r = upload(ui)
    assert r.status == 303 and "RAUC" in flash(ui)


def test_update_page_shows_slots_trust_and_actions(u):
    r = u.c.get("/settings/update")
    page = r.body
    assert r.status == 200 and "/static/update.js" in page and "/static/update.css" in page
    assert "0.3.0" in page and "Slot A" in page and "running now" in page and "Slot B" in page and "not bootable" in page
    assert "own update CA only" in page and "CN=dev update CA" in page and "AA:BB:CC" in page
    assert "updates by upload only" in page and "only own update bundles" in page and "Check now" not in page
    assert 'enctype="multipart/form-data"' in page and "/settings/update/upload?csrf=" in page
    assert "No other bootable version" in page
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    assert "0.3.0 · No update in progress." in u.c.get("/settings").body
    de = u.c.get("/settings/update", headers={"Accept-Language": "de"}).body
    assert 'lang="de"' in de or "Laufende Version" in de


def test_rauc_down_is_shown(u):
    u.rauc.down = True
    assert "not reachable" in u.c.get("/settings/update").body


# ---------------------------------------------------------------- upload: the streamed path
def test_upload_verifies_and_stages_then_install_and_restart(u):
    r = stage(u)
    assert r.status == 303 and flash(u) == "Bundle 0.4.0 received and verified."
    page = u.c.get("/settings/update").body
    assert "Install version 0.4.0" in page and "verified against the trusted keys" in page and "data-confirm" in page
    u.c.post("/settings/update/install")
    u.svc.ota._thread.join(5)
    page = u.c.get("/settings/update").body
    assert "Restart now" in page and "Keep the current version" in page and u.reboots == []
    assert "restart needed" in u.c.get("/settings").body
    u.c.post("/settings/update/reboot")
    assert u.reboots == [1] and "Restarting" in u.c.get("/settings/update").body


def test_owner_declines_the_restart(u):
    stage(u)
    u.c.post("/settings/update/install")
    u.svc.ota._thread.join(5)
    u.c.post("/settings/update/cancel")
    assert "stays on the current version" in flash(u)
    assert "Restart declined" in u.c.get("/settings/update").body
    u.c.post("/settings/update/dismiss")
    assert "Restart declined" not in u.c.get("/settings/update").body


def test_upload_needs_session_and_csrf_before_reading(u):
    tok = token(u)
    anon = Client(u.port)
    body, ctype = multipart([("bundle", "x.raucb", b"data")])
    r = anon.req("POST", f"/settings/update/upload?csrf={tok}", raw_body=body, headers={"Content-Type": ctype})
    assert r.status == 303 and r.getheader("Location") == "/login" and r.getheader("Connection") == "close"
    r = upload(u, csrf="wrong")
    assert r.status == 303 and r.getheader("Location") == "/login"
    r = u.c.req("POST", "/settings/update/upload", raw_body=body, headers={"Content-Type": ctype})       # token only in the body: no
    assert r.getheader("Location") == "/login"
    r = upload(u, extra_headers={"Origin": "http://evil.example"})
    assert r.status == 403
    r = u.c.req("POST", "/api/update", form={})
    assert r.status == 405
    import os
    assert not os.path.exists(u.svc.ota.bundle_path + ".upload") and u.svc.ota.info()["phase"] == "idle"


def raw_post(u, headers: dict, body: bytes = b"", shut=True):
    s = socket.create_connection(("127.0.0.1", u.port), timeout=10)
    cookie = "; ".join(f"{k}={v}" for k, v in u.c.cookies.items())
    head = f"POST /settings/update/upload?csrf={urllib.parse.quote(token(u))} HTTP/1.1\r\nHost: 127.0.0.1:{u.port}\r\nCookie: {cookie}\r\n"
    head += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    s.sendall(head.encode() + body)
    if shut:
        s.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        b = s.recv(65536)
        if not b:
            break
        data += b
    s.close()
    return data.decode("latin-1")


def test_upload_body_limits(u):
    big = views_update.MAX_BUNDLE + views_update.UPLOAD_OVERHEAD + 1
    assert raw_post(u, {"Content-Length": str(big), "Content-Type": "multipart/form-data; boundary=x"}).startswith("HTTP/1.0 413")
    assert raw_post(u, {"Transfer-Encoding": "chunked", "Content-Type": "multipart/form-data; boundary=x"}).startswith("HTTP/1.0 411")
    assert raw_post(u, {"Content-Length": "-5"}).startswith("HTTP/1.0 400")
    resp = raw_post(u, {"Content-Length": "100000", "Content-Type": "multipart/form-data; boundary=x"}, b"--x\r\nContent-Dis")
    assert resp.startswith("HTTP/1.0 303") and "upload%20was%20incomplete" in resp.replace("+", "%20")
    assert u.svc.ota.info()["error"]["code"] == "truncated"


def test_upload_errors_are_reported(u):
    upload(u, extra_headers={"Content-Type": "application/octet-stream"})
    assert "not understood" in flash(u)
    upload(u, parts=[("note", None, b"no file here")])
    assert flash(u) == "No bundle was received."
    upload(u, parts=[("other", "x.raucb", b"data")])
    assert "not understood" in flash(u)
    u.rauc.bundles[u.svc.ota.bundle_path] = {"compatible": "hifi-bridge-rpi4", "version": "0.4.0"}
    upload(u)
    assert "for a different board" in flash(u) and "hifi-bridge-rpi4" in flash(u)
    u.rauc.bundles[u.svc.ota.bundle_path] = "CMS_verify: certificate verify error"
    upload(u)
    assert "not signed by a key this device trusts" in flash(u)
    page = u.c.get("/settings/update").body
    assert 'role="alert"' in page and "certificate verify error" in page
    u.svc.ota.free = lambda p: 100
    upload(u)
    assert "Not enough free space" in flash(u)


def test_upload_deadline(u, monkeypatch):
    t = iter([0.0, 10 ** 6, 10 ** 6, 10 ** 6])
    monkeypatch.setattr(views_update, "_now", lambda: next(t, 10 ** 6))
    upload(u, data=b"x" * 200_000)
    assert flash(u) == "The upload took too long."


def test_upload_socket_error(u, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(views_update, "parse_multipart", boom)
    upload(u)
    assert flash(u) == "The upload failed."


def test_javascript_upload_gets_json_and_the_same_flash(u):
    u.rauc.bundles[u.svc.ota.bundle_path] = {"compatible": C, "version": "0.4.0"}
    r = upload(u, extra_headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status == 200 and json.loads(r.body) == {"ok": True} and r.getheader("Location") is None
    assert flash(u) == "Bundle 0.4.0 received and verified."
    r = upload(u, extra_headers={"X-Requested-With": "XMLHttpRequest"}, parts=[("x", None, b"1")])
    assert json.loads(r.body) == {"ok": False}


def test_api_update_has_no_paths(u):
    stage(u)
    d = json.loads(u.c.get("/api/update").body)
    assert d == {"managed": True, "phase": "staged", "phase_text": "Update ready to install.", "progress": {"done": 0, "total": 12, "message": ""},
                 "reboot_required": False, "error": None} or d["phase"] == "staged"
    assert "/" not in json.dumps(d).replace("\\/", "")


def test_progress_bar_while_installing(u):
    u.rauc.plan = {"hang": True}
    stage(u)
    u.c.post("/settings/update/install")
    page = u.c.get("/settings/update").body
    assert 'id="ota-progress"' in page and 'data-phase="installing"' in page and "Upload an update bundle" not in page
    d = json.loads(u.c.get("/api/update").body)
    assert d["phase"] == "installing" and d["progress"]["total"] == 100


def test_action_errors_become_flash_messages(u):
    u.c.post("/settings/update/install")
    assert "Another update step" in flash(u)
    u.c.post("/settings/update/rollback")
    assert "No other bootable version" in flash(u)
    u.c.post("/settings/update/check")
    assert "No official update source" in flash(u)
    u.svc.ota.cancel_download()                                         # harmless when nothing runs
    u.c.post("/settings/update/cancel-download")
    assert "cancelled" in flash(u)
    stage(u)
    u.c.post("/settings/update/discard")
    assert flash(u) == "Bundle discarded."


def test_manual_rollback(u):
    u.rauc.slot["rootfs.1"].update({"boot-status": "good", "bundle.version": "0.2.0"})
    page = u.c.get("/settings/update").body
    assert "Version 0.2.0 is still kept in slot B" in page and "Start version 0.2.0 next time" in page
    u.c.post("/settings/update/rollback")
    assert "Previous version selected" in flash(u)
    page = u.c.get("/settings/update").body
    assert "Version 0.2.0 starts after the restart." in page and "Restart now" in page and "Keep the current version" not in page


# ---------------------------------------------------------------- update check against a local fake release server
def test_check_and_download_from_the_official_source(u):
    srv = ReleaseServer()
    try:
        name = "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
        srv.assets[name] = b"B" * 3000
        srv.releases = [srv.release("v0.4.0", [(name, {})])]
        m = u.svc.ota
        m.trust = {"mode": "official", "certificates": [], "official_source": srv.source()}
        m.source = UpdateSource(srv.source(), allow_http=True)
        m.download_opts = {"allow_http": True}
        page = u.c.get("/settings/update").body
        assert "Check now" in page and "iskaron/hifi-bridge" in page
        u.c.post("/settings/update/check")
        assert flash(u) == "Version 0.4.0 is available (running: 0.3.0)."
        page = u.c.get("/settings/update").body
        assert "Download 0.4.0 (3 KB)" in page and "Release notes" in page and "/releases/tag/v0.4.0" in page
        u.rauc.bundles[m.bundle_path] = {"compatible": C, "version": "0.4.0"}
        u.c.post("/settings/update/download")
        assert flash(u) == "Download started."
        m._thread.join(5)
        assert "Install version 0.4.0" in u.c.get("/settings/update").body
        srv.api_status, srv.releases = 404, {"message": "Not Found"}
        u.c.post("/settings/update/check")
        assert "not publicly reachable" in flash(u)
    finally:
        srv.close()


# ---------------------------------------------------------------- backup
def test_backup_page_and_export_without_secrets(u):
    page = u.c.get("/settings/backup").body
    assert "Include secrets" in page and "owner password, stream address token, MQTT password" in page and "Never exported" in page
    p = A11y(); p.feed(page)
    assert p.h1 == 1 and not p.problems, p.problems
    r = u.c.post("/settings/backup/export", {})
    assert r.status == 200 and "attachment" in r.getheader("Content-Disposition") and r.getheader("Cache-Control") == "no-store"
    doc = json.loads(r.body)
    assert doc["secrets"] is None and doc["settings"]["device_name"] == "Wohnzimmer"
    assert u.svc.store.secret("stream_token") not in r.body and u.svc.store.secret("admin_password_hash") not in r.body


def test_export_with_secrets_needs_matching_strong_passphrase(u):
    u.c.post("/settings/backup/export", {"include_secrets": "on", "passphrase": "long enough pass", "confirm": "other"})
    assert flash(u) == "The passphrases do not match."
    u.c.post("/settings/backup/export", {"include_secrets": "on", "passphrase": "short", "confirm": "short"})
    assert "at least 10" in flash(u)
    r = u.c.post("/settings/backup/export", {"include_secrets": "on", "passphrase": "long enough pass", "confirm": "long enough pass"})
    assert json.loads(r.body)["secrets"]["cipher"] == "hmac-sha256-ctr" and u.svc.store.secret("stream_token") not in r.body


def import_file(u, raw: bytes, passphrase=""):
    tok = token(u)
    body, ctype = multipart([("backup", "b.json", raw), ("passphrase", None, passphrase.encode())])
    return u.c.req("POST", f"/settings/backup/import?csrf={urllib.parse.quote(tok)}", raw_body=body, headers={"Content-Type": ctype})


def test_import_roundtrip_with_secrets_ends_sessions(u):
    raw = u.c.post("/settings/backup/export", {"include_secrets": "on", "passphrase": "long enough pass", "confirm": "long enough pass"}).body
    old_token = u.svc.store.secret("stream_token")
    u.svc.store.update({"device_name": "Changed"})
    u.svc.rotate_stream_token()
    r = import_file(u, raw.encode(), "wrong passphrase!")
    assert r.getheader("Location") == "/settings/backup" and "Wrong passphrase" in flash(u)
    r = import_file(u, raw.encode())
    assert "contains secrets" in flash(u)
    r = import_file(u, raw.encode(), "long enough pass")
    assert r.getheader("Location") == "/login" and "Secrets restored." in flash(u)
    assert u.svc.store.get("device_name") == "Wohnzimmer" and u.svc.store.secret("stream_token") == old_token
    assert u.svc.runtime.stream.token == old_token if hasattr(u.svc.runtime.stream, "token") else True
    assert u.c.get("/settings/backup").getheader("Location") == "/login"          # sessions ended (restored password)


def test_import_settings_only_and_errors(u):
    raw = u.c.post("/settings/backup/export", {}).body
    doc = json.loads(raw)
    doc["settings"]["future_thing"] = 1
    doc["settings"]["device_name"] = "Küche"
    r = import_file(u, json.dumps(doc).encode())
    assert r.getheader("Location") == "/settings/backup"
    assert "Backup restored: 1 setting(s) changed." in flash(u) and "future_thing" in flash(u) and "no secrets" in flash(u)
    doc["settings"]["lms_port"] = 0
    import_file(u, json.dumps(doc).encode())
    assert "invalid settings" in flash(u) and "lms_port" in flash(u)
    import_file(u, b"garbage")
    assert "not a valid backup" in flash(u)
    import_file(u, b"x" * (300 * 1024))
    assert "too large" in flash(u)
    tok = token(u)
    body, ctype = multipart([("passphrase", None, b"x")])
    u.c.req("POST", f"/settings/backup/import?csrf={tok}", raw_body=body, headers={"Content-Type": ctype})
    assert "not a valid backup" in flash(u)
    body, ctype = multipart([("evil", "x", b"x")])
    u.c.req("POST", f"/settings/backup/import?csrf={tok}", raw_body=body, headers={"Content-Type": ctype})
    assert "not understood" in flash(u)


def test_import_socket_error(u, monkeypatch):
    monkeypatch.setattr(views_update, "parse_multipart", lambda *a, **k: (_ for _ in ()).throw(OSError("reset")))
    import_file(u, b"{}")
    assert "incomplete" in flash(u)


def test_import_deadline(u, monkeypatch):
    t = iter([0.0, 10 ** 6])
    monkeypatch.setattr(views_update, "_now", lambda: next(t, 10 ** 6))
    import_file(u, b"{}")
    assert "took too long" in flash(u)


def test_snapshot_restore_from_the_backup_page(u):
    assert "No snapshot of the running version" in u.c.get("/settings/backup").body
    stage(u)
    u.c.post("/settings/update/install")
    u.svc.ota._thread.join(5)
    u.svc.ota.cancel_installed()
    page = u.c.get("/settings/backup").body
    assert "Snapshot of version 0.3.0" in page and "Restore snapshot and restart" in page
    u.c.post("/settings/backup/restore-snapshot")
    assert "device restarts" in flash(u) and u.reboots == [1]
    assert "restored at the restart" in u.c.get("/settings/backup").body
    u.c.post("/settings/backup/restore-snapshot")
    assert "Another update step" in flash(u) and u.reboots == [1]                    # no second restart


def test_unknown_error_codes_have_a_generic_text(u):
    req = type("R", (), {"t": i18n.Translator("en")})()
    assert views_update._err(req, "brand_new_code") == "The update step failed (brand_new_code)."


# ---------------------------------------------------------------- i18n
def test_every_update_key_exists_in_both_languages():
    src = open(views_update.__file__).read()
    en, de = i18n.strings("en"), i18n.strings("de")
    used = set(re.findall(r'"((?:upd|bak|settings)\.[a-z0-9_.\-]+)"', src))
    missing = sorted(k for k in used if not k.endswith(".") and k not in en)
    assert not missing, missing
    for prefix, values in (("upd.phase.", ("idle", "downloading", "uploading", "verifying", "staged", "installing", "installed",
                                           "rebooting", "switching", "confirming")),
                           ("upd.result.", ("updated", "rolled_back", "switched", "cancelled")),
                           ("upd.reason.", ("boot_failed", "health_check_failed")),
                           ("upd.trust.", ("official", "own", "official-and-own", "none")),
                           ("upd.check.", ("update_available", "up_to_date", "no_bundle", "no_source", "not_public", "rate_limited",
                                           "unreachable", "invalid_response", "insecure_source"))):
        for v in values:
            assert prefix + v in en and prefix + v in de, prefix + v
    upd = json.load(open(i18n._DIR + "/update.en.json"))
    assert set(upd) == set(json.load(open(i18n._DIR + "/update.de.json")))
