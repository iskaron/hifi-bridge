"""Pre-flight check ON THE TEST PI (read-only): are the tools, versions and the configuration that the lab findings require in place?
Every check returns PASS / WARN / FAIL with a concrete fix. Nothing is changed. Findings it encodes: docs/PIPEWIRE_CAPTURE.md."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess

from .env import read, run


def _cfg_text(paths: list[str], reader=read) -> str:
    return "\n".join(t for p in paths for t in [reader(p)] if t)


def checks(runner=run, reader=read, which=shutil.which, globber=glob.glob, home: str | None = None) -> list[dict]:
    home = home or os.path.expanduser("~")
    out = []

    def add(name, status, detail, fix=""):
        out.append({"check": name, "status": status, "detail": detail, "fix": fix})

    model = reader("/proc/device-tree/model") or ""
    add("board", "PASS" if "Raspberry Pi" in model else "WARN", model or "unknown", "run the kit on the Raspberry Pi under test")
    for tool, why in (("bluetoothd", "BlueZ"), ("bluetoothctl", "pairing by hand"), ("pipewire", "audio graph"), ("wireplumber", "session manager"),
                      ("pactl", "pipewire-pulse control"), ("parec", "default capture tool"), ("pw-dump", "node discovery (A3)"),
                      ("busctl", "D-Bus traces by hand")):
        add(f"tool:{tool}", "PASS" if which(tool) else ("FAIL" if tool in ("bluetoothd", "pipewire", "wireplumber", "parec", "pactl") else "WARN"),
            which(tool) or "missing", f"install the package that provides {tool} ({why})")
    try:
        import jeepney  # noqa: F401
        add("python:jeepney", "PASS", jeepney.__version__)
    except ImportError:
        add("python:jeepney", "FAIL", "missing", "apt install python3-jeepney")
    ver = runner(["bluetoothd", "-v"]) or ""
    add("bluez:version", "PASS" if re.match(r"5\.(6[4-9]|[7-9]\d)", ver) else "WARN", ver or "unknown", "BlueZ >= 5.64 recommended")
    units = _cfg_text(["/lib/systemd/system/bluetooth.service", "/etc/systemd/system/bluetooth.service.d/override.conf"]
                      + globber("/etc/systemd/system/bluetooth.service.d/*.conf"), reader)
    add("bluez:experimental", "PASS" if "--experimental" in units or "-E" in units.split() else "WARN",
        "bluetoothd started with --experimental" if "--experimental" in units else "not found",
        "plan A1 decides whether MediaPlayer1 needs it; see hwkit/pi-setup/bluetooth-override.conf")
    pw = _cfg_text(globber("/etc/pipewire/pipewire.conf.d/*.conf") + globber(f"{home}/.config/pipewire/pipewire.conf.d/*.conf"), reader)
    add("pipewire:allowed-rates", "PASS" if re.search(r"allowed-rates\s*=\s*\[[^\]]*44100", pw) else "FAIL",
        "44.1 kHz allowed" if "44100" in pw else "default graph 48 kHz only (capture would be resampled, not bit-exact)",
        "copy hwkit/pi-setup/10-hifi-bridge-rates.conf to /etc/pipewire/pipewire.conf.d/")
    wpver = runner(["wireplumber", "--version"]) or ""
    has_logind = bool(which("loginctl")) and bool(runner(["loginctl", "--no-legend", "list-sessions"]) is not None)
    wp = _cfg_text(globber("/etc/wireplumber/**/*", ) + globber(f"{home}/.config/wireplumber/**/*"), reader)
    if has_logind:
        add("wireplumber:logind", "PASS", "systemd-logind present")
    else:
        ok = "with-logind\"] = false" in wp or "seat-monitoring = disabled" in wp
        add("wireplumber:logind", "PASS" if ok else "FAIL", f"no logind; {wpver.splitlines()[-1] if wpver else '?'}",
            "copy hwkit/pi-setup/wireplumber-no-logind.* (0.4: Lua, 0.5: conf) - WirePlumber 0.4 quits otherwise")
    nodes = runner(["pactl", "list", "short", "sources"]) or ""
    add("pipewire:running", "PASS" if nodes and "error" not in nodes.lower() else "FAIL", "pactl answers" if nodes else "no answer",
        "start pipewire, wireplumber and pipewire-pulse for the test user")
    st = os.statvfs(home)
    free_gb = st.f_bavail * st.f_frsize / 2**30
    add("disk:free", "PASS" if free_gb > 2 else "WARN", f"{free_gb:.1f} GB free", "captures need ~10 MB/min per recording")
    return out


def report(results: list[dict]) -> tuple[str, int]:
    lines = [f"{r['status']:4}  {r['check']:24} {r['detail']}" + (f"\n      fix: {r['fix']}" if r["status"] != "PASS" and r["fix"] else "")
             for r in results]
    fails = sum(1 for r in results if r["status"] == "FAIL")
    lines.append(f"\n{fails} FAIL, {sum(1 for r in results if r['status'] == 'WARN')} WARN")
    return "\n".join(lines), (1 if fails else 0)
