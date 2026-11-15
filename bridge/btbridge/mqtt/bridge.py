"""MQTT bridge with Home Assistant MQTT discovery (optional; audio never depends on it - DECISIONS #11).

Topics (``<base>`` = setting ``mqtt_base_topic``, ``<id>`` = persistent instance id):
  <base>/<id>/availability   online / offline (retained; offline is the broker's last will)
  <base>/<id>/state          JSON: headline, bluetooth/media/ownership/lyrion state, device, now playing, pairing, audio (retained)
  <base>/<id>/system         JSON telemetry: cpu, temperature, memory, storage, uptime (every 5 min, at start, and at once on a
                             thermal state change)
  <base>/<id>/cmd/pairing    ON / OFF      -> open / close the pairing window
  <base>/<id>/cmd/audio      ON / OFF      -> allow / block bridge playback (only bridge-owned playback is ever released)
  <base>/<id>/cmd/test       PRESS         -> diagnostic test tone (refuses on a busy target)
Discovery configs go to ``<prefix>/<component>/<id>/<object>/config`` (retained)."""
from __future__ import annotations

import json
import threading
import time

from .. import __version__
from .client import MqttClient

STATE_FIELDS = ("headline", "bluetooth_connection", "bt_media_activity", "target_ownership", "lyrion_reachability", "audio_permission")


def entities(base: str, iid: str) -> list[tuple[str, str, dict]]:
    st, sy, av = f"{base}/{iid}/state", f"{base}/{iid}/system", f"{base}/{iid}/availability"
    E = []

    def add(component, obj, name, **kw):
        cfg = {"name": name, "unique_id": f"btbridge_{iid}_{obj}", "object_id": f"btbridge_{iid}_{obj}",
               "availability_topic": av, "payload_available": "online", "payload_not_available": "offline", **kw}
        E.append((component, obj, cfg))
    add("sensor", "playback", "Playback", state_topic=st, value_template="{{ value_json.headline }}", icon="mdi:speaker-bluetooth")
    add("binary_sensor", "bluetooth", "Bluetooth connected", state_topic=st, device_class="connectivity",
        value_template="{{ 'ON' if value_json.bluetooth_connection == 'connected' else 'OFF' }}")
    add("sensor", "device", "Connected device", state_topic=st, value_template="{{ value_json.device or 'none' }}", icon="mdi:cellphone")
    add("sensor", "now_playing", "Now playing", state_topic=st, value_template="{{ value_json.now_playing or '' }}", icon="mdi:music")
    add("sensor", "lyrion", "Lyrion", state_topic=st, value_template="{{ value_json.lyrion_reachability }}", icon="mdi:server-network")
    add("switch", "pairing", "Pairing", state_topic=st, command_topic=f"{base}/{iid}/cmd/pairing",
        value_template="{{ 'ON' if value_json.pairing_open else 'OFF' }}", icon="mdi:bluetooth-connect")
    add("switch", "audio", "Bridge audio", state_topic=st, command_topic=f"{base}/{iid}/cmd/audio",
        value_template="{{ 'ON' if value_json.audio_permission == 'allowed' else 'OFF' }}", icon="mdi:volume-high")
    add("button", "test", "Test sound", command_topic=f"{base}/{iid}/cmd/test", payload_press="PRESS", icon="mdi:music-note")
    add("sensor", "temperature", "CPU temperature", state_topic=sy, unit_of_measurement="°C", device_class="temperature",
        state_class="measurement", value_template="{{ value_json.temperature_c }}")
    add("sensor", "thermal", "Thermal state", state_topic=sy, value_template="{{ value_json.thermal_state }}", icon="mdi:thermometer-alert")
    add("sensor", "cpu", "CPU load", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.cpu_percent }}", icon="mdi:cpu-64-bit")
    add("sensor", "memory", "Memory used", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.memory_used_percent }}", icon="mdi:memory")
    add("sensor", "storage", "Storage used", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.storage_used_percent }}", icon="mdi:harddisk")
    add("sensor", "uptime", "Uptime", state_topic=sy, unit_of_measurement="s", device_class="duration",
        value_template="{{ value_json.uptime_s }}", entity_category="diagnostic")
    return E


