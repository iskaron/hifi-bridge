"""INTEGRATION with a REAL Home Assistant 2026.11.1 + REAL Mosquitto 2.0.20 (authenticated listener) + the real appliance service
(SIMULATED phone, no LMS needed). Run via scripts/run_homeassistant.sh (fresh HA config each run). Checks what HA actually created:
entity types, device mapping, units/classes, availability incl. last will, reconnect, HA restart, removal of stale entities, read-only
default, separately enabled commands, telemetry and the immediate over-temperature warning."""
import json
import os
import time

import pytest
from ha import HA

from btbridge.mqtt import MqttBridge
from btbridge.service import ApplianceService
from btbridge.system import SystemMonitor
from btkit.sim_a2dp import SimulatedA2dpSource

DEV = "Lab Bridge"
BROKER = dict(mqtt_enabled=True, mqtt_host="mosquitto", mqtt_port=1884, mqtt_username="bridge")
READ_ONLY = {"Playback": "sensor", "Bluetooth connected": "binary_sensor", "Connected phone": "sensor", "Now playing": "sensor",
             "Pairing window open": "binary_sensor", "Bridge playback allowed": "binary_sensor", "Lyrion": "sensor",
             "Overheating": "binary_sensor", "Thermal state": "sensor", "CPU temperature": "sensor", "CPU load": "sensor",
             "Memory used": "sensor", "Storage used": "sensor", "Uptime": "sensor"}


@pytest.fixture(scope="module")
def ha():
    h = HA()
    h.wait_up()
    h.onboard()
    h.add_mqtt("mosquitto", 1884, "bridge", "lab-only-mqtt-password")
    return h


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("bridge")


@pytest.fixture(scope="module")
def thermal(tmp_path_factory):
    p = tmp_path_factory.mktemp("thermal") / "temp"
    p.write_text("45000")
    return p


def start(data_dir, thermal, telemetry_s=3600, **settings):
    svc = ApplianceService(str(data_dir), source=SimulatedA2dpSource(), stream_port=0, advertise_host="bridge", echo=None,
                           auth_iterations=1000, monitor=SystemMonitor(thermal=str(thermal), data_dir=str(data_dir)),
                           mqtt_factory=lambda s: MqttBridge(s, telemetry_s=telemetry_s))
    svc.store.set_secret("mqtt_password", "lab-only-mqtt-password")
    read_only = {"mqtt_allow_pairing": False, "mqtt_allow_audio": False, "mqtt_allow_test": False}   # product default unless asked
    svc.store.update({"device_name": DEV, **BROKER, **read_only, **settings})
    svc.start()
    return svc


def wait_online(ha, svc, names, timeout=40):
    def ok():
        m = ha.entity_map(DEV, svc.instance_id())
        return m if all(n in m and ha.state(m[n])["state"] != "unavailable" for n in names) else None
    return ha.wait(ok, timeout)


def test_01_read_only_discovery_creates_the_right_entities_on_one_device(ha, data_dir, thermal):
    svc = start(data_dir, thermal)
    try:
        m = wait_online(ha, svc, READ_ONLY)
        assert m, ha.entity_map(DEV, svc.instance_id())
        assert {n: e.split(".")[0] for n, e in m.items()} == READ_ONLY               # exactly these, nothing writable
        assert not any(e.startswith(("switch.", "button.")) for e in ha.mqtt_entities())
        devices = {ha.template("{{ device_attr('" + e + "', 'name') }}") for e in m.values()}
        assert devices == {DEV}
        e = m["CPU temperature"]
        assert ha.template("{{ device_attr('" + e + "', 'model') }}") == "Hi-Fi Bridge"
        a = ha.state(e)["attributes"]
        assert a["unit_of_measurement"] == "°C" and a["device_class"] == "temperature" and a["state_class"] == "measurement"
        assert ha.state(m["Uptime"])["attributes"]["device_class"] == "duration"
        assert ha.state(m["Overheating"])["attributes"]["device_class"] == "heat" and ha.state(m["Overheating"])["state"] == "off"
        assert ha.state(m["Bluetooth connected"])["attributes"]["device_class"] == "connectivity"
        assert float(ha.state(m["CPU temperature"])["state"]) == 45.0
        assert ha.state(m["Playback"])["state"] == "not_configured"
        cat = ha.template("{{ state_attr('" + m["CPU temperature"] + "', 'friendly_name') }}")
        assert cat == f"{DEV} CPU temperature"
    finally:
        svc.stop()


