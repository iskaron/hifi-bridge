"""Package smoke test: the FROZEN helper writes a signed-format image to a SIMULATED card (regular file) over its real stdio protocol, and
the card holds the image and the provisioning document afterwards. Usage: smoke_helper.py <packaged hififlash-helper> <scratch dir>.
(Inside the build container only; the frozen helper is started as an ordinary child process with --simulate.)"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.environ.get("HIFI_REPO", "/work")                # the macOS build (flashtool/packaging/macos/build.sh) runs outside Docker
sys.path[:0] = [os.path.join(REPO, "bridge"), os.path.join(REPO, "flashtool")]

from btbridge import provisioning as P                 # noqa: E402
from hififlash import manifest as M                     # noqa: E402
from hififlash import provision as FP                   # noqa: E402
from hififlash.helper import protocol as PR             # noqa: E402
from hififlash.platforms import simulated as SIM        # noqa: E402


def main(helper: str, d: str) -> int:
    size, slot = 6 << 20, 2 << 20
    data = bytearray(os.urandom(size))
    data[slot:slot + P.SLOT_SIZE] = P.placeholder()
    img = os.path.join(d, "smoke.img")
    with open(img, "wb") as fh:
        fh.write(data)
    m = {"format": M.FORMAT, "compatible": "hifi-bridge-rpi3", "version": "smoke", "image": "smoke.img", "size": size,
         "sha256": hashlib.sha256(data).hexdigest(), "slot": {"file": P.FILE_NAME, "offset": slot, "size": P.SLOT_SIZE}}
    cards = os.path.join(d, "cards")
    card = SIM.create(cards, "smoke", 2 * 1000 ** 3, serial="SMOKE")
    disk = SIM.SimulatedAdapter(cards).list_disks()[0]
    prov = P.render(FP.new_document("DE")[0])
    p = subprocess.Popen([helper, "--stdio", "--simulate", cards], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert json.loads(p.stdout.readline())["event"] == "ready"
    p.stdin.write(PR.encode(PR.write_request(disk, img, m, prov)))
    p.stdin.flush()
    last = None
    for line in p.stdout:
        last = json.loads(line)
        if last["event"] in ("done", "error"):
            break
    p.stdin.close()
    rc = p.wait(60)
    with open(card, "rb") as fh:
        written = fh.read(size)
    ok = last and last["event"] == "done" and rc == 0 and written[slot:slot + P.SLOT_SIZE] == prov and written[:slot] == bytes(data[:slot])
    print(f"helper smoke: {last} rc={rc} -> {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
