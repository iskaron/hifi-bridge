"""INTEGRATION (SIMULATED-BLUEZ): the real ``JeepneyBus`` + ``BluezAdapter`` against a *fake* org.bluez service on a private
D-Bus daemon inside the lab container.  Proves the bridge's D-Bus client code; says nothing about real bluetoothd/phones/Pi."""
import time

import pytest

jeepney = pytest.importorskip("jeepney")
from btkit.fake_bluez import ADAPTER, FakeBluez, PrivateBus, variant  # noqa: E402

from btbridge.adapters.bluez import BluezAdapter  # noqa: E402
from btbridge.adapters.bluez.jeepney_bus import JeepneyBus  # noqa: E402

pytestmark = pytest.mark.dbus


class Rec:
    def __init__(self):
        self.events = []
        self.on_connect = lambda d: (self.events.append(("connect", d)), True)[1]
        self.on_disconnect = lambda d: self.events.append(("disconnect", d))
        self.on_transport = lambda s: self.events.append(("transport", s))
        self.on_avrcp = lambda s: self.events.append(("avrcp", s))
        self.on_volume = lambda v: self.events.append(("volume", v))
        self.on_metadata = lambda t, a, b: self.events.append(("meta", t, a, b))
        self.on_pcm = lambda b: self.events.append(("pcm", len(b)))


class NoPcm:
    def __init__(self): self.log = []
    def start(self, a, cb): self.log.append(("start", a))
    def stop(self): self.log.append(("stop",))


def wait(cond, timeout=5.0, step=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def rig():
    pb = PrivateBus(); fb = FakeBluez(pb.address); fb.start()
    rec, pcm = Rec(), NoPcm()
    bus = JeepneyBus(bus=pb.address)
    ad = BluezAdapter(bus, pcm); ad.attach(rec)
    yield type("R", (), dict(pb=pb, fb=fb, rec=rec, pcm=pcm, ad=ad, bus=bus))
    try:
        ad.stop()
    except Exception:
        pass
    fb.stop(); pb.close()


def props_of(fb, path, iface):
    return {k: v[1] for k, v in fb.objects[path][iface].items()}


def test_start_registers_agent_and_closes_pairing(rig):
    rig.ad.start()
    assert wait(lambda: rig.fb.agent is not None)
    assert rig.fb.agent_capability == "NoInputNoOutput"            # the capability the adapter asked for, over the real bus
    assert props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")["Pairable"] is False
    assert props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")["Discoverable"] is False


def test_pairing_window_sets_adapter_properties_and_agent_policy_over_the_bus(rig):
    rig.ad.start(); assert wait(lambda: rig.fb.agent)
    dev = rig.fb.add_device(1, paired=False)
    assert rig.fb.agent_call("AuthorizeService", dev, "os", (dev, "0000110d-0000-1000-8000-00805f9b34fb")) == ("error", "org.bluez.Error.Rejected")
    rig.ad.set_pairable(True)
    a = props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")
    assert a["Pairable"] is True and a["Discoverable"] is True and a["PairableTimeout"] == 0
    assert rig.fb.agent_call("RequestAuthorization", dev) == ("ok",)
    rig.ad.set_pairable(False)
    assert props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")["Pairable"] is False
    assert rig.fb.agent_call("RequestAuthorization", dev) == ("error", "org.bluez.Error.Rejected")
    assert rig.fb.agent_call("RequestPinCode", dev) == ("error", "org.bluez.Error.Rejected")


def test_connect_transport_avrcp_volume_metadata_flow_over_dbus(rig):
    rig.ad.start()
    dev = rig.fb.add_device(1)
    rig.fb.add_object(f"{dev}/fd0", {"org.bluez.MediaTransport1": {"Device": ("o", dev), "State": "idle", "Volume": ("q", 64)}})
    rig.fb.add_object(f"{dev}/player0", {"org.bluez.MediaPlayer1": {"Device": ("o", dev), "Status": "paused"}})
    rig.fb.set_props(dev, "org.bluez.Device1", Connected=True)
    assert wait(lambda: ("avrcp", "paused") in rig.rec.events)
    assert rig.rec.events[0] == ("connect", "AA:BB:CC:DD:EE:01")
    assert ("transport", "idle") in rig.rec.events and ("volume", 64) in rig.rec.events
    rig.fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="active")
    rig.fb.set_props(f"{dev}/player0", "org.bluez.MediaPlayer1", Status="playing",
                     Track=("a{sv}", {"Title": ("s", "Song"), "Artist": ("s", "Band"), "Album": ("s", "LP")}))
    assert wait(lambda: ("meta", "Song", "Band", "LP") in rig.rec.events)
    assert ("transport", "active") in rig.rec.events and ("avrcp", "playing") in rig.rec.events
    assert ("start", "AA:BB:CC:DD:EE:01") in rig.pcm.log                          # capture starts with the active transport
    rig.fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", Volume=("q", 127))
    assert wait(lambda: ("volume", 127) in rig.rec.events)
    rig.fb.set_props(dev, "org.bluez.Device1", Connected=False)
    assert wait(lambda: ("disconnect", "AA:BB:CC:DD:EE:01") in rig.rec.events) and rig.pcm.log[-1] == ("stop",)


