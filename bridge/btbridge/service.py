"""Appliance service: composes persistent configuration, the audio bridge runtime, the source adapter (BlueZ or simulated), owner
authentication, system monitoring, logs/diagnostics and the optional MQTT bridge.  The WebUI and MQTT talk ONLY to this class.

Safety properties kept from phase 1 (tests: tests/unit/test_service.py):
  * every owner action goes through the Controller's ownership rules (no generic stop, URL-addressed release, no power-off);
  * without a configured LMS host AND target player the bridge talks to an ``UnconfiguredLms`` that is always unreachable, so an empty
    player id can never reach LMS as a command target;
  * settings are applied in place (no runtime rebuild), so a connected phone stays connected;
  * the stream token never appears in status, logs or diagnostics.
"""
from __future__ import annotations

import collections
import json
import os
import platform
import secrets
import socket
import threading
import time
from typing import Callable

from . import __version__
from .app import BridgeRuntime, RuntimeConfig
from .config import ConfigStore
from .core.controller import Config
from .lms import discovery
from .lms.client import LmsClient, LmsUnreachable, Observation
from .stream.server import redact
from .system import SystemMonitor
from .webui.auth import Auth

TELEMETRY_S = 300          # MQTT/system metrics every 5 min (DECISIONS #11, #36)
SYSTEM_SAMPLE_S = 30       # full system sample for the UI
THERMAL_CHECK_S = 10       # fast, independent over-temperature check (DECISIONS #36): a warning never waits for telemetry


class UnconfiguredLms:
    """Stand-in until server AND target are configured: every call is 'unreachable', nothing is ever sent."""
    commands_sent: list = []
    command_times: list = []

    def __init__(self):
        self.commands_sent, self.command_times = [], []

    def _no(self, *a, **k):
        raise LmsUnreachable("not configured")

    observe = play = release = power_on = volume_delta = server_version = players = sync_groups = targets = _no


