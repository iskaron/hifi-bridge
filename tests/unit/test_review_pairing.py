"""UNIT (fake clock / SIMULATED phone / real HTTP): review M5 - a pairing-window change the Bluetooth adapter refused is retried
and shown, instead of the UI and the adapter silently disagreeing (reports/REVIEW_PHASE2B_EXISTING_CODE.md)."""
import urllib.parse

from test_webui import finish_wizard, ui  # noqa: F401  (fixture)

from btbridge.core.clock import FakeClock
from btbridge.core.pairing import PairingWindow


class Adapter:
    def __init__(self, failures=0):
        self.state, self.failures, self.calls = False, failures, 0

    def set_pairable(self, on):
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("org.bluez.Error.Failed")
        self.state = on


def test_failed_open_is_retried_on_tick_and_reported_meanwhile():
    clk, ad = FakeClock(), Adapter(failures=1)
    w = PairingWindow(300, clk, on_change=ad.set_pairable)
    assert w.open() is False and w.error and ad.state is False          # was: exception, window marked as applied
    clk.advance(0.1); w.tick()
    assert ad.state is False and ad.calls == 1                          # retries are spaced, not every 100 ms
    clk.advance(3); w.tick()
    assert ad.state is True and w.is_open() and w.error is None


def test_failed_close_keeps_retrying_until_the_adapter_is_closed():
    clk, ad = FakeClock(), Adapter()
    w = PairingWindow(300, clk, on_change=ad.set_pairable)
    assert w.open() and ad.state is True
    ad.failures = 2
    assert w.close() is False and ad.state is True and w.error          # was: UI 'closed', adapter discoverable forever
    for _ in range(3):
        clk.advance(3); w.tick()
    assert ad.state is False and w.error is None


def test_expiry_that_the_adapter_refuses_is_retried():
    clk, ad = FakeClock(), Adapter()
    w = PairingWindow(60, clk, on_change=ad.set_pairable)
    w.open(); ad.failures = 1
    clk.advance(61); w.tick()
    assert ad.state is True and w.error
    clk.advance(3); w.tick()
    assert ad.state is False and w.error is None


def test_ui_reports_a_refused_pairing_change(ui):
    finish_wizard(ui)
    real = ui.sim.set_pairable
    calls = {"n": 0}

    def flaky(on):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("org.bluez.Error.Failed")
        return real(on)
    ui.sim.set_pairable = flaky
    r = ui.c.post("/pairing/open")
    assert r.status == 303                                              # was: HTTP 500
    assert "adapter" in urllib.parse.unquote(ui.c.cookies.get("bab_flash", "")).lower()
    assert "pairing_adapter_error" in ui.svc.status()["degraded"]
    import time
    t0 = time.time()
    while time.time() - t0 < 5 and "pairing_adapter_error" in ui.svc.status()["degraded"]:
        time.sleep(0.1)
    assert "pairing_adapter_error" not in ui.svc.status()["degraded"] and calls["n"] >= 2
