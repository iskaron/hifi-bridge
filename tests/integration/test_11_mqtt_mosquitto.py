"""INTEGRATION: the stdlib MQTT client and the HA bridge against a REAL Eclipse Mosquitto 2.0.20 broker in the lab network;
end-to-end with the appliance service, the SIMULATED phone, the REAL LMS and Squeezelite. No real Home Assistant is involved: discovery
payloads are checked for the documented HA MQTT-discovery schema keys, not imported into HA."""
import json
import socket
import threading
import uuid
import time

import pytest

from btbridge.mqtt import MqttBridge, MqttClient
from btbridge.service import ApplianceService
from btkit.lab import my_ip, wait_for
from btkit.lms_rpc import PLAYERS
from btkit.sim_a2dp import SimulatedA2dpSource

pytestmark = pytest.mark.integration
BROKER = "mosquitto"


class Sub:
    """A subscriber built from the same client (collects messages)."""
    def __init__(self, topics, **kw):
        self.msgs, self.lock = [], threading.Lock()
        self.c = MqttClient(BROKER, 1883, client_id=f"sub-{time.time_ns()}", subscriptions=topics, on_message=self._m, **kw)
        self.c.start(); assert wait_for(lambda: self.c.connected, 10)
        time.sleep(0.3)

    def _m(self, t, p):
        with self.lock:
            self.msgs.append((t, p.decode()))

    def get(self, topic):
        with self.lock:
            return [p for t, p in self.msgs if t == topic]

    def stop(self):
        self.c.stop()


def test_publish_retained_and_receive_over_the_real_broker():
    pub = MqttClient(BROKER, 1883, client_id="pub-1"); pub.start(); assert wait_for(lambda: pub.connected, 10)
    assert pub.publish("lab/retained", "hello", retain=True)
    pub.stop()
    sub = Sub(["lab/#"])                                   # connects AFTER the publish: retained message must arrive
    assert wait_for(lambda: sub.get("lab/retained") == ["hello"], 5)
    sub.stop()


def test_password_listener_rejects_bad_credentials_and_accepts_good_ones():
    bad = MqttClient(BROKER, 1884, client_id="bad", username="bridge", password="wrong", backoff=(0.2, 0.5)); bad.start()
    assert wait_for(lambda: bad.last_error and "refused" in bad.last_error, 10), bad.last_error
    assert not bad.connected; bad.stop()
    good = MqttClient(BROKER, 1884, client_id="good", username="bridge", password="lab-only-mqtt-password"); good.start()
    assert wait_for(lambda: good.connected, 10); good.stop()


class Proxy:
    """TCP proxy between client and broker so the test can cut the connection abruptly (no DISCONNECT -> last will fires)."""
    def __init__(self):
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(4); self.port = self.srv.getsockname()[1]
        self.conns, self.accepting = [], True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            if not self.accepting:
                c.close(); continue
            b = socket.create_connection((BROKER, 1883))
            self.conns += [c, b]
            for x, y in ((c, b), (b, c)):
                threading.Thread(target=self._pipe, args=(x, y), daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                d = a.recv(4096)
                if not d:
                    break
                b.sendall(d)
        except OSError:
            pass
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

    def cut(self):
        for s in self.conns:
            try:
                s.shutdown(socket.SHUT_RDWR); s.close()
            except OSError:
                pass
        self.conns = []


def test_last_will_on_abrupt_loss_and_automatic_reconnect():
    # A fresh topic per run: a retained "online" left by an earlier run must not satisfy the first check before this client connected.
    topic = f"will/{uuid.uuid4().hex}/avail"
    sub = Sub(["will/#"])
    px = Proxy()
    c = MqttClient("127.0.0.1", px.port, client_id="willy", keepalive=5, will=(topic, b"offline", True), backoff=(0.2, 1.0),
                   on_connect=lambda: c.publish(topic, "online", retain=True))
    c.start()
    assert wait_for(lambda: c.connects >= 1 and sub.get(topic) == ["online"], 10)
    px.cut()                                                         # crash-like loss: broker publishes the last will
    assert wait_for(lambda: "offline" in sub.get(topic), 15), sub.msgs
    assert wait_for(lambda: c.connects >= 2 and sub.get(topic)[-1] == "online", 15)   # reconnected, online again
    c.publish(topic, "", retain=True)                                # leave no retained message behind
    c.stop(); sub.stop()


def test_appliance_end_to_end_ha_discovery_state_and_commands(lab, radio, tmp_path):
    """Service + SIMULATED phone + REAL LMS/Squeezelite + REAL Mosquitto."""
    sim = SimulatedA2dpSource()
    svc = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=8093, advertise_host=my_ip(), mqtt_factory=MqttBridge,
                           echo=None, auth_iterations=1000)
    svc.store.update({"lms_host": "lms", "lms_port": 9000, "target_player": PLAYERS["player1"], "mqtt_enabled": True, "mqtt_host": BROKER,
                      "mqtt_port": 1883, "device_name": "Lab Bridge", "mqtt_allow_pairing": True, "mqtt_allow_audio": True,
                      "mqtt_allow_test": True})
    iid = svc.instance_id()
    sub = Sub(["homeassistant/#", f"btbridge/{iid}/#"])
    svc.start()
    try:
        assert wait_for(lambda: svc.mqtt and svc.mqtt.state()["connected"], 10)
        assert wait_for(lambda: sub.get(f"btbridge/{iid}/availability")[-1:] == ["online"], 10)
        cfg = json.loads(sub.get(f"homeassistant/switch/{iid}/pairing/config")[-1])
        assert cfg["command_topic"] == f"btbridge/{iid}/cmd/pairing" and cfg["device"]["name"] == "Lab Bridge"
        for key in ("name", "unique_id", "state_topic", "availability_topic", "device"):
            assert key in cfg
        assert json.loads(sub.get(f"btbridge/{iid}/system")[-1])["uptime_s"] is not None
        # phone plays -> state over MQTT, audio on the real player
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.mode() == "play" and lab.url() == svc.runtime.controller.session.url, 8)
        assert wait_for(lambda: any(json.loads(p)["headline"] == "playing" for p in sub.get(f"btbridge/{iid}/state")), 8)
        # MQTT command: pairing ON opens the window on the (simulated) adapter
        pub = MqttClient(BROKER, 1883, client_id="ha-sim"); pub.start(); assert wait_for(lambda: pub.connected, 10)
        pub.publish(f"btbridge/{iid}/cmd/pairing", "ON")
        assert wait_for(lambda: sim.pairable, 5)
        # MQTT command: audio OFF releases ONLY our stream on the real LMS
        pub.publish(f"btbridge/{iid}/cmd/audio", "OFF")
        assert wait_for(lambda: lab.mode() == "stop", 5)
        assert wait_for(lambda: json.loads(sub.get(f"btbridge/{iid}/state")[-1])["audio_permission"] == "blocked", 5)
        # foreign source playing now: audio ON + test button must never touch it
        lab.lms.req(PLAYERS["player1"], "playlist", "play", radio.url, "Radio")
        assert wait_for(lambda: lab.url() == radio.url and lab.mode() == "play", 5)
        n = len(svc.runtime.lms.commands_sent)
        pub.publish(f"btbridge/{iid}/cmd/test", "PRESS"); time.sleep(1.5)
        assert lab.url() == radio.url and len(svc.runtime.lms.commands_sent) == n      # diagnostic refused on busy target
        pub.stop()
    finally:
        svc.stop()
        assert wait_for(lambda: sub.get(f"btbridge/{iid}/availability")[-1:] == ["offline"], 5)   # clean stop -> offline
        sub.stop()
