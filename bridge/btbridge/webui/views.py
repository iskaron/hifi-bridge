"""Pages and actions of the owner WebUI.  Every user-visible string goes through ``req.t`` (i18n/en.json, i18n/de.json)."""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse

from .. import __version__
from ..config import SettingError
from .auth import AuthError

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
NAV = (("/", "nav.status"), ("/pairing", "nav.pairing"), ("/settings", "nav.settings"), ("/diagnostics", "nav.diagnostics"),
       ("/system", "nav.system"))
LIVE_KEYS = ("headline.", "state.", "live.")


def _srv():
    from . import server
    return server


def esc(s) -> str:
    return _srv().esc(s)


def public(fn):
    fn.public = True
    return fn


def csrf(req) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(req.session["csrf"])}">' if req.session else ""


def flash_html(req) -> str:
    if not req.flash:
        return ""
    return f'<div class="flash" role="status">{esc(urllib.parse.unquote(req.flash))}</div>'


def live_strings(req) -> str:
    from .i18n import strings
    s = {k: v for k, v in strings(req.t.lang).items() if k.startswith(LIVE_KEYS)}
    return json.dumps(s).replace("</", "<\\/")


def page(ui, req, title: str, body: str, active: str | None = None, live: bool = False, status: int = 200, wizard: bool = False):
    t, svc = req.t, ui.svc
    theme = svc.store.get("theme")
    nav = ""
    if req.session and svc.store.get("setup_complete") and not wizard:
        items = "".join(f'<a href="{href}"{" aria-current=page" if href == active else ""}>{esc(t(key))}</a>' for href, key in NAV)
        nav = (f'<nav class="nav" aria-label="{esc(t("nav.label"))}">{items}</nav>'
               f'<form class="logout" method="post" action="/logout">{csrf(req)}<button class="link">{esc(t("nav.logout"))}</button></form>')
    r = _srv().Response(status, f"""<!doctype html>
<html lang="{req.t.lang}" data-theme="{esc(theme)}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{esc(title)} · {esc(svc.store.get("device_name"))}</title>
<link rel="icon" href="/static/logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script></head>
<body{' data-live="1"' if live else ''}>
<a class="skip" href="#main">{esc(t("a11y.skip"))}</a>
<header class="top"><a class="brand" href="/"><img src="/static/logo.svg" alt="" width="34" height="34">
<span><strong>{esc(svc.store.get("device_name"))}</strong><small>{esc(t("brand.tagline"))}</small></span></a>{nav}</header>
{flash_html(req)}
<main id="main">{body}</main>
<footer class="foot"><span>Bluetooth Audio Bridge {esc(__version__)}</span></footer>
<script type="application/json" id="i18n">{live_strings(req)}</script>
</body></html>""")
    if req.flash:
        r.cookie("bab_flash", "", ui.secure, max_age=0)
    return r


def not_found(ui, req) -> str:
    return f'<!doctype html><meta charset="utf-8"><title>404</title><p>{esc(req.t("error.not_found"))}</p><p><a href="/">→</a></p>'


def static(ui, req):
    name = req.path[len("/static/"):]
    if "/" in name or name.startswith(".") or not name:
        return _srv().Response(404, "not found", "text/plain")
    path = os.path.join(ui.static_dir, name)
    if not os.path.isfile(path):
        return _srv().Response(404, "not found", "text/plain")
    ctype = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}.get(
        os.path.splitext(name)[1], mimetypes.guess_type(name)[0] or "application/octet-stream")
    with open(path, "rb") as fh:
        r = _srv().Response(200, fh.read(), ctype)
    r.headers.append(("Cache-Control", "public, max-age=3600"))
    return r


def err_text(req, e: Exception) -> str:
    if isinstance(e, SettingError):
        return req.t("error.setting", field=req.t("field." + e.key), reason=req.t("error." + e.code))
    if isinstance(e, AuthError):
        if e.code == "rate_limited":
            return req.t("error.rate_limited", seconds=int(e.retry_after) + 1)
        if e.code.startswith("weak_password:"):
            return req.t("error.weak_password", reasons=", ".join(req.t("error.pw." + p) for p in e.code.split(":", 1)[1].split(",")))
        return req.t("error." + e.code)
    return req.t("error.generic")


def headline_card(req, st) -> str:
    t = req.t
    dev = st["bluetooth"]["connected_device"]
    h = st["headline"]
    now = st["now_playing"]
    return f"""<section class="hero state-{esc(h)}" aria-live="polite" data-headline="{esc(h)}">
<div class="ring" aria-hidden="true"><span></span></div>
<div class="hero-text"><p class="eyebrow">{esc(t("dash.now"))}</p>
<h1 id="live-headline">{esc(t("headline." + h))}</h1>
<p id="live-detail" class="detail">{esc(t("headline." + h + ".detail"))}</p>
<dl class="facts">
<div><dt>{esc(t("dash.device"))}</dt><dd id="live-device">{esc(dev["name"]) if dev else esc(t("state.no_device"))}</dd></div>
<div><dt>{esc(t("dash.playing"))}</dt><dd id="live-now">{esc(now) if now else "–"}</dd></div>
<div><dt>{esc(t("dash.target"))}</dt><dd>{esc(target_name(req, st))}</dd></div>
</dl></div></section>"""


