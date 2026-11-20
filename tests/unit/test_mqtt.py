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
    def __init__(self, **allow):
        self.v = {"mqtt_base_topic": "btbridge", "mqtt_discovery_prefix": "homeassistant", "mqtt_host": "b", "mqtt_port": 1883,
                  "mqtt_username": "", "mqtt_tls": False, "device_name": "Wohnzimmer", "mqtt_telemetry_s": 300, "mqtt_ca_pem": "",
                  "mqtt_allow_pairing": False, "mqtt_allow_audio": False, "mqtt_allow_test": False, **allow}
    def get(self, k): return self.v[k]
    def secret(self, k): return None


class FakeService:
    def __init__(self, **allow):
        self.store, self.calls, self.listeners, self.logs = FakeStore(**allow), [], [], lambda m: None
        self.last_system = {"cpu_percent": 3.0, "temperature_c": 51.2, "thermal_state": "ok", "memory": {"used_percent": 40.0},
                            "storage": {"used_percent": 12.0}, "uptime_s": 99}
    def instance_id(self): return "ab12cd34"
    def on_event(self, fn): self.listeners.append(fn)
    def thermal(self): return {"state": "ok", "temperature_c": 51.2}
    def status(self):
        return {"headline": "playing", "bridge": {"bluetooth_connection": "connected", "bt_media_activity": "playing", "target_ownership": "owned",
                "lyrion_reachability": "reachable", "audio_permission": "allowed"}, "bluetooth": {"connected_device": {"name": "Pixel"},
                "pairing_open": False, "pairing_remaining_s": 0}, "now_playing": "Band - Song", "degraded": []}
    def open_pairing(self): self.calls.append("open")
    def close_pairing(self): self.calls.append("close")
    def set_audio_allowed(self, v): self.calls.append(("audio", v))
    def diagnostic_test(self): self.calls.append("test"); return "started"


def bridge(**allow):
    svc = FakeService(**allow); b = MqttBridge(svc, client_factory=FakeClient, telemetry_s=999)
    return svc, b


def configs(b):
    return {t: p for t, p, r in b.client.sent if t.endswith("/config")}


def test_default_is_read_only_no_subscriptions_no_command_entities():
    _, b = bridge(); b._on_connect()
    assert b.client.kw["subscriptions"] == [] and b.client.kw["will"] == ("btbridge/ab12cd34/availability", b"offline", True)
    c = configs(b)
    for removed in ("switch/ab12cd34/pairing", "switch/ab12cd34/audio", "button/ab12cd34/test"):
        assert c[f"homeassistant/{removed}/config"] == ""                     # empty retained config removes stale entities
    for present in ("binary_sensor/ab12cd34/pairing_state", "binary_sensor/ab12cd34/audio_state", "binary_sensor/ab12cd34/overheating"):
        assert json.loads(c[f"homeassistant/{present}/config"])["state_topic"]
    assert not any("command_topic" in p for p in c.values() if p)


def test_each_control_is_enabled_separately():
    _, b = bridge(mqtt_allow_pairing=True); b._on_connect()
    assert b.client.kw["subscriptions"] == ["btbridge/ab12cd34/cmd/pairing"]
    c = configs(b)
    assert json.loads(c["homeassistant/switch/ab12cd34/pairing/config"])["command_topic"] == "btbridge/ab12cd34/cmd/pairing"
    assert c["homeassistant/binary_sensor/ab12cd34/pairing_state/config"] == "" and c["homeassistant/switch/ab12cd34/audio/config"] == ""
    _, b = bridge(mqtt_allow_pairing=True, mqtt_allow_audio=True, mqtt_allow_test=True)
    assert sorted(b.client.kw["subscriptions"]) == ["btbridge/ab12cd34/cmd/audio", "btbridge/ab12cd34/cmd/pairing", "btbridge/ab12cd34/cmd/test"]


def test_on_connect_publishes_retained_discovery_availability_state_thermal_and_system():
    _, b = bridge(); b._on_connect()
    retained = [t for t, _, r in b.client.sent if r]
    assert retained[-4:] == ["btbridge/ab12cd34/availability", "btbridge/ab12cd34/state", "btbridge/ab12cd34/thermal", "btbridge/ab12cd34/system"]
    cfg = json.loads(configs(b)["homeassistant/sensor/ab12cd34/temperature/config"])
    assert cfg["device_class"] == "temperature" and cfg["unit_of_measurement"] == "°C" and cfg["unique_id"] == "hifibridge_ab12cd34_temperature"
    assert cfg["device"]["identifiers"] == ["hifibridge_ab12cd34"] and cfg["device"]["name"] == "Wohnzimmer" and cfg["device"]["model"] == "Hi-Fi Bridge"
    assert cfg["entity_category"] == "diagnostic"
    state = json.loads(next(p for t, p, _ in b.client.sent if t.endswith("/state")))
    assert state["headline"] == "playing" and state["device"] == "Pixel" and state["audio_permission"] == "allowed"


