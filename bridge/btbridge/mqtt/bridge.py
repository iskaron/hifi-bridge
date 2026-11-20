"""MQTT bridge with Home Assistant MQTT discovery - optional; audio never depends on it (DECISIONS #11).

READ-ONLY BY DEFAULT (DECISIONS #36).  Control functions are enabled one by one in the settings and are OFF by default:
  mqtt_allow_pairing -> switch "Pairing window"     (else: read-only binary_sensor "Pairing window open")
  mqtt_allow_audio   -> switch "Bridge playback"    (else: read-only binary_sensor "Bridge playback allowed")
  mqtt_allow_test    -> button "Test tone"          (else: no entity)
The entity variant that is NOT active is removed from Home Assistant by publishing an empty retained discovery config, so toggling an
option never leaves stale entities. The bridge subscribes to command topics only for enabled functions and re-checks the setting for every
received command.

Topics (<base> = mqtt_base_topic, <id> = persistent instance id):
  <base>/<id>/availability   online / offline (retained; offline is also the broker's last will)
  <base>/<id>/state          JSON playback/connection state (retained; on every change)
  <base>/<id>/system         JSON telemetry (retained; every mqtt_telemetry_s, default 300 s, and on (re)connect)
  <base>/<id>/thermal        JSON {"state": ok|warning|critical|unknown, "temperature_c": ...} (retained; at once on every thermal state
                             change - independent of the telemetry cycle; the service checks the temperature every THERMAL_CHECK_S)
  <base>/<id>/cmd/<name>     commands, only for enabled functions
"""
from __future__ import annotations

import json
import ssl
import threading

from .. import PRODUCT_NAME, __version__
from .client import MqttClient

STATE_FIELDS = ("headline", "bluetooth_connection", "bt_media_activity", "target_ownership", "lyrion_reachability", "audio_permission")
COMMANDS = {"pairing": "mqtt_allow_pairing", "audio": "mqtt_allow_audio", "test": "mqtt_allow_test"}


