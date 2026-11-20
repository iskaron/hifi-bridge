"""Boot-partition step, run once per boot BEFORE the bridge service (systemd unit hifi-bridge-boot.service; ``appliance --boot-dir`` on a
test Pi). Order matters:

0. boot cleanup (DECISIONS #121) - leftovers of writes a power cut interrupted and update staging files no state needs
   (``btbridge.storage``);
1. recovery request (P8) - exactly once, power-cut safe (``btbridge.recovery``);
2. provisioning file (P6) - validated, applied idempotently, scrubbed (``btbridge.provisioning``);
3. an owner-requested settings snapshot restore (``ota.manager.prepare_data``, docs/OTA.md section 6) - here, because the network agent
   ``hifi-netd`` (ordered after this unit) reads ``network.json`` and the host name only at its start (review N-4);
4. local CA (P7) - created on the device if missing; the private key never leaves the data partition;
5. ``hifi-status.txt`` - non-secret status for the owner: name, CA fingerprint, results of 1 and 2. A one-time setup code appears ONLY
   when the device generated it itself (no provisioning, or after a password reset) and no owner password exists yet. Rewritten only
   when its content changed (no FAT write on an ordinary boot).

    python3 -m btbridge.bootfiles --data /var/lib/hifi-bridge --boot /boot
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

from . import provisioning, recovery, storage


def network_lines(data_dir: str) -> list[str]:
    """Where to find the device without a working network (no password ever: the owner knows the hotspot password)."""
    from .net import netconfig
    try:
        cfg = netconfig.load(os.path.join(data_dir, netconfig.FILE_NAME))
    except netconfig.NetConfigError:
        return ["Network settings unreadable: Ethernet with DHCP only (put a 'hifi-recovery' file containing 'network-reset' here)"]
    out = []
    if cfg["wifi"]:
        out.append(f"Wi-Fi network: '{cfg['wifi']['ssid']}'")
    if cfg["hotspot"]:
        out.append(f"Setup hotspot (opens when no network works): '{cfg['hotspot']['ssid']}' -> http://{netconfig.HOTSPOT_ADDRESS}/")
    return out


def run(data_dir: str, boot_dir: str, iterations: int | None = None, log=print) -> dict:
    from .config import ConfigStore
    from .net.certs import CertManager
    from .webui.auth import Auth
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    cleanup = storage.boot_cleanup(data_dir, log=log)                # power-cut leftovers, update staging (DECISIONS #121); never raises
    out = {"recovery": recovery.boot_request(data_dir, boot_dir), "cleanup": cleanup}
    out["provisioning"] = provisioning.consume(boot_dir, data_dir, iterations, log=log)
    from .ota.manager import prepare_data
    out["restore"] = prepare_data(data_dir, log=log)                  # never raises
    store = ConfigStore(data_dir)
    iid = store.get("instance_id")
    if not iid:
        iid = secrets.token_hex(4)
        store.update({"instance_id": iid})
    auth = Auth(store, log=log, **({"iterations": iterations} if iterations else {}))   # creates a setup code if none exists
    from .net.certs import CertError
    try:
        ca = CertManager(data_dir, iid, log=log).ensure_ca()          # heals a CA torn by a power cut (review H6)
    except (CertError, OSError) as e:                                 # the status file (setup code!) must still be written
        log(f"bootfiles: local CA not available: {e}")
        ca = None
    out["ca_sha256"] = ca["sha256"] if ca else None
    name = f"{store.get('local_hostname')}.local"
    lines = [f"Hi-Fi Bridge '{store.get('device_name')}' - http://{name}/ (or the IP address shown by your router)",
             f"Device CA fingerprint (SHA-256): {ca['sha256']}" if ca else
             "Device CA: not available yet (HTTPS starts once it could be created; see the diagnostics page)"]
    if out["recovery"] in ("reset-owner-password", "factory-reset", "network-reset"):
        lines.append(f"Recovery request processed: {out['recovery']}")
    if out["restore"] in ("restored", "failed"):
        lines.append("Settings snapshot restored as requested" if out["restore"] == "restored" else
                     "Settings snapshot restore FAILED - the current settings were kept (see the update page)")
    lines += network_lines(data_dir)
    prov = out["provisioning"]
    if prov["result"] == "applied":
        lines += [f"Provisioning applied: {n}" for n in prov["notes"]]
    elif prov["result"] == "invalid":
        lines.append("Provisioning file REJECTED and erased (nothing applied): " + ", ".join(prov["problems"]))
    if not auth.has_password() and os.path.isfile(auth.code_file):
        code = open(auth.code_file).read().strip()
        lines.append(f"One-time setup code: {code} (invalid as soon as the owner password is set)")
    recovery.write_boot_status(boot_dir, lines)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.bootfiles")
    ap.add_argument("--data", required=True)
    ap.add_argument("--boot", required=True)
    a = ap.parse_args(argv)
    if not os.path.isdir(a.boot):
        print(f"boot directory not found: {a.boot}", file=sys.stderr)
        return 2
    r = run(a.data, a.boot)
    print(f"bootfiles: recovery={r['recovery']} provisioning={r['provisioning']['result']} ca={(r['ca_sha256'] or 'none')[:23]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
