"""Network pages of the owner WebUI (docs/NETWORK.md): status, Wi-Fi (scan, join, hidden network, forget), IPv4/IPv6 addresses,
regulatory country and the setup hotspot. Strings: i18n/network.<lang>.json. Every action is a plain form (works without JavaScript);
static/network.js adds the confirmation countdown, country names, scan refresh and hides unused address fields.

Passwords are never sent back to the browser: password fields are always empty, the page only says whether one is stored.
Every change goes through ``NetworkService.save`` (a trial in the root agent); changes that affect the connection the owner may be using
must be confirmed on this page within three minutes or they are rolled back."""
from __future__ import annotations

import copy
import time

from ..net import netconfig
from ..net.netclient import AgentUnavailable, NotManaged

BASE = "/settings/network"
SETUP = "/setup/network"                 # the wizard's network step (views_setup_network.py) posts to its own routes with the same handlers
COUNTRIES = (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK "
    "CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT "
    "GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA "
    "MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS "
    "PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ "
    "UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW").split()
JOINABLE = ("wpa2", "wpa2/wpa3", "wpa3")
OTHER = "__other__"


def _v():
    from . import views
    return views


def _srv():
    from . import server
    return server


def esc(s) -> str:
    return _v().esc(s)


def _net(req):
    return getattr(req.ui.svc, "network", None)


def signal_word(dbm) -> str:
    if dbm is None:
        return "unknown"
    return "excellent" if dbm >= -55 else "good" if dbm >= -67 else "fair" if dbm >= -75 else "weak"


def summary(req) -> str:
    """One line for the settings overview."""
    t, net = req.t, _net(req)
    if net is None:
        return t("net.state.unmanaged")
    try:
        st = net.status()
    except Exception:
        return t("net.state.agent_unreachable")
    state = st.get("state", "unmanaged")
    if state in ("connected", "connecting", "probing") and (st.get("wifi") or {}).get("ssid"):
        return f'{t("net.state." + state)} · {st["wifi"]["ssid"]}'
    return t("net.state." + state)


# ------------------------------------------------------------------ small form helpers (labels always wrap their control)
def _input(req, name, label_key, value="", kind="text", help_key=None, **attrs) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items())
    hid = f"help-{name}"
    desc = f' aria-describedby="{hid}"' if help_key else ""
    helptext = f'<span class="help" id="{hid}">{esc(req.t(help_key))}</span>' if help_key else ""
    if kind == "checkbox":
        return (f'<label class="check"><input type="checkbox" name="{name}" value="on"{" checked" if value else ""}{desc} {extra}>'
                f'<span>{esc(req.t(label_key))}</span></label>{helptext}')
    return f'<label>{esc(req.t(label_key))}<input type="{kind}" name="{name}" value="{esc(value)}"{desc} {extra}></label>{helptext}'


def _select(req, name, label_key, value, options, help_key=None, **attrs) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items())
    hid = f"help-{name}"
    desc = f' aria-describedby="{hid}"' if help_key else ""
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == value else ""}>{esc(label)}</option>' for v, label in options)
    helptext = f'<span class="help" id="{hid}">{esc(req.t(help_key))}</span>' if help_key else ""
    return f'<label>{esc(req.t(label_key))}<select name="{name}"{desc} {extra}>{opts}</select></label>{helptext}'


def _country_select(req, value) -> str:
    opts = [("", req.t("net.country.choose"))] + [(c, c) for c in COUNTRIES]
    return _select(req, "country", "net.country", value or "", opts, "net.country.help", data_countries="1", required="required")


def _form(req, action, inner, button_key, danger=False, confirm_key=None, cls="stack") -> str:
    conf = f' data-confirm="{esc(req.t(confirm_key))}"' if confirm_key else ""
    return (f'<form method="post" action="{action}" class="{cls}"{conf}>{_v().csrf(req)}{inner}'
            f'<button{" class=danger" if danger else ""}>{esc(req.t(button_key))}</button></form>')


def _in_setup(req) -> bool:
    """The wizard is not finished: the network pages are reachable anyway (review N-1) and lead back to the wizard."""
    return not req.ui.svc.store.get("setup_complete")


