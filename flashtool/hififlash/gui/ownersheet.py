"""The owner sheet (DE/EN) shown after writing, printable and saveable as PDF or text. It contains what the owner must keep: the setup code
and the setup hotspot's name and password, plus how to find the device. It NEVER contains the trust key or the home Wi-Fi password
(docs/FLASH_TOOL.md §5, tested)."""
from __future__ import annotations

import html


def address(doc: dict) -> str:
    return f"{doc.get('local_hostname') or 'hifi-bridge'}.local"


def rows(doc: dict, tr) -> list[tuple[str, str]]:
    out = [(tr("sheet.device"), doc.get("device_name") or "Hi-Fi Bridge"), (tr("sheet.setup_code"), doc["setup_code"])]
    if "hotspot" in doc:
        out += [(tr("sheet.hotspot_ssid"), doc["hotspot"]["ssid"]), (tr("sheet.hotspot_psk"), doc["hotspot"]["psk"])]
    if "wifi" in doc:
        out.append((tr("sheet.wifi"), doc["wifi"]["ssid"]))
    out += [(tr("sheet.country"), doc["country"]), (tr("sheet.address"), f"https://{address(doc)}")]
    return out


def steps(doc: dict, tr) -> list[str]:
    s = [tr("sheet.step.insert"), tr("sheet.step.power")]
    if "wifi" in doc:
        s.append(tr("sheet.step.wifi", address=address(doc)))
    else:
        s.append(tr("sheet.step.ethernet", address=address(doc)))
    if "hotspot" in doc:
        s.append(tr("sheet.step.hotspot", ssid=doc["hotspot"]["ssid"]))
    s += [tr("sheet.step.code"), tr("sheet.step.trust")]
    return s


def to_html(doc: dict, tr) -> str:
    e = html.escape
    body = "".join(f"<tr><th align='left' style='padding:4px 16px 4px 0'>{e(k)}</th><td style='font-family:monospace;font-size:12pt'>{e(v)}</td></tr>"
                   for k, v in rows(doc, tr))
    items = "".join(f"<li>{e(x)}</li>" for x in steps(doc, tr))
    return (f"<h1 style='font-family:serif'>Hi-Fi Bridge</h1><p>{e(tr('sheet.intro'))}</p><table>{body}</table>"
            f"<h3>{e(tr('sheet.steps'))}</h3><ol>{items}</ol><p><i>{e(tr('sheet.keep'))}</i></p>")


def to_text(doc: dict, tr) -> str:
    lines = ["Hi-Fi Bridge", "", tr("sheet.intro"), ""] + [f"{k}: {v}" for k, v in rows(doc, tr)]
    lines += ["", tr("sheet.steps")] + [f"{i}. {x}" for i, x in enumerate(steps(doc, tr), 1)] + ["", tr("sheet.keep")]
    return "\n".join(lines) + "\n"
