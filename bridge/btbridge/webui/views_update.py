"""Software update and backup pages of the owner WebUI (docs/OTA.md). Strings: i18n/update.<lang>.json.

* ``/settings/update``: running version and slots, the image's update trust (update-trust.json), manual update check, download, upload,
  installation with RAUC's progress, the owner-confirmed restart, declining it, and selecting the previous slot (manual rollback).
* ``/settings/backup``: export (secrets excluded by default, or encrypted with a passphrase), import, the pre-update settings snapshot.

Every action is a plain form (works without JavaScript); static/update.js adds live progress, upload progress and the wait for the
restart. The two uploads are ``streamed`` handlers: the server checks session + CSRF (query parameter) + Content-Length BEFORE reading the
body (server._route_stream); the body is then parsed as streamed multipart straight to the data partition, with an overall deadline.
"""
from __future__ import annotations

import io
import time
import urllib.parse

from ..ota import backup as B
from ..ota.manager import MAX_BUNDLE, UpdateError
from ..ota.transfer import TransferError, boundary_of, parse_multipart

BASE, BACKUP = "/settings/update", "/settings/backup"
UPLOAD_OVERHEAD = 64 * 1024                 # multipart headers and the closing delimiter around the bundle
MIN_RATE = 64 * 1024                        # bytes/s an upload must sustain on average (plus a fixed allowance), else it is cut off
ACTIVE = ("downloading", "uploading", "verifying", "installing", "rebooting", "confirming")
_now = time.monotonic                       # upload deadlines (patched by tests)


def _v():
    from . import views
    return views


def _srv():
    from . import server
    return server


def esc(s) -> str:
    return _v().esc(s)


def streamed(limit: int):
    def mark(fn):
        fn.stream_body = limit
        return fn
    return mark


def _ota(req):
    return getattr(req.ui.svc, "ota", None)


def _size(n) -> str:
    n = int(n or 0)
    return f"{n / 2 ** 20:.1f} MB" if n >= 2 ** 20 else f"{max(1, round(n / 1024))} KB"


def _when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "–"


def _done(req, back: str, msg: str, error: bool = False):
    """PRG redirect with a flash; for the JavaScript upload (XMLHttpRequest) the same flash cookie with a JSON body instead."""
    r = _srv().redirect(back, msg, req.secure, error=error)
    if req.h.headers.get("X-Requested-With") == "XMLHttpRequest":
        r.status, r.ctype = 200, "application/json"
        r.body = (b'{"ok": false}' if error else b'{"ok": true}')
        r.headers = [h for h in r.headers if h[0] != "Location"]
    return r


def _err(req, code: str, detail: str = "", prefix: str = "upd.error.") -> str:
    t = req.t
    key = prefix + code
    text = t(key) if key in _strings(req) else t(prefix + "generic", code=code)
    return f"{text} ({detail})" if detail else text


def _strings(req):
    from .i18n import strings
    return strings(req.t.lang)


def _form(req, action, button_key, inner="", cls="inline", primary=False, danger=False, confirm_key=None, **kw) -> str:
    t = req.t
    conf = f' data-confirm="{esc(t(confirm_key, **kw))}"' if confirm_key else ""
    klass = " class=primary" if primary else (" class=danger" if danger else "")
    return (f'<form method="post" action="{action}" class="{cls}"{conf}>{_v().csrf(req)}{inner}'
            f'<button{klass}>{esc(t(button_key, **kw))}</button></form>')


def _page(req, title_key, inner):
    t = req.t
    body = (f'<link rel="stylesheet" href="/static/update.css"><p class="back"><a href="/settings">‹ {esc(t("nav.settings"))}</a></p>'
            f'<h1>{esc(t(title_key))}</h1><div class="stack-cards">{inner}</div><script src="/static/update.js" defer></script>')
    return _v().page(req.ui, req, t(title_key), body, active="/settings")


def summary(req, name: str) -> str:
    """One line for the settings overview."""
    t, m = req.t, _ota(req)
    from .. import __version__
    if name == "backup":
        return t("bak.summary")
    if m is None:
        return t("upd.summary.unmanaged", version=__version__)
    try:
        i = m.info()
    except Exception:
        return t("upd.summary.unmanaged", version=__version__)
    if i["reboot_required"]:
        return t("upd.summary.reboot", version=i["version"])
    return t("upd.summary", version=i["version"], phase=t("upd.phase." + i["phase"]))