def _page(req, title_key, inner, back=None):
    t = req.t
    if back:
        back_html = f'<p class="back"><a href="{back}">‹ {esc(t("net.title"))}</a></p>'
    elif _in_setup(req):
        back_html = f'<p class="back"><a href="{esc(_v().wizard_resume(req))}">‹ {esc(t("net.setup.back"))}</a></p>'
    else:
        back_html = f'<p class="back"><a href="/settings">‹ {esc(t("nav.settings"))}</a></p>'
    body = (f'<link rel="stylesheet" href="/static/network.css">{back_html}<h1>{esc(t(title_key))}</h1>'
            f'<div class="stack-cards">{inner}</div><script src="/static/network.js" defer></script>')
    return _v().page(req.ui, req, t(title_key), body, active="/settings")


def _status(req) -> dict:
    net = _net(req)
    if net is None:
        return {"managed": False, "state": "unmanaged", "interfaces": []}
    return net.status()


# ------------------------------------------------------------------ cards
def trial_card(req, st, base: str = BASE, extra: str = "") -> str:
    """Keep/Undo of a pending change (the forms post to ``base``/confirm and ``base``/revert), or what the last rolled-back change
    failed on. ``extra``: HTML the caller adds below the explanation (the wizard's network step: where the device is, what to do next)."""
    t, tr = req.t, st.get("trial")
    if not tr:
        return ""
    if tr["state"] == "pending":
        secs = tr.get("confirm_in_s") or 0
        return (f'<section class="card net-trial" role="alert"><h2>{esc(t("net.trial.title"))}</h2>'
                f'<p>{esc(t("net.trial.text"))}</p>{extra}<p><strong class="net-countdown" data-remaining="{int(secs)}">'
                f'{esc(t("net.trial.remaining", time=f"{secs // 60}:{secs % 60:02d}"))}</strong></p>'
                f'<div class="actions">{_form(req, base + "/confirm", "", "net.trial.keep", cls="inline")}'
                f'{_form(req, base + "/revert", "", "net.trial.undo", cls="inline")}</div></section>')
    if tr["state"] == "rolled_back":
        detail = ""
        addrs = (tr.get("detail") or {}).get("addresses") or []
        if addrs:
            detail = f'<p>{esc(t("net.trial.had_address", addresses=", ".join(addrs)))}</p>'
        return (f'<section class="card net-trial"><h2>{esc(t("net.trial.rolled_back"))}</h2>'
                f'<p>{esc(t("net.reason." + (tr.get("reason") or "timeout")))}</p>{detail}{extra}</section>')
    return ""


def _addresses(st) -> list[tuple[str, str]]:
    out = []
    for i in st.get("interfaces") or []:
        if i["addresses"]:
            out.append((i["name"], ", ".join(i["addresses"])))
    return out


def status_card(req, st) -> str:
    t = req.t
    state = st.get("state", "unmanaged")
    rows = [(t("net.mode"), esc(t("net.state." + state)))]
    wifi, hs = st.get("wifi") or {}, st.get("hotspot") or {}
    if state == "connected" and wifi.get("ssid"):
        sig = wifi.get("signal_dbm")
        rows.append((t("net.wifi"), esc(wifi["ssid"]) + (f' · {esc(t("net.signal." + signal_word(sig)))} ({sig} dBm)' if sig is not None else "")))
    if wifi.get("failure") and state != "connected":
        rows.append((t("net.last_problem"), esc(t("net.reason." + wifi["failure"]))))
    if st.get("fallback_in_s") is not None:
        rows.append((t("net.fallback"), esc(t("net.fallback_in", seconds=st["fallback_in_s"]))))
    if hs.get("active"):
        rows.append((t("net.hotspot"), esc(t("net.hotspot.active", ssid=hs.get("ssid") or "", address=hs.get("address"),
                                              n=hs.get("stations", 0)))))
        if hs.get("next_probe_in_s") is not None:
            rows.append((t("net.retry"), esc(t("net.retry_in", minutes=max(1, round(hs["next_probe_in_s"] / 60))))))
    for name, addrs in _addresses(st):
        rows.append((t("net.addresses_of", name=name), f'<code>{esc(addrs)}</code>'))
    if st.get("hostname"):
        rows.append((t("net.hostname"), f'{esc(st["hostname"])} · <a href="/settings/access">{esc(t("net.change"))}</a>'))
    synced = st.get("time_synchronized")
    rows.append((t("net.time"), esc(t("net.time." + ("synced" if synced else "unknown" if synced is None else "not_synced"),
                                      now=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())))))
    if st.get("firewall"):
        rows.append((t("net.firewall"), esc(t("net.firewall." + st["firewall"]))))
    if st.get("config_error"):
        rows.append((t("net.config_error"), esc(t("net.config_error.text"))))
    dl = "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in rows)
    note = ""
    if state == "agent_unreachable":
        note = f'<p class="help">{esc(t("net.agent_unreachable.help"))}</p>'
    elif not st.get("managed"):
        note = f'<p class="help">{esc(t("net.unmanaged.help"))}</p>'
    return f'<section class="card"><h2>{esc(t("net.status"))}</h2><dl class="facts stacked">{dl}</dl>{note}</section>'