def target_name(req, st) -> str:
    tid = st["lyrion"]["target"]
    return (req.ui.svc.store.get("target_label") or tid) if tid else req.t("state.not_configured")


def _with_label(req, changes: dict) -> dict:
    """Store the display name of the chosen player/group next to its id (read-only lookup at LMS)."""
    tid = changes.get("target_player")
    if tid:
        try:
            name = next((x["name"] for x in req.ui.svc.list_targets() if x["id"] == tid), "")
        except Exception:
            name = ""
        changes["target_label"] = name
    return changes


def toggle_form(req, action: str, label: str, value: str, pressed: bool, extra: str = "") -> str:
    return (f'<form method="post" action="{action}">{csrf(req)}<input type="hidden" name="value" value="{value}">'
            f'<button class="toggle" aria-pressed="{"true" if pressed else "false"}">{esc(label)}</button>{extra}</form>')


# ============================================================== setup wizard
def wizard_steps(req, current: int) -> str:
    t = req.t
    names = ["wizard.step.account", "wizard.step.bluetooth", "wizard.step.lyrion", "wizard.step.target", "wizard.step.finish"]
    items = "".join(f'<li class="{"done" if i < current else ("current" if i == current else "")}"'
                    f'{" aria-current=step" if i == current else ""}><span>{i + 1}</span>{esc(t(n))}</li>' for i, n in enumerate(names))
    return f'<ol class="steps" aria-label="{esc(t("wizard.progress"))}">{items}</ol>'


def lang_switch(req) -> str:
    t = req.t
    btns = "".join(f'<button name="lang" value="{code}" class="chip"{" aria-pressed=true" if req.t.lang == code else ""}>{label}</button>'
                   for code, label in (("de", "Deutsch"), ("en", "English")))
    return f'<form class="langs" method="post" action="/lang" aria-label="{esc(t("field.language"))}">{btns}</form>'


@public
def setup_get(req):
    ui, t = req.ui, req.t
    if ui.svc.auth.has_password():
        return _srv().redirect("/setup/bluetooth" if req.session else "/login")
    body = f"""{wizard_steps(req, 0)}<section class="card narrow"><h1>{esc(t("wizard.welcome"))}</h1>
<p>{esc(t("wizard.welcome.text"))}</p>{lang_switch(req)}
<form method="post" action="/setup" class="stack">
<label>{esc(t("field.setup_code"))}<input name="code" autocomplete="one-time-code" required autocapitalize="characters" spellcheck="false"
 aria-describedby="code-help"></label><p id="code-help" class="help">{esc(t("wizard.code.help"))}</p>
<input type="text" name="username" value="owner" autocomplete="username" hidden>
<label>{esc(t("field.new_password"))}<input type="password" name="password" autocomplete="new-password" minlength="10" required></label>
<label>{esc(t("field.confirm_password"))}<input type="password" name="confirm" autocomplete="new-password" minlength="10" required></label>
<button class="primary">{esc(t("wizard.create_account"))}</button></form></section>"""
    return page(ui, req, t("wizard.welcome"), body, wizard=True)


@public
def setup_post(req):
    ui, t = req.ui, req.t
    if ui.svc.auth.has_password():
        return _srv().redirect("/login")
    f = req.form
    if f.get("password", "") != f.get("confirm", ""):
        return _srv().redirect("/setup", t("error.password_mismatch"), ui.secure)
    try:
        sid, _ = ui.svc.auth.complete_setup(req.client, f.get("code", ""), f.get("password", ""))
    except AuthError as e:
        return _srv().redirect("/setup", err_text(req, e), ui.secure)
    if req.cookies.get("bab_lang") in ("de", "en"):
        ui.svc.store.update({"language": req.cookies["bab_lang"]})
    return _srv().redirect("/setup/bluetooth", t("flash.account_created"), ui.secure).cookie(_srv().COOKIE, sid, ui.secure)


@public
def lang_post(req):
    lang = req.form.get("lang")
    to = req.h.headers.get("Referer") or "/"
    path = urllib.parse.urlsplit(to).path or "/"
    r = _srv().redirect(path if path.startswith("/") else "/")
    if lang in ("de", "en"):
        r.cookie(_srv().LANG_COOKIE, lang, req.ui.secure, max_age=365 * 86400)
        if req.session:
            req.ui.svc.store.update({"language": lang})
    return r