# ====================================================================== update page
def software_card(req, i) -> str:
    t = req.t
    rv = i["rauc"]
    rows = [("upd.version", esc(i["version"]))]
    if rv.get("available"):
        for s in rv["slots"]:
            state = t("upd.slot." + (s["boot_status"] if s["boot_status"] in ("good", "bad") else "unknown"))
            tags = [t("upd.slot.running")] if s["booted"] else []
            if s["primary"]:
                tags.append(t("upd.slot.next"))
            label = t("upd.slot.name", slot=s["bootname"])
            rows.append((label, esc(f'{s["version"] or t("upd.slot.empty")} · {state}' + (f' · {" · ".join(tags)}' if tags else ""))))
    else:
        rows.append(("upd.slots", esc(t("upd.rauc_unavailable"))))
    tr = i["trust"]
    rows.append(("upd.trust", esc(t("upd.trust." + (tr.get("mode") or "none")))))
    src = tr.get("official_source")
    rows.append(("upd.source", esc(src["repository"]) if src else esc(t("upd.source.none"))))
    facts = "".join(f"<div><dt>{esc(t(k) if k.startswith('upd.') else k)}</dt><dd>{v}</dd></div>" for k, v in rows)
    certs = "".join(f'<li>{esc(t("upd.cert." + c.get("role", "own")))}: {esc(c.get("subject", ""))}<br><code class="fp">{esc(c.get("sha256", ""))}'
                    f'</code></li>' for c in tr.get("certificates") or [])
    certs = f'<details><summary>{esc(t("upd.certs"))}</summary><ul class="ota-certs">{certs}</ul></details>' if certs else ""
    note = f'<p class="help">{esc(t("upd.trust.note"))}</p>' if tr.get("mode") in ("own", "official-and-own") else ""
    return f'<section class="card"><h2>{esc(t("upd.software"))}</h2><dl class="facts stacked">{facts}</dl>{certs}{note}</section>'


def status_card(req, i) -> str:
    t, ph = req.t, i["phase"]
    parts = []
    r = i.get("result")
    if r:
        reason = f' {t("upd.reason." + r["reason"])}' if r.get("reason") else ""
        parts.append(f'<p class="ota-result ota-{esc(r["kind"])}">{esc(t("upd.result." + r["kind"], **{"from": r.get("from_version") or "–", "to": r.get("to_version") or "–"}) + reason)}</p>')
    e = i.get("error")
    if e:
        parts.append(f'<p class="error" role="alert">{esc(_err(req, e["code"], e.get("detail") or ""))}</p>')
    if r or e:
        parts.append(_form(req, BASE + "/dismiss", "upd.dismiss"))
    if i.get("restored"):
        parts.append(f'<p class="ota-result">{esc(t("upd.restored", version=i["restored"]["version"]))}</p>')
    pr = i.get("progress") or {}
    prog = ""
    if ph in ("downloading", "uploading", "installing") and pr.get("total"):
        pct = int(100 * pr["done"] / pr["total"]) if ph != "installing" else int(pr["done"])
        msg = pr.get("message") or ""
        prog = (f'<progress id="ota-progress" max="100" value="{pct}" aria-label="{esc(t("upd.progress"))}">{pct} %</progress>'
                f'<p class="help" id="ota-progress-text">{pct} %{(" · " + esc(msg)) if msg else ""}</p>')
    st = i.get("staged")
    actions = ""
    if ph == "staged" and st:
        parts.append(f'<p>{esc(t("upd.staged", version=st["version"], name=st.get("name") or "–", size=_size(st.get("size"))))}</p>'
                     f'<p class="help">{esc(t("upd.staged.verified"))}</p>')
        actions = (_form(req, BASE + "/install", "upd.install", primary=True, confirm_key="upd.install.confirm", version=st["version"]) +
                   _form(req, BASE + "/discard", "upd.discard"))
    elif ph in ("installed", "switching"):
        inst = i.get("install") or {}
        parts.append(f'<p><strong>{esc(t("upd.reboot_needed." + ph, version=inst.get("to_version") or "–"))}</strong></p>')
        actions = _form(req, BASE + "/reboot", "upd.reboot", primary=True, confirm_key="upd.reboot.confirm")
        if ph == "installed":
            actions += _form(req, BASE + "/cancel", "upd.cancel")
    elif ph == "downloading":
        actions = _form(req, BASE + "/cancel-download", "upd.cancel_download")
    text = t("upd.phase." + ph)
    refresh = f'<noscript><p><a href="{BASE}">{esc(t("upd.refresh"))}</a></p></noscript>' if ph in ACTIVE else ""
    live = (f' data-restarting="{esc(t("upd.live.restarting"))}" data-back="{esc(t("upd.live.back"))}"'
            f' data-uploading="{esc(t("upd.live.uploading", pct="{pct}"))}"')
    return (f'<section class="card ota-status" id="ota-status" data-phase="{esc(ph)}"{live} aria-live="polite"><h2>{esc(t("upd.status"))}</h2>'
            f'<p class="ota-phase" id="ota-phase">{esc(text)}</p>{prog}{"".join(parts)}<div class="actions">{actions}</div>{refresh}</section>')


