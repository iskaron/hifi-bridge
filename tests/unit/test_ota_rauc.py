"""UNIT (private dbus-daemon + SIMULATED RAUC service): the bridge's RAUC D-Bus client.

The same client is run against the REAL RAUC 1.13 service in tests/os/test_ota_rauc_real.py; the fake is shaped after those replies."""
import time

import pytest

from btbridge.ota.rauc import RaucClient, RaucError, RaucUnavailable, plain
from btkit.fake_bluez import PrivateBus
from btkit.fake_rauc import FakeRauc


@pytest.fixture
def bus():
    b = PrivateBus()
    yield b
    b.close()


@pytest.fixture
def rauc(bus):
    f = FakeRauc(bus.address)
    f.start()
    c = RaucClient(bus=bus.address, timeout=5)
    yield f, c
    c.close()
    f.stop()


def wait(pred, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = pred()
        if r is not None and r is not False:
            return r
        time.sleep(0.02)
    raise AssertionError("timeout")


def test_plain_unwraps_nested_variants_like_real_rauc_sends_them():
    info = {"update": ("v", ("a{sv}", {"version": ("s", "0.4.0")})), "images": ("v", ("aa{sv}", [{"size": ("t", 5)}])),
            "meta": ("v", ("a{sa{ss}}", {"x": {"k": "v"}})), "n": ("u", 3)}
    assert plain("a{sv}", info) == {"update": {"version": "0.4.0"}, "images": [{"size": 5}], "meta": {"x": {"k": "v"}}, "n": 3}


def test_properties_slots_primary(rauc):
    f, c = rauc
    p = c.properties()
    assert p["Operation"] == "idle" and p["Compatible"] == "hifi-bridge-rpi3" and p["BootSlot"] == "A" and p["Progress"] == (0, "", 0)
    slots = c.slots()
    assert [s["name"] for s in slots] == ["rootfs.0", "rootfs.1"]
    assert slots[0]["bootname"] == "A" and slots[0]["state"] == "booted" and slots[1]["boot-status"] == "bad"
    assert c.primary() == "rootfs.0"


def test_mark_good_active_bad_and_errors(rauc):
    f, c = rauc
    assert c.mark("good", "booted") == ("rootfs.0", "marked slot rootfs.0 as good")
    assert c.mark("active", "other") == ("rootfs.1", "activated slot rootfs.1") and c.primary() == "rootfs.1"
    assert c.mark("bad", "rootfs.1")[0] == "rootfs.1" and c.primary() == "rootfs.0"
    with pytest.raises(RaucError, match="No slot"):
        c.mark("good", "rootfs.7")
    with pytest.raises(ValueError):
        c.mark("weird", "booted")


def test_inspect_returns_plain_info_and_raucs_refusal(rauc):
    f, c = rauc
    f.bundles["/d/ok.raucb"] = {"compatible": "hifi-bridge-rpi3", "version": "0.4.0"}
    f.bundles["/d/bad.raucb"] = "signature verification failed: CMS_verify:certificate verify error"
    info = c.inspect("/d/ok.raucb")
    assert info["update"] == {"compatible": "hifi-bridge-rpi3", "version": "0.4.0", "description": ""}
    assert info["bundle"]["format"] == "verity"
    with pytest.raises(RaucError, match="signature verification failed"):
        c.inspect("/d/bad.raucb")
    with pytest.raises(RaucError, match="Failed to open bundle"):
        c.inspect("/d/missing.raucb")


def test_install_reports_progress_and_the_completed_signal(rauc):
    f, c = rauc
    f.bundles["/d/ok.raucb"] = {"compatible": "hifi-bridge-rpi3", "version": "0.4.0"}
    assert c.completed() is None
    c.install("/d/ok.raucb")
    seen = set()

    def done():
        seen.add(c.properties()["Operation"])
        return c.completed()
    assert wait(lambda: (lambda r: r if r is not None else None)(done())) == 0
    assert "installing" in seen and c.properties()["Progress"][1] == "Installing done."
    assert c.primary() == "rootfs.1" and c.slots()[1]["bundle.version"] == "0.4.0"


def test_failed_install_sets_last_error_and_completed_1(rauc):
    f, c = rauc
    f.install_plan = {"result": 1, "error": "Failed mounting bundle: Failed to open /dev/loop-control"}
    c.install("/d/x.raucb")
    r = wait(lambda: (c.properties(), c.completed())[1])
    assert r == 1 and "loop-control" in c.properties()["LastError"] and c.primary() == "rootfs.0"


def test_second_install_while_busy_is_refused(rauc):
    f, c = rauc
    f.install_plan = {"result": 0, "steps": 20, "step_s": 0.05}
    c.install("/d/x.raucb")
    with pytest.raises(RaucError, match="Already processing"):
        c.install("/d/x.raucb")


def test_daemon_gone_is_unavailable_and_reconnects_when_back(bus):
    f = FakeRauc(bus.address); f.start()
    c = RaucClient(bus=bus.address, timeout=2)
    assert c.properties()["BootSlot"] == "A"
    f.stop()
    with pytest.raises(RaucUnavailable):
        c.properties()
    f2 = FakeRauc(bus.address, booted="B"); f2.start()
    try:
        assert c.properties()["BootSlot"] == "B"
    finally:
        c.close(); f2.stop()


def test_crash_during_install_sends_no_completed(rauc):
    f, c = rauc
    f.install_plan = {"crash": True, "steps": 2}
    c.install("/d/x.raucb")
    with pytest.raises(RaucUnavailable):
        wait(lambda: c.properties() and False, timeout=3)
    assert c.completed() is None


def test_no_bus_is_unavailable():
    c = RaucClient(bus="unix:path=/nonexistent/bus", timeout=1)
    with pytest.raises(RaucUnavailable):
        c.properties()
    assert c.completed() is None
    c.close()


def test_unknown_method_is_a_rauc_error(rauc):
    f, c = rauc
    with pytest.raises(RaucError):
        c._call("NoSuchMethod")


def test_connection_lost_while_pumping_signals(rauc):
    f, c = rauc
    c.properties()

    class Broken:
        def recv_messages(self, timeout=None):
            raise EOFError("gone")

        def close(self):
            pass
    c._conn = Broken()
    with pytest.raises(RaucUnavailable):
        c.completed()
    assert c._conn is None


def test_connection_lost_during_a_call(rauc):
    f, c = rauc
    c.properties()

    class Broken:
        def send_and_get_reply(self, msg, timeout=None):
            raise OSError("broken pipe")

        def close(self):
            pass
    c._conn = Broken()
    with pytest.raises(RaucUnavailable):
        c.primary()
    assert c._conn is None