class MqttBridge:
    def __init__(self, service, client_factory=MqttClient, telemetry_s: float | None = None):
        from ..service import TELEMETRY_S
        self.svc, self.telemetry_s = service, telemetry_s or TELEMETRY_S
        st = service.store
        self.base, self.prefix, self.iid = st.get("mqtt_base_topic"), st.get("mqtt_discovery_prefix"), service.instance_id()
        self.topic = lambda leaf: f"{self.base}/{self.iid}/{leaf}"
        self.client = client_factory(
            st.get("mqtt_host"), st.get("mqtt_port"), client_id=f"btbridge-{self.iid}", username=st.get("mqtt_username") or None,
            password=st.secret("mqtt_password") or None, tls=st.get("mqtt_tls"), will=(self.topic("availability"), b"offline", True),
            subscriptions=[self.topic("cmd/#")], on_message=self._on_message, on_connect=self._on_connect, log=service.logs)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.commands: list[tuple[str, str]] = []
        service.on_event(self._on_event)

    # ---- lifecycle ----
    def start(self) -> None:
        self.client.start()
        self._thread = threading.Thread(target=self._telemetry_loop, daemon=True, name="mqtt-telemetry")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.client.connected:
            self.client.publish(self.topic("availability"), "offline", retain=True)
        self.client.stop()

    def state(self) -> dict:
        s = self.client.state()
        return {"enabled": True, "connected": s["connected"], "last_error": s["last_error"], "broker":
                f"{self.svc.store.get('mqtt_host')}:{self.svc.store.get('mqtt_port')}"}

    # ---- publishing ----
    def device(self) -> dict:
        return {"identifiers": [f"btbridge_{self.iid}"], "name": self.svc.store.get("device_name"), "manufacturer": "Bluetooth Audio Bridge",
                "model": "Bluetooth to Lyrion bridge", "sw_version": __version__}

    def discovery(self) -> list[tuple[str, dict]]:
        out = []
        for component, obj, cfg in entities(self.base, self.iid):
            out.append((f"{self.prefix}/{component}/{self.iid}/{obj}/config", dict(cfg, device=self.device())))
        return out

    def state_payload(self, st: dict | None = None) -> dict:
        st = st or self.svc.status()
        b = st["bridge"]
        dev = st["bluetooth"]["connected_device"]
        return {"headline": st["headline"], **{k: b[k] for k in STATE_FIELDS[1:]}, "device": dev["name"] if dev else None,
                "now_playing": st["now_playing"], "pairing_open": st["bluetooth"]["pairing_open"],
                "pairing_remaining_s": st["bluetooth"]["pairing_remaining_s"], "degraded": st["degraded"]}

    def system_payload(self, sysd: dict | None = None) -> dict:
        s = sysd or self.svc.last_system or {}
        return {"cpu_percent": s.get("cpu_percent"), "temperature_c": s.get("temperature_c"), "thermal_state": s.get("thermal_state"),
                "memory_used_percent": (s.get("memory") or {}).get("used_percent"),
                "storage_used_percent": (s.get("storage") or {}).get("used_percent"), "uptime_s": s.get("uptime_s"),
                "load": s.get("load")}

    def publish_state(self, st=None) -> None:
        self.client.publish(self.topic("state"), json.dumps(self.state_payload(st)), retain=True)

    def publish_system(self, sysd=None) -> None:
        self.client.publish(self.topic("system"), json.dumps(self.system_payload(sysd)), retain=True)

    def _on_connect(self) -> None:
        for topic, cfg in self.discovery():
            self.client.publish(topic, json.dumps(cfg), retain=True)
        self.client.publish(self.topic("availability"), "online", retain=True)
        self.publish_state()
        self.publish_system()                 # initial report after (re)connect

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "settings_changed" and "device_name" in data.get("keys", ()):
            for topic, cfg in self.discovery():                # HA device name follows the Bluetooth name
                self.client.publish(topic, json.dumps(cfg), retain=True)
        if kind == "status_changed":
            self.publish_state(data)
        elif kind == "thermal":
            self.publish_system(data)        # immediate on thermal state change (critical warning)
            self.publish_state()

    def _telemetry_loop(self) -> None:
        while not self._stop.wait(self.telemetry_s):
            self.publish_system()

    # ---- commands ----
    def _on_message(self, topic: str, payload: bytes) -> None:
        cmd = topic.rsplit("/", 1)[-1]
        val = payload.decode("utf-8", "replace").strip().upper()
        self.commands.append((cmd, val))
        if cmd == "pairing" and val in ("ON", "OFF"):
            (self.svc.open_pairing if val == "ON" else self.svc.close_pairing)()
        elif cmd == "audio" and val in ("ON", "OFF"):
            self.svc.set_audio_allowed(val == "ON")
        elif cmd == "test" and val == "PRESS":
            self.svc.diagnostic_test()
        else:
            self.svc.logs(f"mqtt: ignored command {cmd}={val[:20]}")
            return
        self.publish_state()
