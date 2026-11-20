"""Setup wizard, network step (DECISIONS #122, #137; docs/NETWORK.md section 12). Strings: i18n/network.<lang>.json (``wizard.net.*``).

The step sits right after the owner account. It adds no network logic of its own: the Wi-Fi form, the trial with its confirmation and
automatic rollback, and every action handler are the network page's (``views_network``), posting to wizard routes so the owner stays
in the wizard. What the step adds is the guidance around the change:

* already online over Ethernet or a working client Wi-Fi: one confirmation line with the address, *Continue*, and an optional
  "set up Wi-Fi anyway";
* not online (setup hotspot, no network): the Wi-Fi choice, and BEFORE the owner presses Connect an explanation of what happens - this
  page loses its connection, join the home Wi-Fi, open the device at its name (shown with the port of the current address), confirm
  within 3 minutes; on failure the previous settings and the hotspot come back by themselves;
* a pending change: Keep/Undo (also after a new login at another address: ``views.wizard_resume`` lands here);
* a rolled-back change: the reason, retry below, or skip.

Works without JavaScript (plain forms and links; network.js only adds the countdown, scan refresh and country names). The Wi-Fi password
is never sent back to the browser."""
from __future__ import annotations

from . import views_network as vn
from .views_network import SETUP, esc

NEXT = "/setup/bluetooth"


def _v():
    from . import views
    return views


def _status(req) -> dict:
    """Like the network page's status, but a wizard page never fails because of the network service."""
    net = vn._net(req)
    if net is None:
        return {"managed": False, "state": "unmanaged", "interfaces": []}
    try:
        return net.status()
    except Exception:
        return {"managed": False, "state": "agent_unreachable", "interfaces": []}


def _ips(iface: dict) -> list[str]:
    """Addresses an owner can type: without prefix length, IPv4 first, no link-local IPv6."""
    out = [a.split("/")[0] for a in iface.get("addresses") or []]
    out = [a for a in out if not a.lower().startswith("fe80:")]
    return sorted(out, key=lambda a: (":" in a, a))


def online(st: dict) -> list[tuple[str, str | None, list[str]]]:
    """How the device is connected to a network right now: ``(kind, ssid, addresses)`` with kind ethernet | wifi | host (installation
    without its own network service: the host's addresses with a default route). Empty while only the setup hotspot (or nothing) runs."""
    ifs = st.get("interfaces") or []
    if not st.get("managed"):
        if st.get("state") != "unmanaged":
            return []                                                   # agent not reachable: claim nothing
        addrs = [a for i in ifs if i.get("default_route") for a in _ips(i)]
        return [("host", None, addrs)] if addrs else []
    out = []
    if (st.get("ethernet") or {}).get("up"):
        out.append(("ethernet", None, [a for i in ifs if i.get("role") == "ethernet" for a in _ips(i)]))
    if st.get("state") == "connected":
        out.append(("wifi", (st.get("wifi") or {}).get("ssid"), [a for i in ifs if i.get("role") == "wifi" for a in _ips(i)]))
    return out


def device_url(req) -> str:
    """The address to open after the switch: the device's mDNS name with the scheme and port of the current request (port 80/443 on the
    device; the lab and demo publish other ports)."""
    svc = req.ui.svc
    name = svc.access.main_name() if svc.access is not None else f'{svc.store.get("local_hostname")}.local'
    host = req.host or ""
    port = host.rsplit("]:", 1)[1] if host.startswith("[") and "]:" in host else (host.split(":", 1)[1] if host.count(":") == 1 else "")
    default = {"http": "80", "https": "443"}.get(req.scheme)
    return f"{req.scheme}://{name}{':' + port if port and port.isdigit() and port != default else ''}/"


def _html(req, key: str, **html) -> str:
    """A translated sentence whose placeholders are already-escaped HTML (links, emphasis); the sentence itself is escaped."""
    return esc(req.t(key)).format(**html)