def test_phone_already_connected_when_the_bridge_starts_is_found_by_snapshot(rig):
    dev = rig.fb.add_device(1, connected=True)
    rig.fb.add_object(f"{dev}/fd0", {"org.bluez.MediaTransport1": {"Device": ("o", dev), "State": "active"}})
    rig.ad.start()
    assert wait(lambda: ("transport", "active") in rig.rec.events)
    assert rig.rec.events[0] == ("connect", "AA:BB:CC:DD:EE:01")


def test_second_phone_is_disconnected_by_the_bridge_first_stays(rig):
    rig.ad.start()
    d1, d2 = rig.fb.add_device(1), rig.fb.add_device(2)
    rig.fb.set_props(d1, "org.bluez.Device1", Connected=True)
    assert wait(lambda: ("connect", "AA:BB:CC:DD:EE:01") in rig.rec.events)
    rig.fb.set_props(d2, "org.bluez.Device1", Connected=True)
    assert wait(lambda: any(c[1:3] == ("org.bluez.Device1", "Disconnect") and c[0] == d2 for c in rig.fb.calls))
    assert wait(lambda: props_of(rig.fb, d2, "org.bluez.Device1")["Connected"] is False)
    assert ("disconnect", "AA:BB:CC:DD:EE:01") not in rig.rec.events and rig.ad.model.active == d1


def test_non_phone_device_is_left_alone(rig):
    rig.ad.start()
    d = rig.fb.add_device(3, uuids=["00001124-0000-1000-8000-00805f9b34fb"])
    rig.fb.set_props(d, "org.bluez.Device1", Connected=True)
    time.sleep(0.5)
    assert rig.rec.events == [] and not [c for c in rig.fb.calls if c[2] == "Disconnect"]


def test_bluetoothd_restart_releases_phone_then_resyncs_and_reregisters_agent(rig):
    rig.ad.start(); assert wait(lambda: rig.fb.agent)
    rig.ad.set_pairable(True)
    dev = rig.fb.add_device(1, connected=True)
    assert wait(lambda: ("connect", "AA:BB:CC:DD:EE:01") in rig.rec.events)
    rig.fb.stop()
    assert wait(lambda: ("disconnect", "AA:BB:CC:DD:EE:01") in rig.rec.events)
    n = len(rig.rec.events)
    rig.fb.objects[dev]["org.bluez.Device1"]["Connected"] = variant(True)
    rig.fb.start(keep_objects=True)
    assert wait(lambda: rig.fb.agent is not None)                                  # agent registered again
    assert wait(lambda: len(rig.rec.events) > n and rig.rec.events[n] == ("connect", "AA:BB:CC:DD:EE:01"))
    assert props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")["Pairable"] is True      # pairing state re-applied


def test_adapter_property_errors_surface_as_bus_error(rig):
    rig.ad.start()
    rig.fb.stop()
    from btbridge.adapters.bluez import BusError
    with pytest.raises(BusError):
        rig.ad.set_pairable(True)


def test_remove_device_and_alias_over_dbus(rig):
    rig.ad.start()
    d1 = rig.fb.add_device(1, connected=True)
    assert wait(lambda: ("connect", "AA:BB:CC:DD:EE:01") in rig.rec.events)
    assert [d["address"] for d in rig.ad.paired_devices()] == ["AA:BB:CC:DD:EE:01"]
    assert rig.ad.remove_device("AA:BB:CC:DD:EE:01")
    assert wait(lambda: d1 not in rig.fb.objects) and wait(lambda: ("disconnect", "AA:BB:CC:DD:EE:01") in rig.rec.events)
    assert wait(lambda: rig.ad.paired_devices() == [])
    rig.ad.set_alias("Wohnzimmer Hi-Fi")
    assert props_of(rig.fb, ADAPTER, "org.bluez.Adapter1")["Alias"] == "Wohnzimmer Hi-Fi"