def test_02_availability_clean_stop_last_will_and_reconnect(ha, data_dir, thermal):
    svc = start(data_dir, thermal)
    m = wait_online(ha, svc, ["Playback"])
    svc.stop()                                                                          # clean stop -> "offline" published
    assert ha.wait(lambda: ha.state(m["Playback"])["state"] == "unavailable", 20)
    svc = start(data_dir, thermal)
    assert wait_online(ha, svc, ["Playback", "CPU load"])
    svc.mqtt.client.backoff = (8.0, 8.0)                                                # keep the outage visible long enough for HA
    svc.mqtt.client._close()                                                            # abrupt loss: no DISCONNECT -> broker's last will
    assert ha.wait(lambda: ha.state(m["Playback"])["state"] == "unavailable", 8, 0.3)   # HA saw the LAST WILL (not a clean offline)
    assert ha.wait(lambda: svc.mqtt.client.connects >= 2, 30)                         # automatic reconnect ...
    assert wait_online(ha, svc, ["Playback", "CPU load"])                              # ... and available again
    svc.stop()


def test_03_home_assistant_restart_restores_entities_and_states(ha, data_dir, thermal):
    svc = start(data_dir, thermal)
    try:
        m = wait_online(ha, svc, READ_ONLY)
        ha.call("POST", "/api/services/homeassistant/restart", {})
        time.sleep(5)
        ha.wait_up()
        m2 = wait_online(ha, svc, READ_ONLY, timeout=90)
        assert m2 and set(m2) == set(m) and float(ha.state(m2["CPU temperature"])["state"]) == 45.0     # retained discovery + state
    finally:
        svc.stop()


def test_04_enabling_one_control_swaps_entities_and_removes_the_stale_one(ha, data_dir, thermal):
    svc = start(data_dir, thermal, mqtt_allow_pairing=True)
    try:
        m = ha.wait(lambda: (lambda x: x if "Pairing window" in x and "Pairing window open" not in x else None)(ha.entity_map(DEV, svc.instance_id())), 40)
        assert m, ha.entity_map(DEV, svc.instance_id())
        assert m["Pairing window"].startswith("switch.") and "Bridge playback" not in m and "Test tone" not in m
        assert "Bridge playback allowed" in m                                           # audio control still read-only
        # A user can only switch an AVAILABLE entity: wait until HA has processed the retained 'online' after creating the entity
        # (switching in between was a race of this test on a slow CI runner, 2026-11-17; the bridge subscribes before it announces).
        assert ha.wait(lambda: (ha.state(m["Pairing window"]) or {}).get("state") in ("on", "off"), 20)
        ha.service("switch", "turn_on", {"entity_id": m["Pairing window"]})
        assert ha.wait(lambda: svc.runtime.pairing.is_open(), 10)                       # the HA switch really opened the pairing window
        assert ha.wait(lambda: ha.state(m["Pairing window"])["state"] == "on", 10)
        ha.service("switch", "turn_off", {"entity_id": m["Pairing window"]})
        assert ha.wait(lambda: not svc.runtime.pairing.is_open(), 10)
        from btbridge.mqtt.client import MqttClient
        pub = MqttClient("mosquitto", 1884, client_id="lab-attacker", username="bridge", password="lab-only-mqtt-password"); pub.start()
        assert ha.wait(lambda: pub.connected, 10)
        pub.publish(f"btbridge/{svc.instance_id()}/cmd/audio", "OFF"); time.sleep(2)
        assert svc.store.get("audio_allowed") is True                                  # not enabled -> ignored
        pub.stop()
    finally:
        svc.stop()
    svc = start(data_dir, thermal, mqtt_allow_pairing=False)                             # disable again
    try:
        m = ha.wait(lambda: (lambda x: x if "Pairing window open" in x and "Pairing window" not in x else None)(ha.entity_map(DEV, svc.instance_id())), 40)
        assert m and not any(e.startswith("switch.") for e in ha.mqtt_entities())       # stale switch removed from HA
    finally:
        svc.stop()