def test_a_clean_stop_clears_the_retained_phone_and_title_before_going_offline():
    """DECISIONS #124: the retained state must not keep the last phone name and title after the bridge stops."""
    _, b = bridge(); b._on_connect(); b.client.sent.clear()
    b.stop()
    (t1, p1, r1), (t2, p2, r2) = b.client.sent
    assert (t1, r1, t2, p2, r2) == ("btbridge/ab12cd34/state", True, "btbridge/ab12cd34/availability", "offline", True)
    state = json.loads(p1)
    assert state["device"] is None and state["now_playing"] is None and state["bluetooth_connection"] == "disconnected"


def test_stop_still_clears_the_state_and_says_goodbye_when_the_service_status_fails():
    svc, b = bridge(); b._on_connect(); b.client.sent.clear()
    def broken(): raise RuntimeError("runtime already torn down")
    svc.status = broken
    b.stop()
    assert [t for t, _, _ in b.client.sent] == ["btbridge/ab12cd34/state", "btbridge/ab12cd34/availability"]
    assert json.loads(b.client.sent[0][1]) == {"device": None, "now_playing": None, "bluetooth_connection": "disconnected",
                                                 "bt_media_activity": "idle"}


def test_state_payload_after_a_disconnect_has_no_phone_and_no_title():
    svc, b = bridge()
    st = svc.status(); st["bluetooth"]["connected_device"] = None; st["now_playing"] = None
    st["bridge"]["bluetooth_connection"] = "disconnected"
    p = b.state_payload(st)
    assert p["device"] is None and p["now_playing"] is None


def test_unique_ids_are_unique_and_every_entity_has_availability():
    for allow in ({"pairing": False, "audio": False, "test": False}, {"pairing": True, "audio": True, "test": True}):
        cfgs = [cfg for _, _, cfg in entities("b", "x", allow) if cfg]
        ids = [cfg["unique_id"] for cfg in cfgs]
        assert len(ids) == len(set(ids)) >= 14 and all(cfg["availability_topic"] == "b/x/availability" for cfg in cfgs)


def test_disabled_commands_are_ignored_even_if_published():
    svc, b = bridge()
    for topic, payload in (("pairing", b"ON"), ("audio", b"OFF"), ("test", b"PRESS")):
        b._on_message(f"btbridge/ab12cd34/cmd/{topic}", payload)
    assert svc.calls == [] and [c[2] for c in b.commands] == [False, False, False]


def test_enabled_commands_map_to_service_actions_and_bad_values_are_ignored():
    svc, b = bridge(mqtt_allow_pairing=True, mqtt_allow_audio=True, mqtt_allow_test=True)
    for topic, payload in (("pairing", b"ON"), ("pairing", b"off"), ("audio", b"OFF"), ("test", b"PRESS"), ("reboot", b"NOW"), ("audio", b"maybe")):
        b._on_message(f"btbridge/ab12cd34/cmd/{topic}", payload)
    assert svc.calls == ["open", "close", ("audio", False), "test"]


def test_only_the_pairing_command_works_when_only_pairing_is_enabled():
    svc, b = bridge(mqtt_allow_pairing=True)
    b._on_message("btbridge/ab12cd34/cmd/audio", b"OFF"); b._on_message("btbridge/ab12cd34/cmd/test", b"PRESS")
    b._on_message("btbridge/ab12cd34/cmd/pairing", b"ON")
    assert svc.calls == ["open"]


def test_thermal_event_is_published_immediately_and_device_rename_republishes_discovery():
    svc, b = bridge()
    svc.listeners[0]("status_changed", svc.status())
    n = len(b.client.sent)
    svc.listeners[0]("thermal", {"state": "critical", "temperature_c": 83.5, "checked_at": 1.0})
    th = json.loads(next(p for t, p, _ in b.client.sent[n:] if t.endswith("/thermal")))
    assert th == {"state": "critical", "temperature_c": 83.5}
    svc.listeners[0]("settings_changed", {"keys": ["device_name"]})
    assert any(t.endswith("/playback/config") for t, _, _ in b.client.sent)


def test_tls_context_always_verifies_and_accepts_an_owner_ca():
    import ssl, subprocess, tempfile, os
    from btbridge.mqtt.bridge import tls_context
    ctx = tls_context("")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    d = tempfile.mkdtemp()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2",
                    "-subj", "/CN=test-ca", "-keyout", os.path.join(d, "k"), "-out", os.path.join(d, "c")], check=True, capture_output=True)
    ctx = tls_context(open(os.path.join(d, "c")).read())
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname and ctx.cert_store_stats()["x509_ca"] >= 1