def _explain(req, st: dict) -> str:
    """What pressing Connect does, shown right above the button (no dialog: readable before the change, also without JavaScript)."""
    t = req.t
    hs, wifi = st.get("hotspot") or {}, st.get("wifi") or {}
    eth_up = (st.get("ethernet") or {}).get("up")
    if eth_up and not hs.get("active"):
        items = [esc(t("wizard.net.next.keep_ethernet"))]
    elif hs.get("active") or st.get("state") == "connected":
        url = device_url(req)
        link = f'<a href="{esc(url)}">{esc(url)}</a>'
        name = esc(req.ui.svc.store.get("local_hostname"))
        if hs.get("active"):
            first = _html(req, "wizard.net.next.lose_hotspot", hotspot=f'<strong>{esc(hs.get("ssid") or "")}</strong>')
            last = _html(req, "wizard.net.next.fail_hotspot", hotspot=f'<strong>{esc(hs.get("ssid") or "")}</strong>',
                         address=f'<a href="http://{esc(hs.get("address"))}/">http://{esc(hs.get("address"))}/</a>')
        else:
            first = _html(req, "wizard.net.next.lose_wifi", ssid=f'<strong>{esc(wifi.get("ssid") or "")}</strong>')
            last = _html(req, "wizard.net.next.fail_wifi", ssid=f'<strong>{esc(wifi.get("ssid") or "")}</strong>')
        items = [first, esc(t("wizard.net.next.rejoin")), _html(req, "wizard.net.next.open", url=link, name=f"<strong>{name}</strong>"), last]
    else:
        items = [esc(t("wizard.net.next.keep"))]
    lis = "".join(f"<li>{i}</li>" for i in items)
    return (f'<section class="net-explain" aria-labelledby="net-explain-title"><h3 id="net-explain-title">{esc(t("wizard.net.next.title"))}'
            f'</h3><ol class="steps-list">{lis}</ol></section>')


def _join(req, st, cfg) -> str:
    return vn.wifi_join(req, st, cfg, base=SETUP, forget=False, confirm_key=None, before_button=_explain(req, st))


def _continue(req, key: str = "wizard.next", primary: bool = True) -> str:
    return f'<p class="actions"><a class="button{" primary" if primary else ""}" href="{NEXT}">{esc(req.t(key))}</a></p>'


def _online_lines(req, st, lines) -> str:
    t = req.t
    out = []
    for kind, ssid, addrs in lines:
        address = ", ".join(addrs) or "–"
        out.append(f'<li>{_html(req, "wizard.net.online." + kind, address=f"<strong>{esc(address)}</strong>", ssid=esc(ssid or ""))}</li>')
    wifi = st.get("wifi") or {}
    if st.get("managed") and wifi.get("configured") and st.get("state") != "connected":
        state = t("net.state." + st.get("state", "connecting"))
        problem = f' · {esc(t("net.reason." + wifi["failure"]))}' if wifi.get("failure") else ""
        out.append(f'<li>{esc(t("net.wifi"))}: {esc(state)} · {esc(wifi.get("ssid") or "")}{problem}</li>')
    return f'<ul class="net-online">{"".join(out)}</ul>'


def _offline_status(req, st) -> str:
    t = req.t
    hs, wifi = st.get("hotspot") or {}, st.get("wifi") or {}
    if hs.get("active"):
        text = t("wizard.net.offline.hotspot", ssid=hs.get("ssid") or "")
    elif wifi.get("configured") and st.get("state") in ("connecting", "probing"):
        text = t("wizard.net.offline.connecting", ssid=wifi.get("ssid") or "")
    else:
        text = t("wizard.net.offline.none")
    extra = ""
    if wifi.get("failure"):
        extra += f'<p>{esc(t("net.last_problem"))}: {esc(t("net.reason." + wifi["failure"]))}</p>'
    if st.get("fallback_in_s") is not None:
        extra += f'<p class="help">{esc(t("net.fallback"))}: {esc(t("net.fallback_in", seconds=st["fallback_in_s"]))}</p>'
    return f'<p>{esc(text)}</p>{extra}'


def _pending_extra(req, st, cfg) -> str:
    t, tr = req.t, st.get("trial") or {}
    ssid = (cfg.get("wifi") or {}).get("ssid")
    if not (set(tr.get("changed") or ()) & {"wifi", "country"}) or not ssid:
        return f'<p>{esc(t("wizard.net.pending.help"))}</p>'
    addrs = (tr.get("detail") or {}).get("addresses") or []
    line = (t("wizard.net.pending.joined", ssid=ssid, addresses=", ".join(addrs)) if (tr.get("detail") or {}).get("wifi_connected")
            else t("wizard.net.pending.trying", ssid=ssid))
    return f'<p>{esc(line)}</p><p>{esc(t("wizard.net.pending.help"))}</p>'