def setup_bluetooth_get(req):
    t, svc = req.t, req.ui.svc
    body = f"""{wizard_steps(req, 1)}<section class="card narrow"><h1>{esc(t("wizard.bluetooth.title"))}</h1>
<p>{esc(t("wizard.bluetooth.text"))}</p><form method="post" action="/setup/bluetooth" class="stack">{csrf(req)}
<label>{esc(t("field.device_name"))}<input name="device_name" value="{esc(svc.store.get("device_name"))}" maxlength="32" required></label>
<p class="help">{esc(t("wizard.bluetooth.help"))}</p><button class="primary">{esc(t("wizard.next"))}</button></form></section>"""
    return page(req.ui, req, t("wizard.bluetooth.title"), body, wizard=True)


def setup_bluetooth_post(req):
    try:
        req.ui.svc.store.update({"device_name": req.form.get("device_name", "")})
    except SettingError as e:
        return _srv().redirect("/setup/bluetooth", err_text(req, e), req.ui.secure)
    return _srv().redirect("/setup/lyrion")


def setup_lyrion_get(req):
    t, svc = req.t, req.ui.svc
    found = svc.discover_servers(timeout=1.5)
    opts = "".join(f"""<form method="post" action="/setup/lyrion" class="server">{csrf(req)}
<input type="hidden" name="lms_host" value="{esc(s["host"])}"><input type="hidden" name="lms_port" value="{esc(s["port"])}">
<button class="choice"><strong>{esc(s["name"] or s["host"])}</strong><small>{esc(s["host"])}:{esc(s["port"])} · Lyrion {esc(s["version"])}</small></button>
</form>""" for s in found) or f'<p class="muted">{esc(t("wizard.lyrion.none_found"))}</p>'
    body = f"""{wizard_steps(req, 2)}<section class="card narrow"><h1>{esc(t("wizard.lyrion.title"))}</h1>
<p>{esc(t("wizard.lyrion.text"))}</p><h2>{esc(t("wizard.lyrion.found"))}</h2><div class="choices">{opts}</div>
<h2>{esc(t("wizard.lyrion.manual"))}</h2><form method="post" action="/setup/lyrion" class="stack inline">{csrf(req)}
<label>{esc(t("field.lms_host"))}<input name="lms_host" value="{esc(svc.store.get("lms_host"))}" required spellcheck="false" autocapitalize="off"></label>
<label class="port">{esc(t("field.lms_port"))}<input name="lms_port" value="{esc(svc.store.get("lms_port"))}" inputmode="numeric" required></label>
<button class="primary">{esc(t("wizard.lyrion.connect"))}</button></form></section>"""
    return page(req.ui, req, t("wizard.lyrion.title"), body, wizard=True)


def setup_lyrion_post(req):
    from ..config import validate
    t, svc = req.t, req.ui.svc
    try:
        v = validate({"lms_host": req.form.get("lms_host", ""), "lms_port": req.form.get("lms_port", "9000")})
    except SettingError as e:
        return _srv().redirect("/setup/lyrion", err_text(req, e), req.ui.secure)
    if not v["lms_host"]:
        return _srv().redirect("/setup/lyrion", t("error.setting", field=t("field.lms_host"), reason=t("error.invalid_host")), req.ui.secure)
    chk = svc.check_server(v["lms_host"], v["lms_port"])
    if not chk["reachable"]:
        return _srv().redirect("/setup/lyrion", t("wizard.lyrion.unreachable", server=f'{v["lms_host"]}:{v["lms_port"]}'), req.ui.secure)
    svc.store.update(v)
    return _srv().redirect("/setup/target", t("wizard.lyrion.connected", version=chk["version"]), req.ui.secure)


def target_choices(req, selected: str, action: str) -> str:
    t, svc = req.t, req.ui.svc
    try:
        targets = svc.list_targets()
    except Exception:
        return f'<p class="error">{esc(t("wizard.target.unreachable"))}</p>'
    if not targets:
        return f'<p class="muted">{esc(t("wizard.target.none"))}</p>'
    rows = []
    for x in targets:
        kind = t("target.group") if x["kind"] == "group" else (x.get("model") or t("target.player"))
        state = t("target.online") if x["connected"] else t("target.offline")
        rows.append(f"""<label class="choice radio"><input type="radio" name="target_player" value="{esc(x["id"])}"
{" checked" if x["id"] == selected else ""} required><span><strong>{esc(x["name"])}</strong><small>{esc(kind)} · {esc(state)}</small></span></label>""")
    return f"""<form method="post" action="{action}" class="stack">{csrf(req)}<fieldset><legend>{esc(t("wizard.target.legend"))}</legend>
{"".join(rows)}</fieldset><p class="help">{esc(t("wizard.target.help"))}</p><button{' class="primary"' if action.startswith("/setup") else ""}>{esc(t("wizard.next") if action.startswith("/setup") else t("action.save"))}</button></form>"""


def setup_target_get(req):
    t = req.t
    body = f"""{wizard_steps(req, 3)}<section class="card narrow"><h1>{esc(t("wizard.target.title"))}</h1><p>{esc(t("wizard.target.text"))}</p>
{target_choices(req, req.ui.svc.store.get("target_player"), "/setup/target")}</section>"""
    return page(req.ui, req, t("wizard.target.title"), body, wizard=True)