def check_card(req, i) -> str:
    t = req.t
    if not i["check_available"]:
        return (f'<section class="card"><h2>{esc(t("upd.check"))}</h2><p class="help">'
                f'{esc(t("upd.check.own_only" if i["trust"].get("mode") == "own" else "upd.check.unavailable"))}</p></section>')
    c = i.get("check") or {}
    res = ""
    if c:
        lt = c.get("latest") or {}
        res = f'<p id="ota-check">{esc(t("upd.check." + c["status"], version=lt.get("version", "–"), current=i["version"]))}</p>'
        res += f'<p class="help">{esc(t("upd.check.at", time=_when(c.get("checked_at"))))}</p>'
        if c["status"] == "update_available" and i["phase"] == "idle":
            a = lt["asset"]
            key = "upd.resume" if i.get("download_paused") else "upd.download"
            res += _form(req, BASE + "/download", key, version=lt["version"], size=_size(a["size"]))
            res += f'<p><a href="{esc(lt["page"])}" rel="noopener noreferrer">{esc(t("upd.release_notes"))}</a></p>'
    busy = i["phase"] in ACTIVE
    btn = "" if busy else _form(req, BASE + "/check", "upd.check.now")
    return (f'<section class="card"><h2>{esc(t("upd.check"))}</h2><p class="help">{esc(t("upd.check.help"))}</p>{res}'
            f'<div class="actions">{btn}</div></section>')


def upload_card(req, i) -> str:
    t = req.t
    if i["phase"] not in ("idle", "staged"):
        return ""
    token = urllib.parse.quote(req.session["csrf"]) if req.session else ""
    return (f'<section class="card"><h2>{esc(t("upd.upload"))}</h2>'
            f'<form method="post" action="{BASE}/upload?csrf={token}" enctype="multipart/form-data" class="stack" id="ota-upload">'
            f'<label>{esc(t("upd.upload.file"))}<input type="file" name="bundle" accept=".raucb" required aria-describedby="ota-upload-help"></label>'
            f'<p class="help" id="ota-upload-help">{esc(t("upd.upload.help", size=_size(MAX_BUNDLE)))}</p>'
            f'<progress id="ota-upload-progress" max="100" value="0" hidden aria-label="{esc(t("upd.progress"))}"></progress>'
            f'<button>{esc(t("upd.upload.button"))}</button></form></section>')


def rollback_card(req, i) -> str:
    t, o = req.t, i.get("other_slot")
    if i["phase"] not in ("idle", "staged") or o is None:
        return ""
    if i["can_rollback"]:
        inner = (f'<p>{esc(t("upd.rollback.text", version=o["version"], slot=o["bootname"]))}</p>' +
                 _form(req, BASE + "/rollback", "upd.rollback.button", danger=True, confirm_key="upd.rollback.confirm", version=o["version"]))
    else:
        inner = f'<p class="help">{esc(t("upd.rollback.none"))}</p>'
    return f'<section class="card"><h2>{esc(t("upd.rollback"))}</h2>{inner}</section>'