def wifi_join(req, st, cfg, base: str = BASE, forget: bool = True, confirm_key: str | None = "net.wifi.join_confirm",
              before_button: str = "") -> str:
    """The saved network, the scan list, hidden-network entry, the password (always empty) and the country if none is set; the forms
    post to ``base``/scan and ``base``/wifi. Shared by the network page and the wizard's network step, which explains the change in
    ``before_button`` (right above the Connect button) instead of a confirmation dialog."""
    t = req.t
    wifi = cfg.get("wifi")
    parts = []
    if wifi:
        drop = _form(req, BASE + "/forget", "", "net.wifi.forget", danger=True, confirm_key="net.wifi.forget_confirm") if forget else ""
        parts.append(f'<p>{esc(t("net.wifi.saved", ssid=wifi["ssid"]))}{" · " + esc(t("net.wifi.hidden_flag")) if wifi["hidden"] else ""}'
                     f'</p>{drop}')
    scan = st.get("scan") or {}
    results = scan.get("results") or []
    rows = []
    for i, n in enumerate(results):
        ok = n.get("security") in JOINABLE
        sec = t("net.security." + str(n.get("security")))
        checked = " checked" if wifi and n["ssid"] == wifi["ssid"] else ""
        rows.append(f'<label class="choice radio{"" if ok else " muted"}"><input type="radio" name="ssid" value="{esc(n["ssid"])}"'
                    f'{checked}{"" if ok else " disabled"}><span><strong>{esc(n["ssid"])}</strong><small>'
                    f'{esc(t("net.signal." + signal_word(n.get("signal_dbm"))))} · {esc(sec)}'
                    f'{"" if ok else " · " + esc(t("net.security.unsupported"))}</small></span></label>')
    rows.append(f'<label class="choice radio"><input type="radio" name="ssid" value="{OTHER}"{"" if results else " checked"}>'
                f'<span><strong>{esc(t("net.wifi.other"))}</strong><small>{esc(t("net.wifi.other_help"))}</small></span></label>')
    scanning = ' data-scanning="1"' if scan.get("pending") else ""
    if scan.get("age_s") is not None:
        age = t("net.scan.age", minutes=round(scan["age_s"] / 60))
    else:
        age = t("net.scan.none")
    if scan.get("supported") is False:
        age = t("net.scan.unsupported")
    join = (f'<fieldset><legend>{esc(t("net.wifi.choose"))}</legend><div class="choices">{"".join(rows)}</div></fieldset>'
            + _input(req, "ssid_other", "net.wifi.ssid", "", maxlength="32", autocapitalize="off", spellcheck="false", autocomplete="off")
            + _input(req, "hidden", "net.wifi.hidden", False, "checkbox", "net.wifi.hidden_help")
            + _input(req, "password", "net.wifi.password", "", "password", "net.wifi.password_help" if not wifi else "net.wifi.password_keep",
                     autocomplete="new-password", maxlength="64")
            + ("" if cfg.get("country") else _country_select(req, cfg.get("country"))) + before_button)
    managed = st.get("managed")
    scan_form = _form(req, base + "/scan", "", "net.scan.again", cls="inline") if managed else ""
    body = (f'<p class="help"{scanning} id="scan-state">{esc(t("net.scan.running") if scan.get("pending") else age)}</p>{scan_form}'
            + (_form(req, base + "/wifi", join, "net.wifi.join", confirm_key=confirm_key) if managed else ""))
    return "".join(parts) + body


def wifi_card(req, st, cfg) -> str:
    return f'<section class="card"><h2>{esc(req.t("net.wifi.title"))}</h2>{wifi_join(req, st, cfg)}</section>'