def setup_target_post(req):
    try:
        req.ui.svc.store.update(_with_label(req, {"target_player": req.form.get("target_player", "")}))
    except SettingError as e:
        return _srv().redirect("/setup/target", err_text(req, e), req.ui.secure)
    return _srv().redirect("/setup/finish")


def setup_finish_get(req):
    t, svc = req.t, req.ui.svc
    st = svc.status()
    body = f"""{wizard_steps(req, 4)}<section class="card narrow"><h1>{esc(t("wizard.finish.title"))}</h1><p>{esc(t("wizard.finish.text"))}</p>
<dl class="facts"><div><dt>{esc(t("field.device_name"))}</dt><dd>{esc(svc.store.get("device_name"))}</dd></div>
<div><dt>{esc(t("dash.server"))}</dt><dd>{esc(st["lyrion"]["server"] or "–")}</dd></div>
<div><dt>{esc(t("dash.target"))}</dt><dd>{esc(target_name(req, st))}</dd></div></dl>
<form method="post" action="/diagnostics/test" class="stack">{csrf(req)}<input type="hidden" name="back" value="/setup/finish">
<button>{esc(t("action.test_sound"))}</button><p class="help">{esc(t("diag.test.help"))}</p></form>
<form method="post" action="/setup/finish">{csrf(req)}<button class="primary">{esc(t("wizard.finish.done"))}</button></form></section>"""
    return page(req.ui, req, t("wizard.finish.title"), body, wizard=True)


def setup_finish_post(req):
    req.ui.svc.store.update({"setup_complete": True})
    return _srv().redirect("/", req.t("flash.setup_done"), req.ui.secure)


# ============================================================== login / logout
@public
def login_get(req):
    t, ui = req.t, req.ui
    if not ui.svc.auth.has_password():
        return _srv().redirect("/setup")
    if req.session:
        return _srv().redirect("/")
    body = f"""<section class="card narrow"><h1>{esc(t("login.title"))}</h1>{lang_switch(req)}
<form method="post" action="/login" class="stack"><input type="text" name="username" value="owner" autocomplete="username" hidden>
<label>{esc(t("field.password"))}<input type="password" name="password" autocomplete="current-password" required autofocus></label>
<button class="primary">{esc(t("login.submit"))}</button></form><p class="help">{esc(t("login.help"))}</p></section>"""
    return page(ui, req, t("login.title"), body)


@public
def login_post(req):
    ui = req.ui
    try:
        sid, _ = ui.svc.auth.login(req.client, req.form.get("password", ""))
    except AuthError as e:
        return _srv().redirect("/login", err_text(req, e), ui.secure)
    return _srv().redirect("/").cookie(_srv().COOKIE, sid, ui.secure)


def logout_post(req):
    req.ui.svc.auth.sessions.destroy(req.sid)
    return _srv().redirect("/login", req.t("flash.logged_out"), req.ui.secure).cookie(_srv().COOKIE, "", req.ui.secure, max_age=0)


# ============================================================== dashboard
def dashboard(req):
    t, svc = req.t, req.ui.svc
    st = svc.status()
    p = st["bluetooth"]
    allowed = st["bridge"]["audio_permission"] == "allowed"
    pairing = (toggle_form(req, "/pairing/close", t("pairing.close"), "off", True,
                           f'<p class="countdown" data-remaining="{p["pairing_remaining_s"]}" id="live-pairing">'
                           f'{esc(t("pairing.open_for", name=p["name"], seconds=p["pairing_remaining_s"]))}</p>')
               if p["pairing_open"] else toggle_form(req, "/pairing/open", t("pairing.open"), "on", False,
                                                     f'<p class="help">{esc(t("pairing.help", name=p["name"]))}</p>'))
    degraded = "".join(f'<li>{esc(t("degraded." + d))}</li>' for d in st["degraded"])
    sysd = st["system"] or {}
    body = f"""{headline_card(req, st)}
{f'<section class="card warn" role="alert"><h2>{esc(t("dash.attention"))}</h2><ul>{degraded}</ul></section>' if degraded else ''}
<div class="grid">
<section class="card"><h2>{esc(t("dash.pairing"))}</h2>{pairing}</section>
<section class="card"><h2>{esc(t("dash.audio"))}</h2>{toggle_form(req, "/audio", t("audio.allowed") if allowed else t("audio.blocked"),
        "off" if allowed else "on", allowed, f'<p class="help">{esc(t("audio.help"))}</p>')}</section>
<section class="card"><h2>{esc(t("dash.test"))}</h2><form method="post" action="/diagnostics/test">{csrf(req)}<input type="hidden" name="back" value="/">
<button>{esc(t("action.test_sound"))}</button></form><p class="help">{esc(t("diag.test.help"))}</p></section>
<section class="card small"><h2>{esc(t("dash.system"))}</h2><dl class="metrics">
<div><dt>{esc(t("sys.cpu"))}</dt><dd>{esc(fmt(sysd.get("cpu_percent"), "%"))}</dd></div>
<div><dt>{esc(t("sys.temp"))}</dt><dd>{esc(fmt(sysd.get("temperature_c"), " °C"))}</dd></div>
<div><dt>{esc(t("sys.memory"))}</dt><dd>{esc(fmt((sysd.get("memory") or {}).get("used_percent"), "%"))}</dd></div></dl>
<a href="/system">{esc(t("dash.more"))}</a></section></div>"""
    return page(req.ui, req, t("nav.status"), body, active="/", live=True)


