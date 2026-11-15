"""UNIT: MQTT packet encoding and the Home Assistant bridge logic with a fake client (no broker; see test_11 for the real broker)."""
import json

from btbridge.mqtt.bridge import MqttBridge, entities
from btbridge.mqtt.client import _varint, connect_packet, publish_packet, subscribe_packet


def test_varint_encoding_matches_the_spec_examples():
    assert _varint(0) == b"\x00" and _varint(127) == b"\x7f" and _varint(128) == b"\x80\x01"
    assert _varint(16383) == b"\xff\x7f" and _varint(16384) == b"\x80\x80\x01" and _varint(268435455) == b"\xff\xff\xff\x7f"


def test_connect_packet_layout_with_will_and_credentials():
    p = connect_packet("cid", 60, "u", "p", ("a/avail", b"offline", True))
    assert p[0] == 0x10
    body = p[2:]
    assert body[:6] == b"\x00\x04MQTT" and body[6] == 4
    assert body[7] == 0x02 | 0x04 | 0x20 | 0x80 | 0x40            # clean, will, will-retain, user, password
    assert body[8:10] == b"\x00\x3c" and b"a/avail" in body and body.endswith(b"\x00\x01u\x00\x01p")


def test_publish_and_subscribe_packets():
    p = publish_packet("t/x", b"hi", True)
    assert p == b"\x31\x07\x00\x03t/xhi"
    s = subscribe_packet(1, ["a/#"])
    assert s == b"\x82\x08\x00\x01\x00\x03a/#\x00"


class FakeClient:
    def __init__(self, host, port, **kw):
        self.kw, self.sent, self.connected = kw, [], True
    def start(self): pass
    def stop(self): self.connected = False
    def publish(self, topic, payload, retain=False): self.sent.append((topic, payload, retain)); return True
    def state(self): return {"connected": self.connected, "last_error": None}


class FakeStore:
    def __init__(self): self.v = {"mqtt_base_topic": "btbridge", "mqtt_discovery_prefix": "homeassistant", "mqtt_host": "b", "mqtt_port": 1883,
                                  "mqtt_username": "", "mqtt_tls": False, "device_name": "Hi-Fi Bridge"}
    def get(self, k): return self.v[k]
    def secret(self, k): return None


class FakeService:
    def __init__(self):
        self.store, self.calls, self.listeners, self.logs = FakeStore(), [], [], lambda m: None
        self.last_system = {"cpu_percent": 3.0, "temperature_c": 51.2, "thermal_state": "ok", "memory": {"used_percent": 40.0},
                            "storage": {"used_percent": 12.0}, "uptime_s": 99}
    def instance_id(self): return "ab12cd34"
    def on_event(self, fn): self.listeners.append(fn)
    def status(self):
        return {"headline": "playing", "bridge": {"bluetooth_connection": "connected", "bt_media_activity": "playing", "target_ownership": "owned",
                "lyrion_reachability": "reachable", "audio_permission": "allowed"}, "bluetooth": {"connected_device": {"name": "Pixel"},
                "pairing_open": False, "pairing_remaining_s": 0}, "now_playing": "Band - Song", "degraded": []}
    def open_pairing(self): self.calls.append("open")
    def close_pairing(self): self.calls.append("close")
    def set_audio_allowed(self, v): self.calls.append(("audio", v))
    def diagnostic_test(self): self.calls.append("test"); return "started"


def bridge():
    svc = FakeService(); b = MqttBridge(svc, client_factory=FakeClient, telemetry_s=999)
    return svc, b


def test_last_will_and_subscriptions():
    _, b = bridge()
    assert b.client.kw["will"] == ("btbridge/ab12cd34/availability", b"offline", True)
    assert b.client.kw["subscriptions"] == ["btbridge/ab12cd34/cmd/#"]


def test_on_connect_publishes_retained_discovery_availability_state_and_system():
    _, b = bridge(); b._on_connect()
    topics = [t for t, _, r in b.client.sent if r]
    assert "homeassistant/switch/ab12cd34/pairing/config" in topics and "homeassistant/button/ab12cd34/test/config" in topics
    assert topics[-3:] == ["btbridge/ab12cd34/availability", "btbridge/ab12cd34/state", "btbridge/ab12cd34/system"]
    cfg = json.loads(next(p for t, p, _ in b.client.sent if t.endswith("/temperature/config")))
    assert cfg["device_class"] == "temperature" and cfg["unit_of_measurement"] == "°C" and cfg["unique_id"] == "btbridge_ab12cd34_temperature"
    assert cfg["device"]["identifiers"] == ["btbridge_ab12cd34"] and cfg["availability_topic"].endswith("/availability")
    state = json.loads(next(p for t, p, _ in b.client.sent if t.endswith("/state")))
    assert state["headline"] == "playing" and state["device"] == "Pixel" and state["audio_permission"] == "allowed"


def test_unique_ids_are_unique_and_every_entity_has_availability():
    ids = [cfg["unique_id"] for _, _, cfg in entities("b", "x")]
    assert len(ids) == len(set(ids)) >= 12
    assert all(cfg["availability_topic"] == "b/x/availability" for _, _, cfg in entities("b", "x"))


def test_commands_map_to_service_actions_and_unknown_ones_are_ignored():
    svc, b = bridge()
    for topic, payload in (("pairing", b"ON"), ("pairing", b"off"), ("audio", b"OFF"), ("test", b"PRESS"), ("reboot", b"NOW"), ("audio", b"maybe")):
        b._on_message(f"btbridge/ab12cd34/cmd/{topic}", payload)
    assert svc.calls == ["open", "close", ("audio", False), "test"]


def test_events_publish_state_and_thermal_immediately():
    svc, b = bridge()
    svc.listeners[0]("status_changed", svc.status())
    svc.listeners[0]("thermal", dict(svc.last_system, temperature_c=82.0, thermal_state="critical"))
    sysmsg = json.loads(next(p for t, p, _ in b.client.sent if t.endswith("/system")))
    assert sysmsg["thermal_state"] == "critical" and sysmsg["temperature_c"] == 82.0
    svc.listeners[0]("settings_changed", {"keys": ["device_name"]})
    assert any(t.endswith("/playback/config") for t, _, _ in b.client.sent)