def links_card(req) -> str:
    t = req.t
    items = "".join(f'<li><a class="setting-link" href="{BASE}/{p}"><span><strong>{esc(t(k))}</strong><small>{esc(t(k + ".summary"))}</small>'
                    f'</span><span aria-hidden="true" class="chev">›</span></a></li>' for p, k in (("addresses", "net.ip.title"),
                                                                                               ("hotspot", "net.hotspot.title")))
    return f'<section class="card"><h2>{esc(t("net.more"))}</h2><ul class="settings-list">{items}</ul></section>'


# ------------------------------------------------------------------ pages
def _cfg(req) -> dict:
    net = _net(req)
    try:
        return net.config() if net else netconfig.defaults()
    except netconfig.NetConfigError:
        return netconfig.defaults()


def setup_note(req) -> str:
    if not _in_setup(req):
        return ""
    return f'<section class="card"><p class="help">{esc(req.t("net.setup.note"))}</p></section>'


def pending_banner(req) -> str:
    """The confirm/undo banner of a pending network change, for the wizard pages: after joining the home Wi-Fi from the setup hotspot
    the owner logs in again at the new address and lands in the wizard, which must offer "Keep" before the change is rolled back."""
    net = _net(req)
    if net is None:
        return ""
    try:
        st = net.status()
    except Exception:                                                   # a wizard page never fails because of the network
        return ""
    if (st.get("trial") or {}).get("state") != "pending":
        return ""
    return '<link rel="stylesheet" href="/static/network.css">' + trial_card(req, st, base=SETUP)    # the owner stays in the wizard


def trial_pending(req) -> bool:
    """A network change waits for the owner's confirmation (the wizard then resumes at its network step). Never raises."""
    net = _net(req)
    if net is None:
        return False
    try:
        return (net.status().get("trial") or {}).get("state") == "pending"
    except Exception:
        return False


def setup_hint(req) -> str:
    """Wizard, Lyrion step: no server can be found while the device is not on the home network (review N-1); the wizard's network
    step is the place to fix that (DECISIONS #122)."""
    t = req.t
    return f'<p class="help">{esc(t("net.setup.hint"))} <a href="{SETUP}">{esc(t("net.setup.link"))}</a></p>'


def network_get(req):
    st = _status(req)
    cfg = _cfg(req)
    inner = (setup_note(req) + trial_card(req, st) + status_card(req, st) + wifi_card(req, st, cfg)
             + (links_card(req) if st.get("managed") else ""))
    return _page(req, "net.title", inner)


def _ip_fields(req, prefix, ip) -> str:
    t = req.t
    v4, v6 = ip["ipv4"], ip["ipv6"]
    m4 = _select(req, f"{prefix}_ipv4_method", "net.ip.v4_method", v4["method"],
                 [("dhcp", t("net.ip.dhcp")), ("static", t("net.ip.static"))], data_method="1")
    s4 = (f'<div class="static-fields" data-for="{prefix}_ipv4_method">'
          + _input(req, f"{prefix}_ipv4_address", "net.ip.address4", v4.get("address", ""), help_key="net.ip.address4_help",
                   spellcheck="false", autocapitalize="off", inputmode="decimal")
          + _input(req, f"{prefix}_ipv4_gateway", "net.ip.gateway", v4.get("gateway", ""), spellcheck="false", inputmode="decimal")
          + _input(req, f"{prefix}_ipv4_dns", "net.ip.dns", " ".join(v4.get("dns", [])), help_key="net.ip.dns_help", spellcheck="false")
          + "</div>")
    m6 = _select(req, f"{prefix}_ipv6_method", "net.ip.v6_method", v6["method"],
                 [("auto", t("net.ip.auto")), ("static", t("net.ip.static")), ("off", t("net.ip.off"))], data_method="1")
    s6 = (f'<div class="static-fields" data-for="{prefix}_ipv6_method">'
          + _input(req, f"{prefix}_ipv6_address", "net.ip.address6", v6.get("address", ""), help_key="net.ip.address6_help",
                   spellcheck="false", autocapitalize="off")
          + _input(req, f"{prefix}_ipv6_gateway", "net.ip.gateway", v6.get("gateway", ""), spellcheck="false")
          + _input(req, f"{prefix}_ipv6_dns", "net.ip.dns", " ".join(v6.get("dns", [])), spellcheck="false")
          + "</div>")
    return (f'<input type="hidden" name="iface" value="{prefix}"><fieldset><legend>IPv4</legend>{m4}{s4}</fieldset>'
            f'<fieldset><legend>IPv6</legend>{m6}{s6}</fieldset>')