def unmanaged_card(req) -> str:
    from .. import __version__
    t = req.t
    return (f'<section class="card"><h2>{esc(t("upd.software"))}</h2><dl class="facts stacked"><div><dt>{esc(t("upd.version"))}</dt>'
            f'<dd>{esc(__version__)}</dd></div></dl><p class="help">{esc(t("upd.unmanaged"))}</p></section>')


def update_get(req):
    m = _ota(req)
    if m is None:
        return _page(req, "upd.title", unmanaged_card(req))
    i = m.info()
    inner = status_card(req, i) + software_card(req, i) + check_card(req, i) + upload_card(req, i) + rollback_card(req, i)
    return _page(req, "upd.title", inner)


def api_update(req):
    """For update.js: phase and progress only (no file paths, no RAUC internals beyond the progress message)."""
    m = _ota(req)
    if m is None:
        return _srv().json_response({"managed": False})
    i = m.info()
    pr = i.get("progress") or {}
    return _srv().json_response({"managed": True, "phase": i["phase"], "phase_text": req.t("upd.phase." + i["phase"]),
                                 "progress": {"done": pr.get("done", 0), "total": pr.get("total", 0), "message": pr.get("message", "")},
                                 "reboot_required": i["reboot_required"], "error": (i.get("error") or {}).get("code")})


def _action(fn, ok_key: str | None = None, back: str = BASE):
    def handler(req):
        m = _ota(req)
        if m is None:
            return _srv().redirect(back, req.t("upd.unmanaged"), req.secure, error=True)
        try:
            msg = fn(req, m)
        except UpdateError as e:
            return _srv().redirect(back, _err(req, e.code, e.detail), req.secure, error=True)
        return _srv().redirect(back, msg or (req.t(ok_key) if ok_key else None), req.secure)
    return handler


def _check(req, m):
    res = m.check()
    lt = res.get("latest") or {}
    return req.t("upd.check." + res["status"], version=lt.get("version", "–"), current=m.version)


def _install(req, m):
    m.install()


def _reboot(req, m):
    m.request_reboot()


def _restore(req, m):
    m.request_snapshot_restore()


@streamed(MAX_BUNDLE + UPLOAD_OVERHEAD)
def upload_post(req):
    m = _ota(req)
    if m is None:
        return _done(req, BASE, req.t("upd.unmanaged"), error=True)
    n = req.body_length
    try:
        boundary = boundary_of(req.h.headers.get("Content-Type", ""))
        path = m.begin_upload(max(0, n - 200))                  # the bundle is the body minus a few hundred bytes of multipart framing
    except TransferError as e:
        return _done(req, BASE, _err(req, e.code), error=True)
    except UpdateError as e:
        return _done(req, BASE, _err(req, e.code, e.detail), error=True)
    deadline = _now() + 60 + n / MIN_RATE
    left, sinks, names = n, [], []

    def read(k: int) -> bytes:
        nonlocal left
        if left <= 0:
            return b""
        if _now() > deadline:
            raise TransferError("timeout")
        block = req.h.rfile.read(min(k, left))
        if not block:
            raise TransferError("truncated")
        left -= len(block)
        return block

    def open_file(field: str, filename: str):
        if field != "bundle" or sinks:
            raise TransferError("bad_request", "one file field 'bundle' expected")
        fh = open(path, "wb")
        sinks.append(fh)
        names.append(filename)
        return fh
    try:
        try:
            parse_multipart(read, boundary, open_file, progress=lambda got: m.upload_progress(got))
        finally:
            for fh in sinks:
                fh.close()
    except TransferError as e:
        m.abort_upload(e.code, e.detail)
        return _done(req, BASE, _err(req, e.code), error=True)
    except OSError as e:                                         # socket timeout / reset while receiving, disk error
        m.abort_upload("upload_failed", type(e).__name__)
        return _done(req, BASE, _err(req, "upload_failed"), error=True)
    except Exception:                                            # never leave the manager in "uploading"
        m.abort_upload("upload_failed")
        raise
    if not sinks:
        m.abort_upload("no_file")
        return _done(req, BASE, _err(req, "no_file"), error=True)
    try:
        st = m.finish_upload(names[0])
    except UpdateError as e:
        return _done(req, BASE, _err(req, e.code, e.detail), error=True)
    return _done(req, BASE, req.t("upd.uploaded", version=st["version"]))


