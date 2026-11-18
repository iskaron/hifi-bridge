"""UNIT: regressions for the LOW review findings (reports/REVIEW_PHASE2B_EXISTING_CODE.md: L1-L12). Labels per test."""
import threading
import time

import pytest
from test_service import P1, Lmses, fake_tree, wait

from btbridge.service import ApplianceService
from btkit.sim_a2dp import SimulatedA2dpSource


def _svc(tmp_path, **kw):
    lmses = Lmses(); sim = SimulatedA2dpSource()
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge", lms_factory=lmses,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, **kw)
    s.sim, s.lmses = sim, lmses
    return s


# ---------------------------------------------------------------- L1: MQTT restarts leave nothing behind
def test_mqtt_restarts_do_not_accumulate_event_listeners(tmp_path):
    from test_mqtt import FakeClient
    from btbridge.mqtt.bridge import MqttBridge
    s = _svc(tmp_path, mqtt_factory=lambda svc: MqttBridge(svc, client_factory=FakeClient, telemetry_s=999))
    s.store.update({"mqtt_enabled": True, "mqtt_host": "broker.lan"})
    s.start()
    try:
        base = len(s._listeners)
        for i in range(20):
            s.store.update({"mqtt_port": 1000 + i})
        assert len(s._listeners) == base                    # was: one more listener per save
    finally:
        s.stop()


def test_a_stopped_mqtt_client_never_connects_afterwards():
    from btbridge.mqtt.client import MqttClient
    opened, release = threading.Event(), threading.Event()

    class Sock:
        def __init__(self): self.sent, self.closed = [], False
        def sendall(self, b): self.sent.append(b)
        def settimeout(self, t): pass
        def recv(self, n): raise OSError("closed")
        def close(self): self.closed = True
    sock = Sock()
    c = MqttClient("broker.invalid", 1883, client_id="hifibridge-x")

    def slow_open():
        opened.set(); release.wait(5)
        return sock
    c._open = slow_open
    c.start()
    assert opened.wait(2)
    stopper = threading.Thread(target=c.stop); stopper.start()
    assert wait(lambda: c._stop.is_set(), t=2)
    release.set(); stopper.join(6)
    assert sock.sent == [] and sock.closed                  # was: CONNECT with the same client id after stop()


# ---------------------------------------------------------------- L3: crash recovery never clears a newer session's journal
def test_recovery_in_the_watch_thread_keeps_the_journal_of_a_session_started_meanwhile(tmp_path):
    s = _svc(tmp_path)
    s.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    s.journal.record(P1, "http://bridge:8090/s/OLD/crashed.wav")          # left behind by a crashed process
    lms = s.runtime.lms
    entered, go = threading.Event(), threading.Event()
    real = lms.release

    def slow_release(pid, url):
        if "crashed" in url:
            entered.set(); go.wait(5)
        return real(pid, url)
    lms.release = slow_release
    t = threading.Thread(target=s.recover_stale_session); t.start()
    assert entered.wait(2)
    s.journal.record(P1, "http://bridge:8090/s/NEW/live.wav")             # the controller starts a session meanwhile
    go.set(); t.join(5)
    assert (s.journal.pending() or {}).get("url") == "http://bridge:8090/s/NEW/live.wav"   # was: cleared


# ---------------------------------------------------------------- L2: settings apply in the order they were saved, all or nothing
def test_concurrent_updates_reach_listeners_in_the_order_they_were_stored(tmp_path):
    from btbridge.config import ConfigStore
    st = ConfigStore(str(tmp_path))
    applied = []

    def listener(ch):
        if ch.get("device_name") == "A":
            time.sleep(0.3)                                  # the first change is slow to apply
        applied.append(ch["device_name"])
    st.on_change(listener)
    t1 = threading.Thread(target=st.update, args=({"device_name": "A"},)); t1.start()
    time.sleep(0.1)
    st.update({"device_name": "B"})
    t1.join()
    assert applied == ["A", "B"] and st.get("device_name") == applied[-1]   # was: B applied first, A last -> runtime != stored


def test_mqtt_form_does_not_store_the_password_when_another_field_is_invalid(tmp_path):
    from test_webui import Client, finish_wizard
    from btbridge.webui.server import WebUI
    s = _svc(tmp_path, discover=lambda timeout: [])
    s.start()
    w = WebUI(s, "127.0.0.1", 0); port = w.start()
    try:
        u = type("U", (), dict(svc=s, c=Client(port), code=open(s.auth.code_file).read().strip()))
        finish_wizard(u)
        r = u.c.post("/settings/mqtt", {"mqtt_host": "broker.lan", "mqtt_port": "99999", "mqtt_password": "new-secret"})
        assert r.status == 303 and s.store.secret("mqtt_password") is None   # was: saved although the form was rejected
        u.c.post("/settings/mqtt", {"mqtt_host": "broker.lan", "mqtt_port": "1883", "mqtt_password": "new-secret"})
        assert s.store.secret("mqtt_password") == "new-secret"
    finally:
        w.stop(); s.stop()