def addresses_get(req):
    t = req.t
    cfg, st = _cfg(req), _status(req)
    if not st.get("managed"):
        return _srv().redirect(BASE)
    cards = (f'<section class="card"><h2>{esc(t("net.ip.ethernet"))}</h2>'
             + _form(req, BASE + "/ip", _ip_fields(req, "ethernet", cfg["ethernet"]), "action.save", confirm_key="net.ip.confirm") + "</section>")
    if cfg.get("wifi"):
        cards += (f'<section class="card"><h2>{esc(t("net.ip.wifi", ssid=cfg["wifi"]["ssid"]))}</h2>'
                  + _form(req, BASE + "/ip", _ip_fields(req, "wifi", cfg["wifi"]), "action.save", confirm_key="net.ip.confirm") + "</section>")
    cards += f'<section class="card"><p class="help">{esc(t("net.ip.help"))}</p></section>'
    return _page(req, "net.ip.title", cards, back=BASE)


def hotspot_get(req):
    t = req.t
    cfg, st = _cfg(req), _status(req)
    if not st.get("managed"):
        return _srv().redirect(BASE)
    hs = cfg.get("hotspot")
    state = t("net.hotspot.configured", ssid=hs["ssid"]) if hs else t("net.hotspot.none")
    inner = (_country_select(req, cfg.get("country"))
             + _input(req, "hotspot_ssid", "net.hotspot.ssid", hs["ssid"] if hs else "", maxlength="32", autocomplete="off", spellcheck="false")
             + _input(req, "hotspot_password", "net.hotspot.password", "", "password",
                      "net.hotspot.password_keep" if hs else "net.hotspot.password_help", autocomplete="new-password", maxlength="63",
                      minlength=str(netconfig.HOTSPOT_PSK_MIN)))
    cards = (f'<section class="card"><h2>{esc(t("net.hotspot.title"))}</h2><p>{esc(state)}</p><p class="help">{esc(t("net.hotspot.help"))}</p>'
             + _form(req, BASE + "/hotspot", inner, "action.save") + "</section>")
    if hs:
        cards += (f'<section class="card"><h2>{esc(t("net.hotspot.remove_title"))}</h2><p class="help">{esc(t("net.hotspot.remove_help"))}</p>'
                  + _form(req, BASE + "/hotspot/remove", "", "net.hotspot.remove", danger=True, confirm_key="net.hotspot.remove_confirm")
                  + "</section>")
    return _page(req, "net.hotspot.title", cards, back=BASE)


# ------------------------------------------------------------------ actions
def problem_text(req, problems: list[str]) -> str:
    t = req.t
    out = []
    for p in problems[:3]:
        fld, _, reason = p.partition(":")
        label = t("net.field." + fld) if ("net.field." + fld) in req.t.s else fld
        out.append(t("net.problem", field=label, reason=t("net.err." + reason) if ("net.err." + reason) in req.t.s else reason))
    return "; ".join(out)


def _save(req, new: dict, back: str, pending_to: str = BASE):
    t, net = req.t, _net(req)
    if net is None:
        return _srv().redirect(back, t("net.unmanaged.help"), req.secure, error=True)
    try:
        r = net.save(new)
    except netconfig.NetConfigError as e:
        return _srv().redirect(back, problem_text(req, e.problems), req.secure, error=True)
    except NotManaged:
        return _srv().redirect(back, t("net.unmanaged.help"), req.secure, error=True)
    except AgentUnavailable as e:                                           # handed over without an answer / never reached it
        return _srv().redirect(pending_to if e.sent else back, t("net.busy" if e.sent else "net.agent_unreachable.help"), req.secure,
                               error=True)
    if r["result"] == "error":
        return _srv().redirect(back, problem_text(req, r.get("problems") or ["document:" + r.get("error", "invalid")]), req.secure, error=True)
    if r["result"] == "pending":
        return _srv().redirect(pending_to, t("net.saved_pending"), req.secure)
    return _srv().redirect(back, t("net.saved" if r["result"] == "confirmed" else "net.unchanged"), req.secure)


