"""Boot-partition step, run once per boot BEFORE the bridge service (systemd unit hifi-bridge-boot.service; ``appliance --boot-dir`` on a
test Pi). Order matters:

1. recovery request (P8) - exactly once, power-cut safe (``btbridge.recovery``);
2. provisioning file (P6) - validated, applied idempotently, scrubbed (``btbridge.provisioning``);
3. local CA (P7) - created on the device if missing; the private key never leaves the data partition;
4. ``hifi-status.txt`` - non-secret status for the owner: name, CA fingerprint, results of 1 and 2. A one-time setup code appears ONLY
   when the device generated it itself (no provisioning, or after a password reset) and no owner password exists yet.

    python3 -m btbridge.bootfiles --data /var/lib/hifi-bridge --boot /boot
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

from . import provisioning, recovery


def run(data_dir: str, boot_dir: str, iterations: int | None = None, log=print) -> dict:
    from .config import ConfigStore
    from .net.certs import CertManager
    from .webui.auth import Auth
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    out = {"recovery": recovery.boot_request(data_dir, boot_dir)}
    out["provisioning"] = provisioning.consume(boot_dir, data_dir, iterations, log=log)
    store = ConfigStore(data_dir)
    iid = store.get("instance_id")
    if not iid:
        iid = secrets.token_hex(4)
        store.update({"instance_id": iid})
    auth = Auth(store, log=log, **({"iterations": iterations} if iterations else {}))   # creates a setup code if none exists
    ca = CertManager(data_dir, iid, log=log).ensure_ca()
    out["ca_sha256"] = ca["sha256"]
    name = f"{store.get('local_hostname')}.local"
    lines = [f"Hi-Fi Bridge '{store.get('device_name')}' - http://{name}/ (or the IP address shown by your router)",
             f"Device CA fingerprint (SHA-256): {ca['sha256']}"]
    if out["recovery"] in ("reset-owner-password", "factory-reset"):
        lines.append(f"Recovery request processed: {out['recovery']}")
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
    print(f"bootfiles: recovery={r['recovery']} provisioning={r['provisioning']['result']} ca={r['ca_sha256'][:23]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