def entities(base: str, iid: str, allow: dict[str, bool]) -> list[tuple[str, str, dict | None]]:
    """(component, object_id, config or None). None = publish an empty config (remove the entity from Home Assistant)."""
    st, sy, th, av = (f"{base}/{iid}/{x}" for x in ("state", "system", "thermal", "availability"))
    E: list[tuple[str, str, dict | None]] = []

    def add(component, obj, name, enabled=True, **kw):
        cfg = {"name": name, "unique_id": f"hifibridge_{iid}_{obj}", "object_id": f"hifibridge_{iid}_{obj}",
               "availability_topic": av, "payload_available": "online", "payload_not_available": "offline", **kw}
        E.append((component, obj, cfg if enabled else None))

    # --- status (always) ---
    add("sensor", "playback", "Playback", state_topic=st, value_template="{{ value_json.headline }}", icon="mdi:speaker-wireless")
    add("binary_sensor", "bluetooth", "Bluetooth connected", state_topic=st, device_class="connectivity",
        value_template="{{ 'ON' if value_json.bluetooth_connection == 'connected' else 'OFF' }}")
    add("sensor", "device", "Connected phone", state_topic=st, value_template="{{ value_json.device or 'none' }}", icon="mdi:cellphone")
    add("sensor", "now_playing", "Now playing", state_topic=st, value_template="{{ value_json.now_playing or '' }}", icon="mdi:music")
    # --- pairing / playback: switch when allowed, otherwise read-only binary sensor ---
    add("switch", "pairing", "Pairing window", allow["pairing"], state_topic=st, command_topic=f"{base}/{iid}/cmd/pairing",
        value_template="{{ 'ON' if value_json.pairing_open else 'OFF' }}", icon="mdi:bluetooth-connect")
    add("binary_sensor", "pairing_state", "Pairing window open", not allow["pairing"], state_topic=st,
        value_template="{{ 'ON' if value_json.pairing_open else 'OFF' }}", icon="mdi:bluetooth-connect")
    add("switch", "audio", "Bridge playback", allow["audio"], state_topic=st, command_topic=f"{base}/{iid}/cmd/audio",
        value_template="{{ 'ON' if value_json.audio_permission == 'allowed' else 'OFF' }}", icon="mdi:volume-high")
    add("binary_sensor", "audio_state", "Bridge playback allowed", not allow["audio"], state_topic=st,
        value_template="{{ 'ON' if value_json.audio_permission == 'allowed' else 'OFF' }}", icon="mdi:volume-high")
    add("button", "test", "Test tone", allow["test"], command_topic=f"{base}/{iid}/cmd/test", payload_press="PRESS", icon="mdi:music-note")
    # --- diagnostics ---
    add("sensor", "lyrion", "Lyrion", state_topic=st, value_template="{{ value_json.lyrion_reachability }}", icon="mdi:server-network",
        entity_category="diagnostic")
    add("binary_sensor", "overheating", "Overheating", state_topic=th, device_class="heat",
        value_template="{{ 'ON' if value_json.state == 'critical' else 'OFF' }}", entity_category="diagnostic")
    add("sensor", "thermal", "Thermal state", state_topic=th, value_template="{{ value_json.state }}", icon="mdi:thermometer-alert",
        entity_category="diagnostic")
    # --- system telemetry ---
    add("sensor", "temperature", "CPU temperature", state_topic=sy, unit_of_measurement="°C", device_class="temperature",
        state_class="measurement", value_template="{{ value_json.temperature_c }}", entity_category="diagnostic")
    add("sensor", "cpu", "CPU load", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.cpu_percent }}", icon="mdi:cpu-64-bit", entity_category="diagnostic")
    add("sensor", "memory", "Memory used", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.memory_used_percent }}", icon="mdi:memory", entity_category="diagnostic")
    add("sensor", "storage", "Storage used", state_topic=sy, unit_of_measurement="%", state_class="measurement",
        value_template="{{ value_json.storage_used_percent }}", icon="mdi:harddisk", entity_category="diagnostic")
    add("sensor", "uptime", "Uptime", state_topic=sy, unit_of_measurement="s", device_class="duration", state_class="measurement",
        value_template="{{ value_json.uptime_s }}", entity_category="diagnostic")
    return E


def tls_context(ca_pem: str) -> ssl.SSLContext:
    """Broker verification is never disabled: system CAs, or the owner's CA certificate (PEM) when given."""
    ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = True, ssl.CERT_REQUIRED
    return ctx