# ====================================================================== backup page
def backup_get(req):
    t, m = req.t, _ota(req)
    secrets_list = ", ".join(t("bak.secret." + k) for k in B.EXPORTED_SECRETS)
    export = (f'<section class="card"><h2>{esc(t("bak.export"))}</h2><form method="post" action="{BACKUP}/export" class="stack">'
              f'{_v().csrf(req)}<p class="help">{esc(t("bak.export.help"))}</p>'
              f'<label class="check"><input type="checkbox" name="include_secrets" value="on" aria-describedby="bak-secrets-help">'
              f'<span>{esc(t("bak.include_secrets"))}</span></label>'
              f'<p class="help" id="bak-secrets-help">{esc(t("bak.include_secrets.help", secrets=secrets_list))}</p>'
              f'<input type="text" name="username" value="owner" autocomplete="username" hidden>'
              f'<label>{esc(t("bak.passphrase"))}<input type="password" name="passphrase" autocomplete="new-password" minlength="{B.MIN_PASSPHRASE}"></label>'
              f'<label>{esc(t("bak.passphrase_confirm"))}<input type="password" name="confirm" autocomplete="new-password"></label>'
              f'<p class="help">{esc(t("bak.never"))}</p><button class="primary">{esc(t("bak.export.button"))}</button></form></section>')
    token = urllib.parse.quote(req.session["csrf"]) if req.session else ""
    imp = (f'<section class="card"><h2>{esc(t("bak.import"))}</h2>'
           f'<form method="post" action="{BACKUP}/import?csrf={token}" enctype="multipart/form-data" class="stack" '
           f'data-confirm="{esc(t("bak.import.confirm"))}">'
           f'<label>{esc(t("bak.import.file"))}<input type="file" name="backup" accept=".json,application/json" required></label>'
           f'<label>{esc(t("bak.import.passphrase"))}<input type="password" name="passphrase" autocomplete="off" aria-describedby="bak-imp-help"></label>'
           f'<p class="help" id="bak-imp-help">{esc(t("bak.import.help"))}</p><button>{esc(t("bak.import.button"))}</button></form></section>')
    snap = ""
    if m is not None:
        i = m.info()
        s = i.get("snapshot")
        if s and i["phase"] in ("idle", "staged") and not i.get("restore_requested"):
            snap = (f'<p>{esc(t("bak.snapshot.text", version=s["version"], time=_when(s.get("created"))))}</p>' +
                    _form(req, BACKUP + "/restore-snapshot", "bak.snapshot.button", danger=True, confirm_key="bak.snapshot.confirm"))
        elif i.get("restore_requested"):
            snap = f'<p>{esc(t("bak.snapshot.requested"))}</p>'
        else:
            snap = f'<p class="help">{esc(t("bak.snapshot.none"))}</p>'
        e = i.get("error") or {}
        if e.get("code") == "restore_failed":                  # review N-4: a failed restore at the start is reported here too
            snap = f'<p class="error" role="alert">{esc(_err(req, "restore_failed", e.get("detail") or ""))}</p>' + snap
        snap = f'<section class="card"><h2>{esc(t("bak.snapshot"))}</h2><p class="help">{esc(t("bak.snapshot.help"))}</p>{snap}</section>'
    return _page(req, "bak.title", export + imp + snap)


def backup_export(req):
    f, svc = req.form, req.ui.svc
    include = f.get("include_secrets") == "on"
    if include and f.get("passphrase", "") != f.get("confirm", ""):
        return _srv().redirect(BACKUP, req.t("bak.error.passphrase_mismatch"), req.secure, error=True)
    try:
        raw = B.export(svc.store, include, f.get("passphrase", ""))
    except B.BackupError as e:
        return _srv().redirect(BACKUP, _err(req, e.code, prefix="bak.error."), req.secure, error=True)
    svc.logs("backup exported" + (" (secrets encrypted)" if include else " (without secrets)"))
    name = "hifi-bridge-backup-" + time.strftime("%Y%m%d-%H%M") + ".json"
    r = _srv().Response(200, raw, "application/json")
    r.headers.append(("Content-Disposition", f'attachment; filename="{name}"'))
    return r