def lms_url(host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"http://{h}:{port}"


def local_ip_for(host: str, port: int) -> str:
    """The local address the bridge would use to reach ``host`` (= what LMS/players must use to reach the stream). No packet is sent."""
    try:
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class LogBuffer:
    """Recent log lines in RAM (bounded) + persistent critical faults (bounded file), always token-redacted."""

    def __init__(self, data_dir: str, size: int = 600, faults_keep: int = 200, echo: Callable[[str], None] | None = print):
        self.lines: collections.deque = collections.deque(maxlen=size)
        self.faults_path, self.faults_keep, self.echo = os.path.join(data_dir, "faults.jsonl"), faults_keep, echo
        self._lock = threading.Lock()
        self.secrets: list[str] = []

    def _clean(self, msg: str) -> str:
        m = redact(str(msg))
        for s in self.secrets:
            if s:
                m = m.replace(s, "***")
        return m

    def __call__(self, msg: str) -> None:
        m = self._clean(msg)
        with self._lock:
            self.lines.append((time.time(), m))
        if self.echo:
            self.echo(m)

    def fault(self, kind: str, detail: str) -> None:
        self(f"FAULT {kind}: {detail}")
        rec = json.dumps({"t": time.time(), "kind": kind, "detail": self._clean(detail)})
        with self._lock:
            try:
                old = open(self.faults_path).read().splitlines()[-(self.faults_keep - 1):] if os.path.exists(self.faults_path) else []
                tmp = self.faults_path + ".tmp"
                with open(tmp, "w") as fh:
                    fh.write("\n".join(old + [rec]) + "\n")
                os.replace(tmp, self.faults_path)
            except OSError:
                pass

    def recent(self, n: int = 200) -> list[tuple[float, str]]:
        with self._lock:
            return list(self.lines)[-n:]

    def faults(self) -> list[dict]:
        try:
            return [json.loads(x) for x in open(self.faults_path).read().splitlines() if x.strip()]
        except (OSError, ValueError):
            return []


class ApplianceService:
    def __init__(self, data_dir: str, source=None, stream_port: int = 8090, advertise_host: str = "auto",
                 lms_factory: Callable[[str], object] = LmsClient, monitor: SystemMonitor | None = None,
                 mqtt_factory: Callable | None = None, discover: Callable = discovery.discover, echo=print, auth_iterations: int | None = None):
        self.logs = LogBuffer(data_dir, echo=echo)
        self.store = ConfigStore(data_dir, log=self.logs)
        self.logs.secrets = [self.store.secret("stream_token") or ""]
        self.auth = Auth(self.store, log=self.logs, **({"iterations": auth_iterations} if auth_iterations else {}))
        self.lms_factory, self.discover, self.advertise_cfg = lms_factory, discover, advertise_host
        self.monitor = monitor or SystemMonitor(data_dir=data_dir)
        self.mqtt_factory, self.mqtt = mqtt_factory, None
        c = self.store.all()
        rc = RuntimeConfig(lms_url=lms_url(c["lms_host"], c["lms_port"]) if c["lms_host"] else "http://127.0.0.1:9",
                           advertise_host=self._advertise(c), stream_port=stream_port, stream_format=c["stream_format"],
                           icy_metadata=c["icy_metadata"], pairing_s=float(c["pairing_seconds"]))
        cc = Config(target_player=c["target_player"], audio_allowed=c["audio_allowed"], override_lms=c["override_lms"],
                    pause_strategy=c["pause_strategy"])
        self.runtime = BridgeRuntime(rc, cc, source=source, lms=self._lms_for(c), log=self.logs, token=self.store.secret("stream_token"))
        self.source = source
        self.store.on_change(self._apply)
        self._listeners: list[Callable[[str, dict], None]] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_key = None
        self.started_at = time.time()
        self.last_system: dict = {}
        self._thermal = {"state": "unknown", "temperature_c": None, "checked_at": None}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.runtime.start()
        if self.source is not None and hasattr(self.source, "set_alias"):
            try:
                self.source.set_alias(self.store.get("device_name"))
            except Exception as e:
                self.logs(f"could not set Bluetooth name: {type(e).__name__}: {e}")
        self.last_system = self.monitor.sample()
        self._threads = [threading.Thread(target=self._watch, daemon=True, name="service-watch")]
        for t in self._threads:
            t.start()
        self._restart_mqtt()
        self.logs(f"service started (version {__version__})")

    def stop(self) -> None:
        self._stop.set()
        if self.mqtt:
            self.mqtt.stop()
        self.runtime.stop()

    # ------------------------------------------------------------------ config -> runtime
    def _lms_for(self, c: dict):
        if not c["lms_host"] or not c["target_player"]:
            return UnconfiguredLms()
        return self.lms_factory(lms_url(c["lms_host"], c["lms_port"]))

    def _advertise(self, c: dict) -> str:
        if self.advertise_cfg != "auto":
            return self.advertise_cfg
        return local_ip_for(c["lms_host"], c["lms_port"]) if c["lms_host"] else "127.0.0.1"

    def _apply(self, ch: dict) -> None:
        rt, ctl = self.runtime, self.runtime.controller
        c = self.store.all()
        if "device_name" in ch and self.source is not None and hasattr(self.source, "set_alias"):
            try:
                self.source.set_alias(ch["device_name"])
            except Exception as e:
                self.logs(f"could not set Bluetooth name: {type(e).__name__}: {e}")
        if "pairing_seconds" in ch:
            rt.pairing.duration = float(ch["pairing_seconds"])
        with ctl.lock:
            if "override_lms" in ch:
                ctl.cfg.override_lms = ch["override_lms"]
            if "pause_strategy" in ch:
                ctl.cfg.pause_strategy = ch["pause_strategy"]
        if "audio_allowed" in ch:
            ctl.set_audio_allowed(ch["audio_allowed"])
        if "stream_format" in ch or "icy_metadata" in ch:
            rt.reconfigure_stream(ch.get("stream_format"), ch.get("icy_metadata"))
        if {"lms_host", "lms_port"} & set(ch) or ("target_player" in ch and (not c["target_player"] or isinstance(rt.lms, UnconfiguredLms))):
            rt.rcfg.advertise_host = self._advertise(c)
            if "target_player" in ch:
                with ctl.lock:
                    ctl._release("target_change")
                    ctl.cfg.target_player = c["target_player"]
            rt.set_lms(self._lms_for(c))
        elif "target_player" in ch:
            ctl.set_target(c["target_player"])
        if any(k.startswith("mqtt_") for k in ch):
            self._restart_mqtt()
        self._emit("settings_changed", {"keys": sorted(ch)})

    def _restart_mqtt(self) -> None:
        if self.mqtt:
            self.mqtt.stop()
            self.mqtt = None
        if self.mqtt_factory and self.store.get("mqtt_enabled") and self.store.get("mqtt_host"):
            self.mqtt = self.mqtt_factory(self)
            self.mqtt.start()

    # ------------------------------------------------------------------ events
    def on_event(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, kind: str, data: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, data)
            except Exception as e:
                self.logs(f"event listener error: {type(e).__name__}: {e}")

    def thermal(self) -> dict:
        return dict(self._thermal)

    def check_thermal(self) -> None:
        """Read the temperature now; on a state change log a fault (warning/critical) and emit 'thermal' at once."""
        temp, state = self.monitor.temperature()
        prev = self._thermal["state"]
        self._thermal = {"state": state, "temperature_c": temp, "checked_at": time.time()}
        if self.last_system:
            self.last_system["temperature_c"], self.last_system["thermal_state"] = temp, state
        if state != prev:
            if state in ("warning", "critical"):
                self.logs.fault("thermal", f"{state}: {temp} C")
            elif prev in ("warning", "critical"):
                self.logs(f"thermal state back to {state} ({temp} C)")
            self._emit("thermal", self.thermal())

    def _watch(self) -> None:
        next_sys = time.monotonic() + 2.0                 # second sample soon: CPU % needs two /proc/stat readings
        next_thermal = time.monotonic()
        while not self._stop.wait(0.5):
            try:
                now = time.monotonic()
                if now >= next_thermal:
                    next_thermal = now + THERMAL_CHECK_S
                    self.check_thermal()
                if now >= next_sys:
                    next_sys = now + SYSTEM_SAMPLE_S
                    self.last_system = self.monitor.sample()
                    self.last_system.update(temperature_c=self._thermal["temperature_c"] if self._thermal["checked_at"] else
                                            self.last_system.get("temperature_c"))
                st = self.status()
                key = (st["headline"], st["bridge"]["bluetooth_connection"], st["bridge"]["bt_media_activity"],
                       st["bridge"]["target_ownership"], st["bridge"]["lyrion_reachability"], st["bluetooth"]["pairing_open"],
                       st["bridge"]["audio_permission"], st["bluetooth"]["connected_device"] and st["bluetooth"]["connected_device"]["address"],
                       st["now_playing"])
                if key != self._last_key:
                    self._last_key = key
                    self._emit("status_changed", st)
            except Exception as e:
                self.logs(f"watch error: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ status
    def instance_id(self) -> str:
        iid = self.store.get("instance_id")
        if not iid:
            iid = secrets.token_hex(4)
            self.store.update({"instance_id": iid})
        return iid

    def configured(self) -> bool:
        return bool(self.store.get("lms_host") and self.store.get("target_player"))

    def status(self) -> dict:
        rt = self.runtime
        b = rt.status()
        device_addr = rt.controller.device
        labels = self.store.get("device_labels")
        dev = None
        if device_addr:
            name = labels.get(str(device_addr).upper())
            if not name:
                for d in self.paired_devices():
                    if d["address"].upper() == str(device_addr).upper():
                        name = d["name"]
            dev = {"address": device_addr, "name": name or device_addr}
        degraded = []
        if not self.configured():
            degraded.append("not_configured")
        elif b["lyrion_reachability"] == "unreachable":
            degraded.append("lyrion_unreachable")
        if self._thermal["state"] in ("warning", "critical"):
            degraded.append("thermal_" + self._thermal["state"])
        if self.mqtt and not self.mqtt.state().get("connected"):
            degraded.append("mqtt_disconnected")
        headline = self._headline(b, degraded)
        return {
            "version": __version__,
            "headline": headline,
            "setup_complete": self.store.get("setup_complete"),
            "configured": self.configured(),
            "degraded": degraded,
            "bridge": {k: b[k] for k in ("bluetooth_connection", "bt_media_activity", "audio_permission", "lyrion_reachability",
                                          "target_ownership", "stream_transport", "blocked_reason")},
            "bluetooth": {"name": self.store.get("device_name"), "pairing_open": rt.pairing.is_open(),
                          "pairing_remaining_s": int(rt.pairing.remaining()), "connected_device": dev},
            "lyrion": {"server": f"{self.store.get('lms_host')}:{self.store.get('lms_port')}" if self.store.get("lms_host") else None,
                       "target": self.store.get("target_player") or None},
            "now_playing": rt.stream.title or None,
            "mqtt": self.mqtt.state() if self.mqtt else {"enabled": bool(self.store.get("mqtt_enabled")), "connected": False},
            "system": self.last_system,
            "uptime_s": int(time.time() - self.started_at),
        }

    @staticmethod
    def _headline(b: dict, degraded: list[str]) -> str:
        if "not_configured" in degraded:
            return "not_configured"
        if b["audio_permission"] == "blocked":
            return "audio_disabled"
        if "lyrion_unreachable" in degraded:
            return "lyrion_unreachable"
        if b["bluetooth_connection"] != "connected":
            return "ready"
        if b["target_ownership"] == "owned" and b["bt_media_activity"] == "playing":
            return "playing"
        if b["blocked_reason"] in ("override_disabled", "notification_filter"):
            return "waiting_for_target"
        if b["bt_media_activity"] == "paused":
            return "paused"
        return "connected"

    # ------------------------------------------------------------------ owner actions
    def open_pairing(self) -> None:
        self.runtime.pairing.open(); self.logs("pairing window opened")

    def close_pairing(self) -> None:
        self.runtime.pairing.close(); self.logs("pairing window closed")

    def set_audio_allowed(self, allowed: bool) -> None:
        self.store.update({"audio_allowed": allowed})

    def paired_devices(self) -> list[dict]:
        src = self.source
        if src is None or not hasattr(src, "paired_devices"):
            return []
        try:
            devs = src.paired_devices()
        except Exception as e:
            self.logs(f"paired_devices failed: {type(e).__name__}: {e}")
            return []
        labels = self.store.get("device_labels")
        return [dict(d, label=labels.get(str(d["address"]).upper(), "")) for d in devs]

    def remove_device(self, address: str) -> bool:
        ok = bool(self.source is not None and hasattr(self.source, "remove_device") and self.source.remove_device(address))
        labels = self.store.get("device_labels")
        if labels.pop(str(address).upper(), None) is not None:
            self.store.update({"device_labels": labels})
        self.logs(f"paired device removed: {'ok' if ok else 'not found'}")
        return ok

    def set_device_label(self, address: str, label: str) -> None:
        labels = self.store.get("device_labels")
        labels[str(address).upper()] = label
        self.store.update({"device_labels": labels})

    def discover_servers(self, timeout: float = 2.0) -> list[dict]:
        try:
            return [{"host": d.get("address"), "port": int(d.get("JSON") or 9000), "name": d.get("NAME", ""), "version": d.get("VERS", "")}
                    for d in self.discover(timeout=timeout)]
        except OSError as e:
            self.logs(f"discovery failed: {e}")
            return []

    def check_server(self, host: str, port: int) -> dict:
        try:
            return {"reachable": True, "version": self.lms_factory(lms_url(host, port)).server_version()}
        except LmsUnreachable as e:
            return {"reachable": False, "error": str(e)[:200]}

    def list_targets(self, host: str | None = None, port: int | None = None) -> list[dict]:
        host = host or self.store.get("lms_host")
        port = port or self.store.get("lms_port")
        if not host:
            return []
        return self.lms_factory(lms_url(host, port)).targets()

    def diagnostic_test(self) -> str:
        if not self.configured():
            return "refused:not_configured"
        r = self.runtime.diagnostic_test()
        self.logs(f"diagnostic test: {r}")
        return r

    def rotate_stream_token(self) -> None:
        new = secrets.token_urlsafe(24)
        self.logs.secrets.append(new)
        self.store.set_secret("stream_token", new)
        self.runtime.rotate_token(new)
        self.logs("stream token rotated (new sessions use the new URL)")

    def diagnostics_bundle(self) -> dict:
        """Owner download: everything needed to debug, nothing secret (test: no token/password hash/MAC labels leak)."""
        return {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": __version__,
            "python": platform.python_version(), "platform": platform.platform(),
            "config": self.store.export_redacted(), "status": self.status(),
            "stream": dict(self.runtime.stream.stats), "lms_commands": [c[0] for c in getattr(self.runtime.lms, "commands_sent", [])][-50:],
            "controller_events": [(round(t, 2), n, str(d)) for t, n, d in self.runtime.controller.events[-100:]],
            "logs": [(round(t, 2), m) for t, m in self.logs.recent(300)], "faults": self.logs.faults(),
            "note": "stream token, password hash and other secrets are never included",
        }
