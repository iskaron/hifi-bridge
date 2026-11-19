"""A complete, SIMULATED world for the GUI tests and the screenshots: a throw-away "official" key (generated per run, never committed),
signed images on a loopback release server, simulated cards (regular files) incl. ones the policy must refuse, and scripted dialogs.
The helper runs for real (protocol + server + simulated adapter) in process. No process is started, no device is touched."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "flashtool"))

import flashfixtures as F                                    # noqa: E402

from hififlash.gui.services import Services, Settings        # noqa: E402
from hififlash.helper import client as CL                    # noqa: E402
from hififlash.keystore import KeyStore                      # noqa: E402
from hififlash.platforms import simulated as SIM             # noqa: E402

GB = 1000 ** 3


class FakeDialogs:
    def __init__(self):
        self.open_paths: list = []
        self.save_paths: list = []
        self.confirms: list = []
        self.printed: list = []
        self.urls: list = []
        self.asked: list = []

    def open_file(self, title, filters):
        return self.open_paths.pop(0) if self.open_paths else None

    def save_file(self, title, name, filters):
        self.asked.append(name)
        return self.save_paths.pop(0) if self.save_paths else None

    def confirm(self, title, text):
        self.asked.append(text)
        return self.confirms.pop(0) if self.confirms else True

    def print_document(self, document):
        self.printed.append(document.toPlainText())
        return True

    def open_url(self, url):
        self.urls.append(url)


class Env:
    def __init__(self, tmp: str):
        self.tmp = tmp
        self.official_key, pub = F.keypair()
        off = os.path.join(tmp, "official")
        os.makedirs(off)
        with open(os.path.join(off, "hifi-bridge-test.pem"), "wb") as fh:
            fh.write(pub)
        self.keys = KeyStore(os.path.join(tmp, "cfg", "publishers"), official_dir=off)
        rel = os.path.join(tmp, "release")
        os.makedirs(rel)
        self.rpi3 = F.image(rel, "hifi-bridge-rpi3", "0.5.0", compress=True, key=self.official_key)
        self.rpi4 = F.image(rel, "hifi-bridge-rpi4", "0.5.0", key=self.official_key)
        self.server = F.ReleaseServer()
        self.server.publish("v0.5.0", [self.rpi3, self.rpi4])
        self.cards = os.path.join(tmp, "cards")
        self.sd = SIM.create(self.cards, "sdcard", 32 * GB, name="sdcard", model="SD Card Reader", serial="SD-0001", mounts=["/media/owner/BOOT"])
        SIM.create(self.cards, "system", 512 * GB, name="nvme0n1", model="Internal SSD", serial="SYS", transport="nvme", removable=False,
                   system=True, mounts=["/"])
        SIM.create(self.cards, "small", 512 * 1000 ** 2, name="mmcblk1", model="Old SD card", serial="SMALL")
        SIM.create(self.cards, "large", 2000 * GB, name="sdc", model="USB Backup Disk", serial="BIG", transport="usb")
        SIM.create(self.cards, "locked", 16 * GB, name="sdd", model="Locked SD card", serial="RO", readonly=True)
        self.dialogs = FakeDialogs()
        self.adapter = SIM.SimulatedAdapter(self.cards)
        settings = Settings(os.path.join(tmp, "cfg", "settings.json"))
        settings.values["source"] = self.server.base + "/releases"
        self.services = Services(settings=settings, keys=self.keys, cache_dir=os.path.join(tmp, "cache"), list_disks=self.adapter.list_disks,
                                 channel_factory=lambda: CL.inprocess(self.adapter, "simulated"), dialogs=self.dialogs, simulated=True)

    def set_card_meta(self, **meta):
        import json
        p = self.sd + ".json"
        with open(p) as fh:
            m = json.load(fh)
        m.update(meta)
        with open(p, "w") as fh:
            json.dump(m, fh)

    def close(self):
        self.server.close()