@streamed(B.MAX_BACKUP + UPLOAD_OVERHEAD)
def backup_import(req):
    svc, n = req.ui.svc, req.body_length
    deadline = _now() + 60
    left = n
    files: dict[str, io.BytesIO] = {}

    class Capped(io.BytesIO):
        def write(self, b):
            if self.tell() + len(b) > B.MAX_BACKUP:
                raise TransferError("too_large")
            return super().write(b)

    def read(k: int) -> bytes:
        nonlocal left
        if left <= 0:
            return b""
        if _now() > deadline:
            raise TransferError("timeout")
        block = req.h.rfile.read(min(k, left))
        if not block:
            raise TransferError("truncated")
        left -= len(block)
        return block

    def open_file(field, filename):
        if field != "backup" or files:
            raise TransferError("bad_request")
        files[field] = Capped()
        return files[field]
    try:
        fields = parse_multipart(read, boundary_of(req.h.headers.get("Content-Type", "")), open_file)
    except TransferError as e:
        return _srv().redirect(BACKUP, _err(req, e.code, prefix="bak.error."), req.secure, error=True)
    except OSError:
        return _srv().redirect(BACKUP, _err(req, "truncated", prefix="bak.error."), req.secure, error=True)
    if "backup" not in files:
        return _srv().redirect(BACKUP, _err(req, "invalid_file", prefix="bak.error."), req.secure, error=True)
    try:
        p = B.plan(B.parse(files["backup"].getvalue()), fields.get("passphrase", ""))
        res = B.apply(svc.store, p)
    except B.BackupError as e:
        return _srv().redirect(BACKUP, _err(req, e.code, e.detail if e.code == "invalid_settings" else "", prefix="bak.error."),
                               req.secure, error=True)
    svc.apply_restored_secrets(res["secrets"])
    svc.logs(f"backup restored: {len(res['changed'])} setting(s) changed, secrets: {', '.join(res['secrets']) or 'none'}")
    msg = req.t("bak.restored", n=len(res["changed"]))
    if res["skipped"]:
        msg += " " + req.t("bak.skipped", keys=", ".join(res["skipped"]))
    msg += " " + req.t("bak.secrets_restored" if res["secrets"] else "bak.secrets_not_included")
    if "admin_password_hash" in res["secrets"]:
        return _srv().redirect("/login", msg, req.secure)
    return _srv().redirect(BACKUP, msg, req.secure)


def routes(ui):
    return {
        ("GET", BASE): update_get, ("GET", "/api/update"): api_update, ("POST", BASE + "/upload"): upload_post,
        ("POST", BASE + "/check"): _action(_check),
        ("POST", BASE + "/download"): _action(lambda req, m: m.start_download(), "upd.download.started"),
        ("POST", BASE + "/cancel-download"): _action(lambda req, m: m.cancel_download(), "upd.download.cancelled"),
        ("POST", BASE + "/discard"): _action(lambda req, m: m.discard(), "upd.discarded"),
        ("POST", BASE + "/install"): _action(_install, "upd.install.started"),
        ("POST", BASE + "/reboot"): _action(_reboot, "upd.reboot.started"),
        ("POST", BASE + "/cancel"): _action(lambda req, m: m.cancel_installed(), "upd.cancelled"),
        ("POST", BASE + "/rollback"): _action(lambda req, m: m.rollback(), "upd.rollback.selected"),
        ("POST", BASE + "/dismiss"): _action(lambda req, m: m.dismiss()),
        ("POST", BACKUP + "/restore-snapshot"): _action(_restore, "bak.snapshot.started", back=BACKUP),
        ("GET", BACKUP): backup_get, ("POST", BACKUP + "/export"): backup_export, ("POST", BACKUP + "/import"): backup_import,
    }