def wifi_post(req, back: str = BASE):
    f, cfg = req.form, copy.deepcopy(_cfg(req))
    ssid = f.get("ssid", "")
    if ssid == OTHER or not ssid:
        ssid = f.get("ssid_other", "").strip()
    old = cfg.get("wifi")
    psk = f.get("password", "")
    same = bool(old and old["ssid"] == ssid)
    if not psk and same:
        psk = old["psk"]                                                    # re-join / toggle "hidden" without retyping
    wifi = {"ssid": ssid, "psk": psk, "hidden": f.get("hidden") == "on"}
    if same:
        wifi.update(ipv4=old["ipv4"], ipv6=old["ipv6"])
    cfg["wifi"] = wifi
    if f.get("country"):
        cfg["country"] = f["country"]
    return _save(req, cfg, back, pending_to=back)


def forget_post(req):
    cfg = copy.deepcopy(_cfg(req))
    cfg["wifi"] = None
    return _save(req, cfg, BASE)


def scan_post(req, back: str = BASE):
    net = _net(req)
    ok = bool(net and net.scan())
    return _srv().redirect(back, req.t("net.scan.started" if ok else "net.scan.failed"), req.secure, error=not ok)


def _ip_from_form(f, prefix) -> dict:
    def block(v):
        m = f.get(f"{prefix}_{v}_method", "dhcp" if v == "ipv4" else "auto")
        if m != "static":
            return {"method": m}
        return {"method": m, "address": f.get(f"{prefix}_{v}_address", ""), "gateway": f.get(f"{prefix}_{v}_gateway", ""),
                "dns": f.get(f"{prefix}_{v}_dns", "")}
    return {"ipv4": block("ipv4"), "ipv6": block("ipv6")}


def ip_post(req):
    f, cfg = req.form, copy.deepcopy(_cfg(req))
    back = BASE + "/addresses"
    iface = f.get("iface")
    if iface == "ethernet":
        cfg["ethernet"] = _ip_from_form(f, "ethernet")
    elif iface == "wifi" and cfg.get("wifi"):
        cfg["wifi"].update(_ip_from_form(f, "wifi"))
    else:
        return _srv().redirect(back, req.t("error.generic"), req.secure, error=True)
    return _save(req, cfg, back)


def hotspot_post(req):
    f, cfg = req.form, copy.deepcopy(_cfg(req))
    back = BASE + "/hotspot"
    cfg["country"] = f.get("country") or None
    ssid, psk = f.get("hotspot_ssid", "").strip(), f.get("hotspot_password", "")
    old = cfg.get("hotspot")
    if ssid or psk:
        if not psk and old:
            psk = old["psk"]                                                # rename only: keep the owner-known password
        cfg["hotspot"] = {"ssid": ssid, "psk": psk}
    return _save(req, cfg, back)


def hotspot_remove_post(req):
    cfg = copy.deepcopy(_cfg(req))
    cfg["hotspot"] = None
    return _save(req, cfg, BASE + "/hotspot")


def _trial_answer(req, ok, done_key: str, back: str, done_to: str | None = None):
    """True done, False nothing waiting, None = the agent did not answer (review N-3: the next status reconciles)."""
    key = done_key if ok else ("net.busy" if ok is None else "net.trial.too_late")
    return _srv().redirect((done_to or back) if ok else back, req.t(key), req.secure, error=not ok)


def confirm_post(req, back: str = BASE, done_to: str | None = None):
    net = _net(req)
    return _trial_answer(req, net.confirm() if net else False, "net.trial.kept", back, done_to)


def revert_post(req, back: str = BASE):
    net = _net(req)
    return _trial_answer(req, net.revert() if net else False, "net.trial.undone", back)


def api_network(req):
    st = _status(req)
    tr = st.get("trial") or {}
    scan = st.get("scan") or {}
    return _srv().json_response({"state": st.get("state"), "trial": {"state": tr.get("state"), "confirm_in_s": tr.get("confirm_in_s")},
                                 "scan": {"pending": scan.get("pending"), "age_s": scan.get("age_s")}})


def routes(ui):
    return {
        ("GET", BASE): network_get, ("GET", BASE + "/addresses"): addresses_get, ("GET", BASE + "/hotspot"): hotspot_get,
        ("POST", BASE + "/wifi"): wifi_post, ("POST", BASE + "/forget"): forget_post, ("POST", BASE + "/scan"): scan_post,
        ("POST", BASE + "/ip"): ip_post, ("POST", BASE + "/hotspot"): hotspot_post, ("POST", BASE + "/hotspot/remove"): hotspot_remove_post,
        ("POST", BASE + "/confirm"): confirm_post, ("POST", BASE + "/revert"): revert_post, ("GET", "/api/network"): api_network,
    }
