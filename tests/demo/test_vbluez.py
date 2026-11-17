"""Virtual bluetoothd against the PRODUCT's real BluezAdapter (JeepneyBus on a private D-Bus). PipeWire is replaced by a recorder here;
the real PipeWire path is covered by tests/demo/test_demo_stack.py. Nothing in the product is patched or bypassed."""
import json
import threading
import time

import pytest

from btbridge.adapters.bluez.adapter import BluezAdapter
from btbridge.adapters.bluez.jeepney_bus import JeepneyBus
from btkit.fake_bluez import PrivateBus
from hifidemo.vbluez import VirtualBluetoothd

PHONE_A, PHONE_B = "02:AA:00:00:00:01", "02:BB:00:00:00:02"


class Nodes:
    """Records what would happen in PipeWire."""
    def __init__(self):
        self.loaded, self.pcm, self.history = set(), {}, []

    def load(self, mac):
        self.loaded.add(mac); self.history.append(("load", mac))

    def unload(self, mac):
        self.loaded.discard(mac); self.history.append(("unload", mac))

    def write(self, mac, data):
        self.pcm[mac] = self.pcm.get(mac, b"") + data
        return len(data)


class Product:
    """The bridge's callbacks as the service would see them (accept the first source)."""
    def __init__(self):
        self.events, self.accept = [], True

    def on_connect(self, dev):
        self.events.append(("connect", dev)); return self.accept

    def on_disconnect(self, dev): self.events.append(("disconnect", dev))
    def on_transport(self, s): self.events.append(("transport", s))
    def on_avrcp(self, s): self.events.append(("avrcp", s))
    def on_volume(self, v): self.events.append(("volume", v))
    def on_metadata(self, t, a, b): self.events.append(("metadata", t, a, b))
    def on_pcm(self, b): pass


class Pcm:
    def __init__(self):
        self.running = None

    def start(self, addr, cb): self.running = addr
    def stop(self): self.running = None


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def world(tmp_path):
    bus = PrivateBus()
    nodes = Nodes()
    vbt = VirtualBluetoothd(bus.address, str(tmp_path / "devices.json"), nodes)
    vbt.start()
    product, pcm = Product(), Pcm()
    adapter = BluezAdapter(JeepneyBus(bus=bus.address), pcm)
    adapter.attach(product)
    adapter.start()
    yield vbt, adapter, product, nodes, pcm, tmp_path
    adapter.stop(); vbt.stop(); bus.close()


def test_bridge_is_visible_and_pairable_only_inside_the_pairing_window(world):
    vbt, adapter, product, nodes, pcm, _ = world
    assert vbt.visible() is None                                      # the product starts with the window closed
    assert vbt.pair(PHONE_A, "Anna's phone") == "not_discoverable"
    adapter.set_alias("Wohnzimmer Bridge")
    adapter.set_pairable(True)
    assert wait(lambda: vbt.visible() is not None)
    assert vbt.visible()["name"] == "Wohnzimmer Bridge"
    assert vbt.pair(PHONE_A, "Anna's phone") == "paired"
    assert wait(lambda: vbt.device(PHONE_A)["trusted"])              # the product's agent trusted it
    assert [d["address"] for d in adapter.paired_devices()] == [PHONE_A]
    adapter.set_pairable(False)
    assert wait(lambda: vbt.visible() is None)
    assert vbt.pair(PHONE_B, "Ben's phone") == "not_discoverable"


def test_connect_play_and_avrcp_reach_the_product_unchanged(world):
    vbt, adapter, product, nodes, pcm, _ = world
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna"); adapter.set_pairable(False)
    assert vbt.connect(PHONE_A) == "connected"
    assert PHONE_A in nodes.loaded
    assert wait(lambda: ("connect", PHONE_A) in product.events)
    vbt.set_track(PHONE_A, "Abendlicht", "Demo Ensemble", "Demo", 60000)
    vbt.set_transport(PHONE_A, "pending"); vbt.set_transport(PHONE_A, "active")
    vbt.set_status(PHONE_A, "playing")
    vbt.set_volume(PHONE_A, 90)
    assert wait(lambda: ("avrcp", "playing") in product.events and ("volume", 90) in product.events)
    assert ("transport", "active") in product.events
    assert ("metadata", "Abendlicht", "Demo Ensemble", "Demo") in product.events
    assert pcm.running == PHONE_A                                      # the product's own capture lifecycle started
    assert vbt.write_pcm(PHONE_A, b"\x01\x00" * 4) == 8
    assert nodes.pcm[PHONE_A] == b"\x01\x00" * 4


