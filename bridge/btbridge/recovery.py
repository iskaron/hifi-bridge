"""Local recovery (no network path): reset the owner password, reset the network settings or factory-reset the configuration.

Authority = local access: a console on the device, or physical access to the SD card's boot partition (P8, docs/PHASE2B_PLAN.md).
Never reachable from the WebUI or MQTT.

    python3 -m btbridge.recovery --data /var/lib/hifi-bridge reset-owner-password
    python3 -m btbridge.recovery --data /var/lib/hifi-bridge factory-reset --yes
    python3 -m btbridge.recovery --data /var/lib/hifi-bridge network-reset          # broken static address / wrong Wi-Fi (docs/NETWORK.md)
    python3 -m btbridge.recovery --data /var/lib/hifi-bridge boot-request --boot /boot     # processes /boot/hifi-recovery once

Factory reset removes ONLY the bridge's own known files in the data directory (settings, secrets, certificates, journals, faults, setup code,
network configuration); it never deletes the directory itself or anything it does not know.

A boot request runs **exactly once** and is **power-cut safe** (review decision on P8): a journal in the data directory (which the factory
reset deliberately keeps) records "running" before the action and "done" after it; the request file is removed only after "done". A power
cut before "done" repeats the (idempotent) action on the next boot; a power cut after "done" only removes the file. The journal is cleared
once the request file is gone, so the same request can be placed again later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

OWN_FILES = ("settings.json", "secrets.json", "session.json", "faults.jsonl", "setup-code.txt", "network.json", "provision-journal.json",
             "faults-respawn.log",
             "network.pending.json")
OWN_CERT_FILES = ("ca.key", "ca.crt", "server.key", "server.crt", "custom.key", "custom.crt", "state.json")
REQUEST_FILE, STATUS_FILE, JOURNAL = "hifi-recovery", "hifi-status.txt", "recovery-journal.json"
ACTIONS = ("reset-owner-password", "factory-reset", "network-reset")


def network_reset(data_dir: str) -> str:
    """Forget the client Wi-Fi and every static address (Ethernet and Wi-Fi back to DHCP/automatic IPv6); keep the regulatory country
    and the owner-known hotspot credentials, so the setup hotspot can open (docs/NETWORK.md). Idempotent. Returns a status text that
    never contains a password."""
    from .net import netconfig
    path = os.path.join(data_dir, netconfig.FILE_NAME)
    try:
        old, note = netconfig.load(path), ""
    except netconfig.NetConfigError:
        old, note = netconfig.defaults(), " The old network file was unreadable; no setup hotspot credentials could be kept."
    new = netconfig.reset_network(old)
    netconfig.write_private(path, new)
    try:
        os.unlink(os.path.join(data_dir, netconfig.PENDING_NAME))
    except FileNotFoundError:
        pass
    hs = new["hotspot"]
    where = (f"Without Ethernet the setup hotspot '{hs['ssid']}' opens after about 15 s: join it with its password and open "
             f"http://{netconfig.HOTSPOT_ADDRESS}/" if hs else "No setup hotspot is configured: connect the device by Ethernet (DHCP)")
    return f"network reset: Wi-Fi client and static addresses removed, DHCP everywhere. {where}.{note}"


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


def _fsync_dir(d: str) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(path: str, text: str, mode: int = 0o644) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


def _write_status(boot_dir: str, text: str) -> None:
    _write_atomic(os.path.join(boot_dir, STATUS_FILE), text)


def _journal_path(data_dir: str) -> str:
    return os.path.join(data_dir, JOURNAL)


def _read_journal(data_dir: str) -> dict:
    try:
        with open(_journal_path(data_dir)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _set_journal(data_dir: str, j: dict | None) -> None:
    if j is None:
        try:
            os.unlink(_journal_path(data_dir))
        except OSError:
            pass
        return
    _write_atomic(_journal_path(data_dir), json.dumps(j), 0o600)


def _remove_request(req: str) -> None:
    os.unlink(req)
    _fsync_dir(os.path.dirname(req) or ".")


def boot_request(data_dir: str, boot_dir: str, hooks: dict | None = None) -> str:
    """Process a recovery request file on the boot partition exactly once (see module doc). Returns
    none | reset-owner-password | factory-reset | already-done | unknown. ``hooks`` lets tests inject a crash at a named step."""
    hooks = hooks or {}
    req = os.path.join(boot_dir, REQUEST_FILE)
    if not os.path.isfile(req):
        if _read_journal(data_dir):
            _set_journal(data_dir, None)                   # the request is gone: the same request may be placed again later
        return "none"
    with open(req, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    action = raw.decode("utf-8", "replace").strip().lower()
    j = _read_journal(data_dir)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if j.get("state") == "done" and j.get("digest") == digest:
        _remove_request(req)                               # power cut after the action: finish without repeating it
        _set_journal(data_dir, None)
        return "already-done"
    if action not in ACTIONS:
        _write_status(boot_dir, f"{stamp} unknown recovery request ignored (use 'reset-owner-password', 'network-reset' or 'factory-reset').\n")
        _remove_request(req)
        return "unknown"
    os.makedirs(data_dir, exist_ok=True)
    _set_journal(data_dir, {"state": "running", "digest": digest, "action": action, "at": stamp})
    hooks.get("after_running", lambda: None)()
    if action == "reset-owner-password":
        code = reset_owner_password(data_dir)
        text = (f"{stamp} owner password reset. New one-time setup code: {code}\n"
                f"   It becomes invalid as soon as a new owner password is set.\n")
    elif action == "network-reset":
        text = f"{stamp} {network_reset(data_dir)}\n"
    else:
        removed = factory_reset(data_dir)
        text = f"{stamp} factory reset: {len(removed)} file(s) removed. A new setup code is created on the next start.\n"
    hooks.get("after_action", lambda: None)()
    _write_status(boot_dir, text)
    _set_journal(data_dir, {"state": "done", "digest": digest, "action": action, "at": stamp})
    hooks.get("after_done", lambda: None)()
    _remove_request(req)
    _set_journal(data_dir, None)
    return action


def write_boot_status(boot_dir: str, lines: list[str]) -> None:
    """Device status for the owner on the boot partition (name, CA fingerprint; a one-time setup code only while needed)."""
    _write_status(boot_dir, "".join(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n" for line in lines))


def _drop_to(uid: int, gid: int) -> None:
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def run_as_data_owner(data_dir: str) -> str | None:
    """Act as the owner of the data directory. Run as root on the console, the CLI used to leave root-owned files that the
    service (user hifi-bridge) could not read: the next start moved secrets.json aside and the printed setup code was useless
    (review M8). Root drops to the owner; any other user is refused. Returns an error text, or None."""
    st = os.stat(data_dir)
    euid = os.geteuid()
    if euid == st.st_uid:
        return None
    if euid == 0:
        _drop_to(st.st_uid, st.st_gid)
        return None
    return (f"refusing: {data_dir} belongs to uid {st.st_uid}, this process runs as uid {euid}; "
            "run as root (the tool switches to the data owner) or as the data owner")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.recovery", description="local recovery (console / SD card only)")
    ap.add_argument("--data", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset-owner-password")
    fr = sub.add_parser("factory-reset"); fr.add_argument("--yes", action="store_true", help="confirm")
    br = sub.add_parser("boot-request"); br.add_argument("--boot", required=True)
    sub.add_parser("network-reset")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.data):
        print(f"data directory not found: {a.data}", file=sys.stderr)
        return 2
    err = run_as_data_owner(a.data)
    if err:
        print(err, file=sys.stderr)
        return 4
    if a.cmd == "reset-owner-password":
        print(f"owner password removed; new setup code: {reset_owner_password(a.data)}")
    elif a.cmd == "factory-reset":
        if not a.yes:
            print("refusing without --yes (this removes all settings, secrets and certificates)", file=sys.stderr)
            return 3
        removed = factory_reset(a.data)
        print(f"factory reset: removed {len(removed)} file(s): {', '.join(removed) or '-'}")
    elif a.cmd == "network-reset":
        print(network_reset(a.data))
    else:
        print(f"recovery request: {boot_request(a.data, a.boot)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
