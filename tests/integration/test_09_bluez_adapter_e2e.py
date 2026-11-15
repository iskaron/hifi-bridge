"""INTEGRATION: fake BlueZ (private D-Bus) -> real JeepneyBus + BluezAdapter -> real bridge -> REAL LMS 9.1.2 -> REAL Squeezelite.

SIMULATED: BlueZ (hand-written service per the BlueZ docs), the phone, and the PCM source (a real-time signal generator standing in
for PipeWire capture).  REAL: the adapter code path, the D-Bus client, the bridge, LMS and the player.  This is NOT evidence that a real
bluetoothd, phone or PipeWire behaves like the fake (docs/HARDWARE_VALIDATION_PLAN.md)."""
import threading
import time

import pytest

pytest.importorskip("jeepney")
from btkit import analyze as A  # noqa: E402
from btkit import signal as S  # noqa: E402
from btkit.fake_bluez import FakeBluez, PrivateBus  # noqa: E402
from btkit.lab import make_bridge, wait_for  # noqa: E402
from btkit.lms_rpc import PLAYERS  # noqa: E402

from btbridge.adapters.bluez import BluezAdapter  # noqa: E402
from btbridge.adapters.bluez.jeepney_bus import JeepneyBus  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.dbus]
RADIO_SEED = 7


class SignalPcm:
    """PcmCapture test double: delivers the index-addressable test signal in real time while started (stand-in for PipeWire)."""
    def __init__(self):
        self.frame, self.sent, self._stop, self._t = 0, [], threading.Event(), None
        self.starts = 0

    def start(self, address, on_pcm):
        self.stop(); self.starts += 1
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(on_pcm, self._stop), daemon=True); self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
        self._t = None

    def _run(self, on_pcm, stop):
        n = S.RATE // 50
        nxt = time.monotonic()
        while not stop.is_set():
            data = S.frames_bytes(self.frame, n, 0)
            self.sent.append((time.time(), self.frame, n)); self.frame += n
            on_pcm(data)
            nxt += 0.02
            time.sleep(max(0, nxt - time.monotonic()))


@pytest.fixture
def stack(lab):
    pb = PrivateBus(); fb = FakeBluez(pb.address); fb.start()
    pcm = SignalPcm()
    ad = BluezAdapter(JeepneyBus(bus=pb.address), pcm)
    rt, _ = make_bridge(sim=ad)
    yield type("S", (), dict(lab=lab, fb=fb, pcm=pcm, ad=ad, rt=rt))
    rt.stop(); fb.stop(); pb.close()


def phone_play(fb, n=1):
    dev = fb.add_device(n)
    fb.add_object(f"{dev}/fd0", {"org.bluez.MediaTransport1": {"Device": ("o", dev), "State": "idle", "Volume": ("q", 60)}})
    fb.add_object(f"{dev}/player0", {"org.bluez.MediaPlayer1": {"Device": ("o", dev), "Status": "paused"}})
    fb.set_props(dev, "org.bluez.Device1", Connected=True)
    return dev


def press_play(fb, dev):
    fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="active")
    fb.set_props(f"{dev}/player0", "org.bluez.MediaPlayer1", Status="playing")


def press_pause(fb, dev):
    fb.set_props(f"{dev}/player0", "org.bluez.MediaPlayer1", Status="paused")
    fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="idle")


def test_play_pause_disconnect_through_the_dbus_adapter_is_bit_exact(stack):
    s, rt, lab = stack, stack.rt, stack.lab
    t0 = time.time()
    dev = phone_play(s.fb)
    assert wait_for(lambda: rt.status()["bluetooth_connection"] == "connected", 5)
    time.sleep(0.5); assert lab.mode() == "stop"                         # connecting alone never starts LMS
    press_play(s.fb, dev)
    assert wait_for(lambda: rt.controller.ownership == "owned" and lab.mode() == "play", 8)
    assert lab.url() == rt.controller.session.url
    time.sleep(8)
    t_end = time.time()
    press_pause(s.fb, dev)
    assert wait_for(lambda: lab.mode() == "stop", 5)                     # drop_restart: our item removed at once
    cap = lab.capture(since=t0, until=t_end + 0.2)
    res = A.integrity(cap)
    assert res["blocks"] > 60 and res["mismatched"] <= 1 and all(d == 0 for _, d in res["jumps"]), res
    press_play(s.fb, dev)                                                # resume within grace: fresh session, again our URL
    assert wait_for(lambda: lab.mode() == "play" and lab.url() == rt.controller.session.url, 8)
    s.fb.set_props(dev, "org.bluez.Device1", Connected=False)            # disconnect releases immediately
    assert wait_for(lambda: lab.mode() == "stop", 5)
    assert {c[0] for c in rt.lms.commands_sent} <= {"play", "release", "power_on", "volume_delta"}


def test_radio_takeover_then_late_dbus_events_never_touch_the_radio(stack, radio):
    s, rt, lab = stack, stack.rt, stack.lab
    dev = phone_play(s.fb)
    press_play(s.fb, dev)
    assert wait_for(lambda: lab.mode() == "play" and lab.url() == rt.controller.session.url, 8)
    lab.lms.req(PLAYERS["player1"], "playlist", "play", radio.url, "Radio")        # the 'user' switches to radio
    assert wait_for(lambda: lab.url() == radio.url and rt.controller.ownership == "lost", 6)
    radio_url = radio.url
    n = len(rt.lms.commands_sent)
    press_pause(s.fb, dev); time.sleep(1); s.fb.set_props(dev, "org.bluez.Device1", Connected=False); time.sleep(3)
    assert lab.url() == radio_url and lab.mode() == "play"
    assert len(rt.lms.commands_sent) == n                                 # zero LMS commands after ownership loss


def test_second_phone_is_refused_over_dbus_while_first_keeps_playing(stack):
    s, rt, lab = stack, stack.rt, stack.lab
    d1 = phone_play(s.fb, 1); press_play(s.fb, d1)
    assert wait_for(lambda: lab.mode() == "play", 8)
    d2 = s.fb.add_device(2); s.fb.set_props(d2, "org.bluez.Device1", Connected=True)
    assert wait_for(lambda: s.fb.objects[d2]["org.bluez.Device1"]["Connected"][1] is False, 5)
    time.sleep(1)
    assert lab.mode() == "play" and rt.status()["bluetooth_connection"] == "connected"