def test_05_all_controls_enabled_work_from_home_assistant(ha, data_dir, thermal):
    svc = start(data_dir, thermal, mqtt_allow_pairing=True, mqtt_allow_audio=True, mqtt_allow_test=True)
    try:
        # wait until the entities are AVAILABLE, not just registered: a command sent while HA still shows them unavailable can be lost
        m = wait_online(ha, svc, ["Pairing window", "Bridge playback", "Test tone"])
        assert m and m["Test tone"].startswith("button.") and m["Bridge playback"].startswith("switch.")
        ha.service("switch", "turn_off", {"entity_id": m["Bridge playback"]})
        assert ha.wait(lambda: svc.store.get("audio_allowed") is False, 10)
        ha.service("switch", "turn_on", {"entity_id": m["Bridge playback"]})
        assert ha.wait(lambda: svc.store.get("audio_allowed") is True, 10)
        ha.service("button", "press", {"entity_id": m["Test tone"]})
        assert ha.wait(lambda: ("test", "PRESS", True) in svc.mqtt.commands, 10)       # executed (refused by the bridge: not configured)
    finally:
        svc.stop()
    svc = start(data_dir, thermal)                                                       # back to the read-only default
    try:
        ok = ha.wait(lambda: not any(e.startswith(("switch.", "button.")) for e in ha.mqtt_entities()), 40)
        left = [(e, (ha.state(e) or {}).get("state")) for e in ha.mqtt_entities() if e.startswith(("switch.", "button."))]
        assert ok, left
    finally:
        svc.stop()


def test_06_regular_telemetry(ha, data_dir, thermal):
    svc = start(data_dir, thermal, telemetry_s=3)                                        # test override; product default 300 s
    try:
        m = wait_online(ha, svc, ["Uptime"])
        first = int(float(ha.state(m["Uptime"])["state"]))
        assert ha.wait(lambda: int(float(ha.state(m["Uptime"])["state"])) > first, 30)  # periodic system publish reaches HA
    finally:
        svc.stop()


def test_07_over_temperature_is_published_at_once_independent_of_telemetry(ha, data_dir, thermal):
    svc = start(data_dir, thermal, telemetry_s=3600)                                     # telemetry far away: must not matter
    try:
        m = wait_online(ha, svc, ["Overheating", "Thermal state"])
        assert ha.state(m["Overheating"])["state"] == "off"
        t0 = time.time()
        thermal.write_text("84000")
        assert ha.wait(lambda: ha.state(m["Overheating"])["state"] == "on", 30, 0.5)
        dt = time.time() - t0
        assert ha.state(m["Thermal state"])["state"] == "critical"
        from btbridge.service import THERMAL_CHECK_S
        assert dt <= THERMAL_CHECK_S + 5, dt                                             # within one local check interval (+ HA latency)
        thermal.write_text("50000")
        assert ha.wait(lambda: ha.state(m["Overheating"])["state"] == "off", 30, 0.5)
        print(f"over-temperature visible in Home Assistant after {dt:.1f} s")
        import json as _j, os as _o
        _o.makedirs("/work/measurements", exist_ok=True)
        _j.dump({"home_assistant": "2026.11.1", "mosquitto": "2.0.20", "over_temperature_visible_in_ha_s": round(dt, 2),
                 "thermal_check_interval_s": THERMAL_CHECK_S, "telemetry_interval_in_test_s": 3600,
                 "entities_read_only_default": sorted(READ_ONLY),
                 "label": "INTEGRATION real Home Assistant + real Mosquitto; SIMULATED phone; temperature from a fake sysfs file"},
                open("/work/measurements/homeassistant_integration.json", "w"), indent=1)
    finally:
        thermal.write_text("45000")
        svc.stop()