def test_second_phone_is_refused_by_the_product_and_told_so(world):
    vbt, adapter, product, nodes, pcm, _ = world
    adapter.set_pairable(True)
    vbt.pair(PHONE_A, "Anna"); vbt.pair(PHONE_B, "Ben")
    adapter.set_pairable(False)
    assert vbt.connect(PHONE_A) == "connected"
    assert vbt.connect(PHONE_B) == "refused"                          # one source at a time; the first is never evicted
    assert vbt.device(PHONE_B)["connected"] is False and PHONE_B not in nodes.loaded
    assert any(e["kind"] == "refused" for e in vbt.events(PHONE_B))
    assert vbt.device(PHONE_A)["connected"] is True
    assert vbt.write_pcm(PHONE_B, b"\0" * 8) == 0                     # a disconnected phone's audio goes nowhere


def test_unpaired_or_forgotten_phones_cannot_connect(world):
    vbt, adapter, product, nodes, pcm, _ = world
    assert vbt.connect(PHONE_A) == "not_paired"
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna"); adapter.set_pairable(False)
    assert vbt.connect(PHONE_A) == "connected"
    assert adapter.remove_device(PHONE_A) is True                     # owner removes the phone in the WebUI
    assert wait(lambda: vbt.device(PHONE_A) is None)
    assert PHONE_A not in nodes.loaded
    assert any(e["kind"] == "removed" for e in vbt.events(PHONE_A))
    assert wait(lambda: ("disconnect", PHONE_A) in product.events)
    assert vbt.connect(PHONE_A) == "not_paired"


def test_phone_disconnect_link_loss_and_owner_disconnect(world):
    vbt, adapter, product, nodes, pcm, _ = world
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna"); adapter.set_pairable(False)
    vbt.connect(PHONE_A); vbt.disconnect(PHONE_A)
    assert wait(lambda: ("disconnect", PHONE_A) in product.events) and PHONE_A not in nodes.loaded
    product.events.clear()
    vbt.connect(PHONE_A); vbt.set_transport(PHONE_A, "active"); vbt.drop(PHONE_A)
    assert wait(lambda: ("disconnect", PHONE_A) in product.events)
    assert any(e["kind"] == "link_lost" for e in vbt.events(PHONE_A))
    vbt.connect(PHONE_A)
    adapter.disconnect_device(PHONE_A)                                 # owner presses "Disconnect" in the WebUI
    assert wait(lambda: vbt.device(PHONE_A)["connected"] is False)
    assert any(e["kind"] == "disconnected_by_bridge" for e in vbt.events(PHONE_A))


def test_pairings_survive_a_restart_like_var_lib_bluetooth(world):
    vbt, adapter, product, nodes, pcm, tmp = world
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna"); adapter.set_pairable(False)
    assert wait(lambda: vbt.device(PHONE_A)["trusted"])
    saved = json.loads((tmp / "devices.json").read_text())
    assert saved[PHONE_A]["name"] == "Anna" and saved[PHONE_A]["trusted"] is True
    bus2 = PrivateBus()
    try:
        again = VirtualBluetoothd(bus2.address, str(tmp / "devices.json"), Nodes())
        again.start()
        assert again.device(PHONE_A) == {"address": PHONE_A, "name": "Anna", "paired": True, "trusted": True, "connected": False}
        again.stop()
    finally:
        bus2.close()


def test_events_are_incremental(world):
    vbt, adapter, *_ = world
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna")
    first = vbt.events(PHONE_A)
    assert first and all("seq" in e for e in first)
    assert vbt.events(PHONE_A, since=first[-1]["seq"]) == []


def test_concurrent_phone_actions_do_not_deadlock(world):
    vbt, adapter, *_ = world
    adapter.set_pairable(True); vbt.pair(PHONE_A, "Anna"); vbt.connect(PHONE_A)
    errors = []

    def spam(i):
        try:
            for k in range(20):
                vbt.set_volume(PHONE_A, (i * 20 + k) % 128); vbt.set_status(PHONE_A, "playing" if k % 2 else "paused")
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
    ts = [threading.Thread(target=spam, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join(10) for t in ts]
    assert not errors and not any(t.is_alive() for t in ts)