def fmt(v, unit="") -> str:
    return "–" if v is None else f"{v}{unit}"


def pairing_open(req):
    req.ui.svc.open_pairing()
    return _srv().redirect(safe_back(req, "/"), req.t("flash.pairing_opened"), req.ui.secure)


def pairing_close(req):
    req.ui.svc.close_pairing()
    return _srv().redirect(safe_back(req, "/"), req.t("flash.pairing_closed"), req.ui.secure)


def audio_post(req):
    on = req.form.get("value") == "on"
    req.ui.svc.set_audio_allowed(on)
    return _srv().redirect(safe_back(req, "/"), req.t("flash.audio_on" if on else "flash.audio_off"), req.ui.secure)


def safe_back(req, default: str) -> str:
    b = req.form.get("back", "")
    return b if b.startswith("/") and not b.startswith("//") else default


# ============================================================== pairing page
def pairing_page(req):
    t, svc = req.t, req.ui.svc
    st = svc.status()["bluetooth"]
    devs = svc.paired_devices()
    rows = "".join(f"""<li class="device"><div><strong>{esc(d.get("label") or d["name"] or d["address"])}</strong>
<small>{esc(d["address"])} · {esc(t("state.connected") if d["connected"] else t("state.not_connected"))}</small></div>
<form method="post" action="/devices/label" class="inline">{csrf(req)}<input type="hidden" name="address" value="{esc(d["address"])}">
<label class="sr">{esc(t("pairing.label"))}<input name="label" value="{esc(d.get("label", ""))}" maxlength="40" placeholder="{esc(t("pairing.label"))}"></label>
<button class="small">{esc(t("action.save"))}</button></form>
<form method="post" action="/devices/remove" data-confirm="{esc(t("pairing.remove.confirm"))}">{csrf(req)}
<input type="hidden" name="address" value="{esc(d["address"])}"><button class="danger small">{esc(t("pairing.remove"))}</button></form></li>""" for d in devs)
    win = (toggle_form(req, "/pairing/close", t("pairing.close"), "off", True,
                       f'<input type="hidden" name="back" value="/pairing"><p class="countdown" data-remaining="{st["pairing_remaining_s"]}" '
                       f'id="live-pairing">{esc(t("pairing.open_for", name=st["name"], seconds=st["pairing_remaining_s"]))}</p>')
           if st["pairing_open"] else toggle_form(req, "/pairing/open", t("pairing.open"), "on", False,
                                                  f'<input type="hidden" name="back" value="/pairing"><p class="help">{esc(t("pairing.help", name=st["name"]))}</p>'))
    body = f"""<h1>{esc(t("nav.pairing"))}</h1><div class="grid">
<section class="card"><h2>{esc(t("pairing.window"))}</h2>{win}<p class="help">{esc(t("pairing.guest_hint", name=st["name"]))}</p></section>
<section class="card wide"><h2>{esc(t("pairing.devices"))}</h2>{f'<ul class="devices">{rows}</ul>' if rows else f'<p class="muted">{esc(t("pairing.no_devices"))}</p>'}
<p class="help">{esc(t("pairing.one_source"))}</p></section></div>"""
    return page(req.ui, req, t("nav.pairing"), body, active="/pairing", live=True)


def device_remove(req):
    ok = req.ui.svc.remove_device(req.form.get("address", ""))
    return _srv().redirect("/pairing", req.t("flash.device_removed" if ok else "flash.device_not_found"), req.ui.secure)


def device_label(req):
    try:
        req.ui.svc.set_device_label(req.form.get("address", ""), req.form.get("label", ""))
    except SettingError as e:
        return _srv().redirect("/pairing", err_text(req, e), req.ui.secure)
    return _srv().redirect("/pairing", req.t("flash.saved"), req.ui.secure)


