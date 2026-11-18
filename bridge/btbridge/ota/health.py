"""Post-boot health check (``hifi-bridge-health.service``, runs once per boot after ``hifi-bridge.service``; docs/OTA.md section 4).

    python3 -m btbridge.ota.health --data /var/lib/hifi-bridge [--healthz http://127.0.0.1:80/healthz] [--timeout 300]

Checks (all must pass within the timeout): the data partition is mounted, the bridge's state directory is writable, the bridge reports
itself not ``failed`` on ``/healthz`` (which also proves the WebUI answers), and RAUC is reachable.

* Pass  -> ``rauc mark-good booted`` on EVERY boot (RAUC's U-Boot integration decrements the attempt counter on each boot; marking good
          resets it), record this slot's version, and finish a pending update or slot switch ("updated" / "switched").
* Fail on the FIRST boot of a new slot (phase "confirming") -> ``mark-bad booted`` and exit 1; the unit's ``FailureAction=reboot``
          restarts the device and U-Boot boots the previous slot (automatic rollback; the bridge then reports "rolled_back").
* Fail on an established slot -> recorded and shown in the WebUI, exit 0, no reboot (an external problem would otherwise cause a
          reboot loop). The slot is not marked good, so after three such boots U-Boot's boot-count falls back to the other slot.

Runs as the unprivileged ``hifi-bridge`` user; the D-Bus policy allows it to call RAUC.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Callable

from .. import __version__
from .manager import StateFile, atomic_json, boot_id, confirm, load_json, reconcile
from .rauc import RaucClient, RaucError, RaucUnavailable


def check_once(data_dir: str, data_mount: str, healthz: str, rauc, timeout: float = 5.0) -> dict:
    res = {"data_mounted": os.path.ismount(data_mount) if data_mount else True}
    probe = os.path.join(data_dir, "ota", ".health-probe")
    try:
        os.makedirs(os.path.dirname(probe), mode=0o700, exist_ok=True)
        with open(probe, "w") as fh:
            fh.write("ok")
            fh.flush()
            os.fsync(fh.fileno())
        os.unlink(probe)
        res["data_writable"] = True
    except OSError:
        res["data_writable"] = False
    try:
        with urllib.request.urlopen(healthz, timeout=timeout) as r:
            body = json.loads(r.read(65536).decode("utf-8"))
        res["bridge"] = body.get("status") in ("ok", "degraded")
    except Exception:                                          # refused, timeout, 503 (failed), garbage
        res["bridge"] = False
    try:
        rauc.properties()
        res["rauc"] = True
    except (RaucUnavailable, RaucError):
        res["rauc"] = False
    return res


def run(data_dir: str, rauc, healthz: str = "http://127.0.0.1:80/healthz", data_mount: str = "/data", timeout: float = 300.0,
        interval: float = 3.0, version: str = __version__, current_boot: Callable[[], str] = boot_id,
        log: Callable[[str], None] = print, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int:
    ota_dir = os.path.join(data_dir, "ota")
    t0 = clock()
    while True:
        result = check_once(data_dir, data_mount, healthz, rauc)
        if all(result.values()) or clock() - t0 >= timeout:
            break
        sleep(interval)
    ok = all(result.values())
    try:
        props = rauc.properties()
        booted = props.get("BootSlot")
    except (RaucUnavailable, RaucError):
        props, booted = {}, None
    state = StateFile(ota_dir)
    with state.edit() as st:
        this_boot = (st.get("install") or {}).get("boot_id") == current_boot()
        if not (st["phase"] == "installing" and this_boot):    # an installation of THIS boot belongs to the bridge's own monitor
            reconcile(st, booted, current_boot(), False, clock())
        trial = st["phase"] == "confirming"
    record = {"at": clock(), "boot_id": current_boot(), "slot": booted, "version": version, "ok": ok, "checks": result,
              "trial": trial, "marked": None}
    code = 0
    if ok:
        try:
            name, _ = rauc.mark("good", "booted")
            record["marked"] = f"good:{name}"
        except (RaucUnavailable, RaucError) as e:
            record["marked"] = f"error:{e}"
        known = load_json(os.path.join(ota_dir, "slots.json"), {}) or {}
        known[booted or "?"] = {"version": version, "at": clock()}
        atomic_json(os.path.join(ota_dir, "slots.json"), known)
        with state.edit() as st:
            confirm(st, booted, version, clock())
        log(f"health: ok on slot {booted} ({version}); marked good")
    else:
        failed = ", ".join(k for k, v in result.items() if not v)
        if trial:
            with state.edit() as st:
                (st.get("install") or {})["failure"] = "health_check_failed"
                st["phase"] = "rebooting"
            try:
                name, _ = rauc.mark("bad", "booted")
                record["marked"] = f"bad:{name}"
            except (RaucUnavailable, RaucError) as e:          # still reboot: the boot-count then falls back after the attempts
                record["marked"] = f"error:{e}"
            code = 1
            log(f"health: FAILED on the first start of slot {booted} ({failed}); marked bad - rebooting into the previous slot")
        else:
            log(f"health: FAILED on slot {booted} ({failed}); slot not confirmed (no reboot)")
    atomic_json(os.path.join(ota_dir, "health.json"), record)
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.ota.health", description="post-boot health check: mark the RAUC slot good or roll back")
    ap.add_argument("--data", required=True)
    ap.add_argument("--healthz", default="http://127.0.0.1:80/healthz")
    ap.add_argument("--data-mount", default="/data")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--dbus", default="SYSTEM")
    a = ap.parse_args(argv)
    rauc = RaucClient(bus=a.dbus)
    try:
        return run(a.data, rauc, a.healthz, a.data_mount, a.timeout, a.interval, log=lambda m: print(m, flush=True))
    finally:
        rauc.close()


if __name__ == "__main__":
    sys.exit(main())
