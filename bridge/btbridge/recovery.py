"""Local recovery (no network path): reset the owner password or factory-reset the configuration.

Authority = local access: a console on the device, or physical access to the SD card's boot partition (phase 2b, proposal P8 in
docs/PHASE2B_PLAN.md). Never reachable from the WebUI or MQTT.

    python3 -m btbridge.recovery --data /var/lib/hifi-bridge reset-owner-password
    python3 -m btbridge.recovery --data /var/lib/hifi-bridge factory-reset --yes
    python3 -m btbridge.recovery --data /var/lib/hifi-bridge boot-request --boot /boot     # processes /boot/hifi-recovery once

Factory reset removes ONLY the bridge's own known files in the data directory (settings, secrets, certificates, journal, faults, setup code);
it never deletes the directory itself or anything it does not know.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

OWN_FILES = ("settings.json", "secrets.json", "session.json", "faults.jsonl", "setup-code.txt")
OWN_CERT_FILES = ("ca.key", "ca.crt", "server.key", "server.crt", "custom.key", "custom.crt", "state.json")
REQUEST_FILE, STATUS_FILE = "hifi-recovery", "hifi-status.txt"


def reset_owner_password(data_dir: str) -> str:
    from .config import ConfigStore
    from .webui.auth import Auth
    store = ConfigStore(data_dir)
    return Auth(store).reset_with_new_setup_code()


def factory_reset(data_dir: str) -> list[str]:
    removed = []
    for name in OWN_FILES:
        p = os.path.join(data_dir, name)
        if os.path.isfile(p):
            os.unlink(p)
            removed.append(name)
    certs = os.path.join(data_dir, "certs")
    for name in OWN_CERT_FILES:
        p = os.path.join(certs, name)
        if os.path.isfile(p):
            os.unlink(p)
            removed.append(f"certs/{name}")
    if os.path.isdir(certs) and not os.listdir(certs):
        os.rmdir(certs)
    for name in os.listdir(data_dir) if os.path.isdir(data_dir) else []:
        if name.startswith(("settings.json.corrupt-", "secrets.json.corrupt-")):
            os.unlink(os.path.join(data_dir, name))
            removed.append(name)
    return removed


def _write_status(boot_dir: str, text: str) -> None:
    tmp = os.path.join(boot_dir, STATUS_FILE + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, os.path.join(boot_dir, STATUS_FILE))


def boot_request(data_dir: str, boot_dir: str) -> str:
    """Process a recovery request file placed on the boot partition (physical access). The file is removed in every case, so a request
    runs at most once; the result (incl. a new setup code) is written to hifi-status.txt next to it."""
    req = os.path.join(boot_dir, REQUEST_FILE)
    if not os.path.isfile(req):
        return "none"
    action = open(req).read().strip().lower()
    os.unlink(req)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if action == "reset-owner-password":
        code = reset_owner_password(data_dir)
        _write_status(boot_dir, f"{stamp} owner password reset. New setup code: {code}\n")
        return "reset-owner-password"
    if action == "factory-reset":
        removed = factory_reset(data_dir)
        _write_status(boot_dir, f"{stamp} factory reset: {len(removed)} file(s) removed. A new setup code is created on the next start.\n")
        return "factory-reset"
    _write_status(boot_dir, f"{stamp} unknown recovery request ignored (use 'reset-owner-password' or 'factory-reset').\n")
    return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.recovery", description="local recovery (console / SD card only)")
    ap.add_argument("--data", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset-owner-password")
    fr = sub.add_parser("factory-reset"); fr.add_argument("--yes", action="store_true", help="confirm")
    br = sub.add_parser("boot-request"); br.add_argument("--boot", required=True)
    a = ap.parse_args(argv)
    if not os.path.isdir(a.data):
        print(f"data directory not found: {a.data}", file=sys.stderr)
        return 2
    if a.cmd == "reset-owner-password":
        print(f"owner password removed; new setup code: {reset_owner_password(a.data)}")
    elif a.cmd == "factory-reset":
        if not a.yes:
            print("refusing without --yes (this removes all settings, secrets and certificates)", file=sys.stderr)
            return 3
        removed = factory_reset(a.data)
        print(f"factory reset: removed {len(removed)} file(s): {', '.join(removed) or '-'}")
    else:
        print(f"recovery request: {boot_request(a.data, a.boot)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
