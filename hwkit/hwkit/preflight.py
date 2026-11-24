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
    # pw-dump is how the bridge finds the phone's node (G1): required, not optional
    for c in out:
        if c["check"] == "tool:pw-dump" and c["status"] == "WARN":
            c["status"], c["fix"] = "FAIL", "install pipewire-bin (pw-dump: the bridge finds the phone's audio node with it)"
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
    wp = _cfg_text(globber("/etc/wireplumber/**/*", recursive=True) + globber(f"{home}/.config/wireplumber/**/*", recursive=True), reader)
    seat_off = "with-logind\"] = false" in wp or "seat-monitoring = disabled" in wp
    target = (runner(["systemctl", "get-default"]) or "").strip()
    headless = target != "graphical.target"
    # G1 (2026-11-24): logind is present on Pi OS Lite, but WirePlumber's seat monitoring keeps Bluetooth audio off without an ACTIVE
    # LOCAL session - an SSH-only Pi needs the override although `loginctl` exists.
    if seat_off:
        add("wireplumber:seat-monitoring", "PASS", "seat monitoring disabled for Bluetooth (headless)")
    elif headless:
        add("wireplumber:seat-monitoring", "FAIL", f"headless ({target or 'default target unknown'}) and seat monitoring on: "
            "WirePlumber keeps the Bluetooth A2DP endpoints off without an active local session",
            "sudo sh hwkit/pi-setup/setup_test_pi.sh --i-am-on-the-test-pi --headless (installs 80-no-logind.conf)")
    else:
        add("wireplumber:seat-monitoring", "WARN", "desktop target: Bluetooth audio works only while a local session is active",
            "use --headless with setup_test_pi.sh if the Pi is used over SSH only")
    role = re.search(r"bluez5\.media-source-role\s*=\s*\"?input", wp)
    old_wp = bool(re.search(r"libwireplumber 0\.4", wpver))
    add("wireplumber:a2dp-input-role", "PASS" if role and not old_wp else "FAIL",
        "phone audio becomes a recordable Audio/Source" if role and not old_wp else
        ("WirePlumber 0.4: not supported for G1 (use Pi OS trixie)" if old_wp else
         "default role 'playback': the phone plays on the Pi's own output and the bridge records nothing (G1)"),
        "copy hwkit/pi-setup/50-hifi-bridge-a2dp-input.conf to /etc/wireplumber/wireplumber.conf.d/, restart wireplumber, reconnect the phone")
    plugin = globber("/usr/lib/*/spa-0.2/bluez5/libspa-bluez5.so") + globber("/usr/lib/spa-0.2/bluez5/libspa-bluez5.so")
    add("pipewire:bluez5-plugin", "PASS" if plugin else "FAIL", plugin[0] if plugin else "libspa-bluez5.so missing",
        "apt install libspa-0.2-bluetooth (only a Recommends of pipewire: --no-install-recommends skips it)")
    show = runner(["bluetoothctl", "show"]) or ""
    if show.startswith(("(exit", "error:")):
        show = ""
    add("bluez:audio-sink-uuid", "PASS" if "0000110b" in show.lower() else ("WARN" if not show else "FAIL"),
        "adapter offers Audio Sink (A2DP)" if "0000110b" in show.lower() else ("bluetoothctl gave no answer" if not show else
                                                                             "adapter does not offer Audio Sink (0000110b)"),
        "needs libspa-0.2-bluetooth + WirePlumber running for this user with Bluetooth enabled (seat monitoring), then reconnect")
    nodes = runner(["pactl", "list", "short", "sources"]) or ""
    add("pipewire:running", "PASS" if nodes and "error" not in nodes.lower() else "FAIL", "pactl answers" if nodes else "no answer",
        "start pipewire, wireplumber and pipewire-pulse for the test user")
    try:
        from btbridge.adapters.bluez.pcm import choose_capture
        tool, _ = choose_capture(which=which, run=lambda argv, **k: type("R", (), {"stdout": runner(argv) or ""})())
        add("capture:tool", "PASS" if tool != "none" else "FAIL", f"bridge would use: {tool}",
            "install pipewire-bin (pw-cat) or pulseaudio-utils (parec)")
    except ImportError:
        add("capture:tool", "WARN", "bridge package not on PYTHONPATH", "PYTHONPATH=$PWD/bridge:$PWD/hwkit")
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