# ============================================================== settings
def field(req, key, value, kind="text", **attrs) -> str:
    t = req.t
    extra = " ".join(f'{k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items())
    if kind == "checkbox":
        return (f'<label class="check"><input type="checkbox" name="{key}" value="on"{" checked" if value else ""} {extra}>'
                f'<span>{esc(t("field." + key))}</span></label>')
    return f'<label>{esc(t("field." + key))}<input type="{kind}" name="{key}" value="{esc(value)}" {extra}></label>'


def select(req, key, value, choices) -> str:
    t = req.t
    opts = "".join(f'<option value="{c}"{" selected" if c == value else ""}>{esc(t("choice." + key + "." + c))}</option>' for c in choices)
    return f'<label>{esc(t("field." + key))}<select name="{key}">{opts}</select></label>'


def settings_page(req):
    t, s = req.t, req.ui.svc.store
    c = s.all()
    group = lambda name, action, inner, note="": (f'<section class="card"><h2>{esc(t("settings." + name))}</h2>'
                                                  f'<form method="post" action="{action}" class="stack">{csrf(req)}{inner}'
                                                  f'{f"<p class=help>{esc(note)}</p>" if note else ""}<button>{esc(t("action.save"))}</button></form></section>')
    body = f"""<h1>{esc(t("nav.settings"))}</h1><div class="grid">
{group("lyrion", "/settings/lyrion", field(req, "lms_host", c["lms_host"], spellcheck="false", autocapitalize="off") +
       field(req, "lms_port", c["lms_port"], inputmode="numeric"), t("settings.lyrion.help"))}
<section class="card"><h2>{esc(t("settings.target"))}</h2>{target_choices(req, c["target_player"], "/settings/target")}</section>
{group("playback", "/settings/playback", field(req, "override_lms", c["override_lms"], "checkbox") +
       select(req, "pause_strategy", c["pause_strategy"], ("drop_restart", "keepalive_silence")) +
       select(req, "stream_format", c["stream_format"], ("wav", "flac")) + field(req, "icy_metadata", c["icy_metadata"], "checkbox"),
       t("settings.playback.help"))}
{group("bluetooth", "/settings/bluetooth", field(req, "device_name", c["device_name"], maxlength="32") +
       field(req, "pairing_seconds", c["pairing_seconds"], "number", min="60", max="1800", step="30"))}
{group("mqtt", "/settings/mqtt", field(req, "mqtt_enabled", c["mqtt_enabled"], "checkbox") + field(req, "mqtt_host", c["mqtt_host"], spellcheck="false") +
       field(req, "mqtt_port", c["mqtt_port"], inputmode="numeric") + field(req, "mqtt_username", c["mqtt_username"], autocomplete="off") +
       f'<label>{esc(t("field.mqtt_password"))}<input type="password" name="mqtt_password" autocomplete="new-password" placeholder="{esc(t("settings.mqtt.password_keep") if s.secret("mqtt_password") else "")}"></label>' +
       field(req, "mqtt_tls", c["mqtt_tls"], "checkbox") + field(req, "mqtt_base_topic", c["mqtt_base_topic"]) +
       field(req, "mqtt_discovery_prefix", c["mqtt_discovery_prefix"]), t("settings.mqtt.help"))}
{group("appearance", "/settings/appearance", select(req, "language", c["language"], ("auto", "de", "en")) +
       select(req, "theme", c["theme"], ("auto", "light", "dark")))}
<section class="card"><h2>{esc(t("settings.security"))}</h2><form method="post" action="/settings/password" class="stack">{csrf(req)}
<input type="text" name="username" value="owner" autocomplete="username" hidden>
<label>{esc(t("field.password"))}<input type="password" name="old" autocomplete="current-password" required></label>
<label>{esc(t("field.new_password"))}<input type="password" name="new" autocomplete="new-password" minlength="10" required></label>
<label>{esc(t("field.confirm_password"))}<input type="password" name="confirm" autocomplete="new-password" minlength="10" required></label>
<button>{esc(t("settings.change_password"))}</button></form>
<h3>{esc(t("settings.token.title"))}</h3><form method="post" action="/settings/token" class="stack" data-confirm="{esc(t("settings.token.confirm"))}">{csrf(req)}
<p class="help">{esc(t("settings.token.help"))}</p><button class="danger">{esc(t("settings.token.rotate"))}</button></form></section>
</div>"""
    return page(req.ui, req, t("nav.settings"), body, active="/settings")


def _save(req, keys: tuple[str, ...], checkboxes: tuple[str, ...] = ()):
    changes = {k: req.form.get(k, "") for k in keys if k in req.form}
    for k in checkboxes:
        changes[k] = req.form.get(k) == "on"
    try:
        req.ui.svc.store.update(changes)
    except SettingError as e:
        return _srv().redirect("/settings", err_text(req, e), req.ui.secure)
    return _srv().redirect("/settings", req.t("flash.saved"), req.ui.secure)


def settings_lyrion(req):
    return _save(req, ("lms_host", "lms_port"))


def settings_target(req):
    try:
        req.ui.svc.store.update(_with_label(req, {"target_player": req.form.get("target_player", "")}))
    except SettingError as e:
        return _srv().redirect("/settings", err_text(req, e), req.ui.secure)
    return _srv().redirect("/settings", req.t("flash.saved"), req.ui.secure)


def settings_playback(req):
    return _save(req, ("pause_strategy", "stream_format"), ("override_lms", "icy_metadata"))


def settings_bluetooth(req):
    return _save(req, ("device_name", "pairing_seconds"))


def settings_mqtt(req):
    pw = req.form.get("mqtt_password", "")
    if pw:
        req.ui.svc.store.set_secret("mqtt_password", pw)
    return _save(req, ("mqtt_host", "mqtt_port", "mqtt_username", "mqtt_base_topic", "mqtt_discovery_prefix"), ("mqtt_enabled", "mqtt_tls"))


def settings_appearance(req):
    r = _save(req, ("language", "theme"))
    lang = req.form.get("language")
    if lang in ("de", "en"):
        r.cookie(_srv().LANG_COOKIE, lang, req.ui.secure, max_age=365 * 86400)
    return r


def settings_password(req):
    f = req.form
    if f.get("new") != f.get("confirm"):
        return _srv().redirect("/settings", req.t("error.password_mismatch"), req.ui.secure)
    try:
        req.ui.svc.auth.change_password(f.get("old", ""), f.get("new", ""))
    except AuthError as e:
        return _srv().redirect("/settings", err_text(req, e), req.ui.secure)
    sid, _ = req.ui.svc.auth.sessions.create()                 # all old sessions are gone; keep this browser logged in
    return _srv().redirect("/settings", req.t("flash.password_changed"), req.ui.secure).cookie(_srv().COOKIE, sid, req.ui.secure)


def settings_token(req):
    req.ui.svc.rotate_stream_token()
    return _srv().redirect("/settings", req.t("flash.token_rotated"), req.ui.secure)


# ============================================================== diagnostics + system
def diagnostics_page(req):
    t, svc = req.t, req.ui.svc
    st = svc.status()
    logs = "\n".join(f"{time_str(ts)}  {m}" for ts, m in svc.logs.recent(80))
    faults = "".join(f'<li>{esc(time_str(f["t"]))} · {esc(f["kind"])}: {esc(f["detail"])}</li>' for f in svc.logs.faults()[-20:])
    states = "".join(f'<div><dt>{esc(t("dim." + k))}</dt><dd>{esc(t("state." + str(v)) if v else "–")}</dd></div>'
                     for k, v in st["bridge"].items())
    body = f"""<h1>{esc(t("nav.diagnostics"))}</h1><div class="grid">
<section class="card"><h2>{esc(t("diag.test"))}</h2><form method="post" action="/diagnostics/test">{csrf(req)}<input type="hidden" name="back" value="/diagnostics">
<button class="primary">{esc(t("action.test_sound"))}</button></form><p class="help">{esc(t("diag.test.help"))}</p></section>
<section class="card"><h2>{esc(t("diag.lyrion"))}</h2><form method="post" action="/diagnostics/check">{csrf(req)}<button>{esc(t("diag.check"))}</button></form>
<p class="help">{esc(t("diag.lyrion.help"))}</p></section>
<section class="card"><h2>{esc(t("diag.states"))}</h2><dl class="facts">{states}</dl></section>
<section class="card"><h2>{esc(t("diag.bundle"))}</h2><p class="help">{esc(t("diag.bundle.help"))}</p>
<a class="button" href="/api/diagnostics" download>{esc(t("diag.download"))}</a></section>
<section class="card wide"><h2>{esc(t("diag.faults"))}</h2>{f"<ul class=faults>{faults}</ul>" if faults else f'<p class="muted">{esc(t("diag.no_faults"))}</p>'}</section>
<section class="card wide"><h2>{esc(t("diag.logs"))}</h2><pre class="log" tabindex="0">{esc(logs)}</pre></section></div>"""
    return page(req.ui, req, t("nav.diagnostics"), body, active="/diagnostics")


def time_str(ts: float) -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def diagnostics_test(req):
    r = req.ui.svc.diagnostic_test()
    msg = req.t("diag.test.started") if r == "started" else req.t("diag.test.refused", reason=req.t("refusal." + r.split(":", 1)[-1]))
    return _srv().redirect(safe_back(req, "/diagnostics"), msg, req.ui.secure)


def diagnostics_check(req):
    svc = req.ui.svc
    host, port = svc.store.get("lms_host"), svc.store.get("lms_port")
    if not host:
        return _srv().redirect("/diagnostics", req.t("state.not_configured"), req.ui.secure)
    r = svc.check_server(host, port)
    msg = req.t("diag.check.ok", version=r["version"]) if r["reachable"] else req.t("diag.check.fail", server=f"{host}:{port}")
    return _srv().redirect("/diagnostics", msg, req.ui.secure)


def system_page(req):
    t, svc = req.t, req.ui.svc
    s = svc.status()["system"] or {}
    mem, sto = s.get("memory") or {}, s.get("storage") or {}
    net = "".join(f'<div><dt>{esc(name)}</dt><dd>{esc(t("sys.net_value", rx=round(v.get("rx_bytes", 0) / 2**20, 1), tx=round(v.get("tx_bytes", 0) / 2**20, 1)))}'
                  f'{esc(" · " + t("sys.wifi", dbm=v["wifi_signal_dbm"])) if "wifi_signal_dbm" in v else ""}</dd></div>'
                  for name, v in (s.get("network") or {}).items())
    body = f"""<h1>{esc(t("nav.system"))}</h1><div class="grid">
<section class="card"><h2>{esc(t("sys.health"))}</h2><dl class="metrics big">
<div><dt>{esc(t("sys.cpu"))}</dt><dd>{esc(fmt(s.get("cpu_percent"), "%"))}</dd></div>
<div><dt>{esc(t("sys.temp"))}</dt><dd>{esc(fmt(s.get("temperature_c"), " °C"))}<small>{esc(t("thermal." + str(s.get("thermal_state", "unknown"))))}</small></dd></div>
<div><dt>{esc(t("sys.load"))}</dt><dd>{esc(" / ".join(str(x) for x in s["load"]) if s.get("load") else "–")}</dd></div>
<div><dt>{esc(t("sys.memory"))}</dt><dd>{esc(fmt(mem.get("used_percent"), "%"))}<small>{esc(fmt(mem.get("available_mb"), " MB"))} {esc(t("sys.free"))}</small></dd></div>
<div><dt>{esc(t("sys.storage"))}</dt><dd>{esc(fmt(sto.get("used_percent"), "%"))}<small>{esc(fmt(sto.get("free_mb"), " MB"))} {esc(t("sys.free"))}</small></dd></div>
<div><dt>{esc(t("sys.uptime"))}</dt><dd>{esc(duration(s.get("uptime_s")))}</dd></div></dl></section>
<section class="card"><h2>{esc(t("sys.network"))}</h2><dl class="facts">{net or "<p class=muted>–</p>"}</dl></section>
<section class="card"><h2>{esc(t("sys.mqtt"))}</h2><p>{esc(mqtt_text(req))}</p></section></div>"""
    return page(req.ui, req, t("nav.system"), body, active="/system")


def mqtt_text(req) -> str:
    m = req.ui.svc.status()["mqtt"]
    if not m.get("enabled"):
        return req.t("mqtt.disabled")
    return req.t("mqtt.connected", broker=m.get("broker", "")) if m.get("connected") else req.t("mqtt.disconnected", error=m.get("last_error") or "–")


def duration(s) -> str:
    if s is None:
        return "–"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h {rem // 60}m" if d else f"{h}h {rem // 60}m"


# ============================================================== JSON API (used by app.js; same auth + CSRF rules)
def api_status(req):
    return _srv().json_response(req.ui.svc.status())


def api_diagnostics(req):
    r = _srv().json_response(req.ui.svc.diagnostics_bundle())
    r.headers.append(("Content-Disposition", 'attachment; filename="bridge-diagnostics.json"'))
    return r


def routes(ui):
    return {
        ("GET", "/setup"): setup_get, ("POST", "/setup"): setup_post, ("POST", "/lang"): lang_post,
        ("GET", "/setup/bluetooth"): setup_bluetooth_get, ("POST", "/setup/bluetooth"): setup_bluetooth_post,
        ("GET", "/setup/lyrion"): setup_lyrion_get, ("POST", "/setup/lyrion"): setup_lyrion_post,
        ("GET", "/setup/target"): setup_target_get, ("POST", "/setup/target"): setup_target_post,
        ("GET", "/setup/finish"): setup_finish_get, ("POST", "/setup/finish"): setup_finish_post,
        ("GET", "/login"): login_get, ("POST", "/login"): login_post, ("POST", "/logout"): logout_post,
        ("GET", "/"): dashboard, ("POST", "/pairing/open"): pairing_open, ("POST", "/pairing/close"): pairing_close,
        ("POST", "/audio"): audio_post, ("GET", "/pairing"): pairing_page, ("POST", "/devices/remove"): device_remove,
        ("POST", "/devices/label"): device_label, ("GET", "/settings"): settings_page, ("POST", "/settings/lyrion"): settings_lyrion,
        ("POST", "/settings/target"): settings_target, ("POST", "/settings/playback"): settings_playback,
        ("POST", "/settings/bluetooth"): settings_bluetooth, ("POST", "/settings/mqtt"): settings_mqtt,
        ("POST", "/settings/appearance"): settings_appearance, ("POST", "/settings/password"): settings_password,
        ("POST", "/settings/token"): settings_token, ("GET", "/diagnostics"): diagnostics_page,
        ("POST", "/diagnostics/test"): diagnostics_test, ("POST", "/diagnostics/check"): diagnostics_check,
        ("GET", "/system"): system_page, ("GET", "/api/status"): api_status, ("GET", "/api/diagnostics"): api_diagnostics,
    }
