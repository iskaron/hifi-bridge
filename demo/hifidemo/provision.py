"""The flash tool's part of the demo (runs once, before the bridge's first start): what an owner does on their computer when writing the
card. It uses the REAL flash-tool core (``hififlash provision``): a per-device setup code and trust key, delivered on the boot partition.

  * /boot-demo/hifi-provision.json  - the provisioning file (the bridge applies and then scrubs it)
  * /owner/owner-sheet.txt           - what the owner keeps: the setup code (shown by ``make demo-status``)
  * /owner/trust.key                 - the owner's key for the CA trust proof (``make demo-trust``)
"""
from __future__ import annotations

import os
import subprocess
import sys

BOOT = os.environ.get("DEMO_BOOT", "/boot-demo")
OWNER = os.environ.get("DEMO_OWNER", "/owner")


def main() -> int:
    sheet = os.path.join(OWNER, "owner-sheet.txt")
    if os.path.exists(sheet):
        print("[flashtool] already provisioned (owner sheet exists); nothing to do", flush=True)
        return 0
    r = subprocess.run([sys.executable, "-m", "hififlash", "provision", "--country", "DE", "--no-hotspot", "--lang", "de",
                        "--out", os.path.join(BOOT, "hifi-provision.json"), "--key-out", os.path.join(OWNER, "trust.key")],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print("[flashtool] provisioning FAILED: " + r.stderr.strip(), flush=True)
        return 1
    fd = os.open(sheet + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(r.stdout)
    os.replace(sheet + ".tmp", sheet)
    print("[flashtool] card provisioned: setup code + trust key created (see make demo-status)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