def setup_network_get(req):
    t = req.t
    st, cfg = _status(req), vn._cfg(req)
    scan, state = st.get("scan") or {}, st.get("state")
    if st.get("managed") and state not in ("hotspot", "unavailable") and scan.get("age_s") is None and not scan.get("pending"):
        if vn._net(req).scan():                    # no list yet (the hotspot brings one along): ask once, network.js refreshes the page
            st = _status(req)
    tr = st.get("trial") or {}
    parts = [f'<h1>{esc(t("wizard.net.title"))}</h1><p class="lead">{esc(t("wizard.net.text"))}</p>']
    if tr.get("state") == "pending":
        parts.append(vn.trial_card(req, st, base=SETUP, extra=_pending_extra(req, st, cfg)))   # no "continue" before Keep or Undo
    else:
        lines = online(st)
        if tr.get("state") == "rolled_back":
            key = "wizard.net.failed.hotspot" if (st.get("hotspot") or {}).get("active") else (
                "wizard.net.failed.online" if lines else "wizard.net.failed.retry")
            parts.append(vn.trial_card(req, st, base=SETUP, extra=f'<p>{esc(t(key))}</p>'))
        if lines:
            parts.append(_online_lines(req, st, lines) + _continue(req))
            if st.get("managed") and state != "unavailable":
                summary = "wizard.net.wifi_other" if any(k == "wifi" for k, _, _ in lines) else "wizard.net.wifi_anyway"
                parts.append(f'<details class="net-more"><summary>{esc(t(summary))}</summary>'
                             f'<p class="help">{esc(t(summary + ".help"))}</p><h2>{esc(t("wizard.net.choose"))}</h2>'
                             f'{_join(req, st, cfg)}</details>')                 # h2: the explanation below is an h3 (heading order)
        elif state == "agent_unreachable":
            parts.append(f'<p>{esc(t("net.agent_unreachable.help"))}</p>'
                         f'<p class="actions"><a class="button" href="{SETUP}">{esc(t("wizard.net.reload"))}</a></p>'
                         + _continue(req, "wizard.net.skip", primary=False) + f'<p class="help">{esc(t("wizard.net.skip.help"))}</p>')
        elif not st.get("managed"):
            parts.append(f'<p>{esc(t("wizard.net.unmanaged"))}</p>' + _continue(req))
        elif state == "unavailable":
            parts.append(f'<p>{esc(t("wizard.net.no_wifi"))}</p>'
                         + _continue(req, "wizard.net.skip", primary=False) + f'<p class="help">{esc(t("wizard.net.skip.help"))}</p>')
        else:
            parts.append(_offline_status(req, st) + f'<h2>{esc(t("wizard.net.choose"))}</h2>' + _join(req, st, cfg)
                         + f'<div class="net-skip">{_continue(req, "wizard.net.skip", primary=False)}'
                           f'<p class="help">{esc(t("wizard.net.skip.help"))}</p></div>')
    body = (f'<link rel="stylesheet" href="/static/network.css">{_v().wizard_steps(req, 1, banner=False)}'
            f'<section class="card narrow wizard-net">{"".join(parts)}</section><script src="/static/network.js" defer></script>')
    return _v().page(req.ui, req, t("wizard.net.title"), body, wizard=True)


def routes(ui):
    """The network page's handlers, answering back to the wizard step."""
    return {
        ("GET", SETUP): setup_network_get,
        ("POST", SETUP + "/wifi"): lambda req: vn.wifi_post(req, SETUP),
        ("POST", SETUP + "/scan"): lambda req: vn.scan_post(req, SETUP),
        ("POST", SETUP + "/confirm"): lambda req: vn.confirm_post(req, SETUP, done_to=NEXT),     # kept: on to the next step
        ("POST", SETUP + "/revert"): lambda req: vn.revert_post(req, SETUP),
    }
