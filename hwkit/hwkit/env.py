"""Record the software/hardware environment of the device under test (run ON THE PI; read-only commands only)."""
from __future__ import annotations

import json
import platform
import subprocess
import time

COMMANDS = {
    "kernel": ["uname", "-srmv"],
    "bluetoothd": ["bluetoothd", "-v"],
    "bluetoothctl": ["bluetoothctl", "--version"],
    "pipewire": ["pipewire", "--version"],
    "wireplumber": ["wireplumber", "--version"],
    "pactl_info": ["pactl", "info"],
    "firmware": ["vcgencmd", "version"],
}
FILES = {"board_model": "/proc/device-tree/model", "os_release": "/etc/os-release"}


def run(argv, timeout=5):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip()[:2000] or f"(exit {r.returncode})"
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {type(e).__name__}"


def read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read().replace("\x00", "").strip()[:2000]
    except OSError:
        return None


def collect(runner=run, reader=read) -> dict:
    out = {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "python": platform.python_version()}
    for k, argv in COMMANDS.items():
        out[k] = runner(argv)
    for k, p in FILES.items():
        out[k] = reader(p)
    try:
        import jeepney
        out["jeepney"] = jeepney.__version__
    except ImportError:
        out["jeepney"] = None
    out["missing"] = sorted(k for k in list(COMMANDS) + list(FILES) if out.get(k) is None)
    return out


def main(path: str) -> None:
    with open(path, "w") as fh:
        json.dump(collect(), fh, indent=1)