class MqttBridge:
    def __init__(self, service, client_factory=MqttClient, telemetry_s: float | None = None):
        self.svc = service
        st = service.store
        self.telemetry_s = telemetry_s or st.get("mqtt_telemetry_s")
        self.base, self.prefix, self.iid = st.get("mqtt_base_topic"), st.get("mqtt_discovery_prefix"), service.instance_id()
        self.allow = {name: bool(st.get(key)) for name, key in COMMANDS.items()}
        self.topic = lambda leaf: f"{self.base}/{self.iid}/{leaf}"
        subs = [self.topic(f"cmd/{name}") for name, on in self.allow.items() if on]
        self.client = client_factory(
            st.get("mqtt_host"), st.get("mqtt_port"), client_id=f"hifibridge-{self.iid}", username=st.get("mqtt_username") or None,
            password=st.secret("mqtt_password") or None, tls=st.get("mqtt_tls"),
            tls_context=tls_context(st.get("mqtt_ca_pem")) if st.get("mqtt_tls") else None,
            will=(self.topic("availability"), b"offline", True), subscriptions=subs,
            on_message=self._on_message, on_connect=self._on_connect, log=service.logs)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        from ..core.bounded import BoundedList
        self.commands: list[tuple[str, str, bool]] = BoundedList(200)   # (command, value, executed)
        service.on_event(self._on_event)

    # ---- lifecycle ----
    def start(self) -> None:
        self.client.start()
        self._thread = threading.Thread(target=self._telemetry_loop, daemon=True, name="mqtt-telemetry")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        off = getattr(self.svc, "off_event", None)
        if off is not None:
            off(self._on_event)                          # a restarted bridge must not leave its listener behind (review L1)
        if self.client.connected:
            # the retained state must not keep the last phone and title (DECISIONS #124); a crash leaves them, but the broker's last
            # will marks every entity unavailable and the next start publishes the real state
            try:
                st = self.state_payload()
            except Exception:                            # a half-stopped service must not prevent the goodbye
                st = {}
            st.update(device=None, now_playing=None, bluetooth_connection="disconnected", bt_media_activity="idle")
            self.client.publish(self.topic("state"), json.dumps(st), retain=True)
            self.client.publish(self.topic("availability"), "offline", retain=True)
        self.client.stop()

    def state(self) -> dict:
        s = self.client.state()
        return {"enabled": True, "connected": s["connected"], "last_error": s["last_error"],
                "broker": f"{self.svc.store.get('mqtt_host')}:{self.svc.store.get('mqtt_port')}",
                "commands": [name for name, on in self.allow.items() if on]}

    # ---- publishing ----
    def device(self) -> dict:
        return {"identifiers": [f"hifibridge_{self.iid}"], "name": self.svc.store.get("device_name"), "manufacturer": "Hi-Fi Bridge project",
                "model": PRODUCT_NAME, "sw_version": __version__}

    def discovery(self) -> list[tuple[str, dict | None]]:
        out = []
        for component, obj, cfg in entities(self.base, self.iid, self.allow):
            out.append((f"{self.prefix}/{component}/{self.iid}/{obj}/config", dict(cfg, device=self.device()) if cfg else None))
        return out

    def publish_discovery(self) -> None:
        for topic, cfg in self.discovery():
            self.client.publish(topic, json.dumps(cfg) if cfg else "", retain=True)     # "" removes a stale/disabled entity

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

    def thermal_payload(self, th: dict | None = None) -> dict:
        th = th or self.svc.thermal()
        return {"state": th.get("state", "unknown"), "temperature_c": th.get("temperature_c")}

    def publish_state(self, st=None) -> None:
        self.client.publish(self.topic("state"), json.dumps(self.state_payload(st)), retain=True)

    def publish_system(self, sysd=None) -> None:
        self.client.publish(self.topic("system"), json.dumps(self.system_payload(sysd)), retain=True)

    def publish_thermal(self, th=None) -> None:
        self.client.publish(self.topic("thermal"), json.dumps(self.thermal_payload(th)), retain=True)

    def _on_connect(self) -> None:
        self.publish_discovery()
        self.client.publish(self.topic("availability"), "online", retain=True)
        self.publish_state()
        self.publish_thermal()
        self.publish_system()                 # initial report after every (re)connect

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "settings_changed" and "device_name" in data.get("keys", ()):
            self.publish_discovery()           # HA device name follows the device name
        if kind == "status_changed":
            self.publish_state(data)
        elif kind == "thermal":                # immediate - NOT tied to the telemetry cycle
            self.publish_thermal(data)
            self.publish_state()

    def _telemetry_loop(self) -> None:
        while not self._stop.wait(self.telemetry_s):
            self.publish_system()

    # ---- commands (only for explicitly enabled functions) ----
    def _on_message(self, topic: str, payload: bytes) -> None:
        cmd = topic.rsplit("/", 1)[-1]
        val = payload.decode("utf-8", "replace").strip().upper()
        key = COMMANDS.get(cmd)
        if key is None or not self.svc.store.get(key):
            self.commands.append((cmd, val[:20], False))
            self.svc.logs(f"mqtt: command {cmd!r} ignored (not enabled)")
            return
        ok = True
        if cmd == "pairing" and val in ("ON", "OFF"):
            (self.svc.open_pairing if val == "ON" else self.svc.close_pairing)()
        elif cmd == "audio" and val in ("ON", "OFF"):
            self.svc.set_audio_allowed(val == "ON")
        elif cmd == "test" and val == "PRESS":
            self.svc.diagnostic_test()
        else:
            ok = False
            self.svc.logs(f"mqtt: invalid value for {cmd}: {val[:20]}")
        self.commands.append((cmd, val[:20], ok))
        if ok:
            self.publish_state()
