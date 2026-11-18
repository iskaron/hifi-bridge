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
import ssl
import threading
import time
from typing import Callable

from . import __version__, privacy
from .app import BridgeRuntime, RuntimeConfig
from .config import ConfigStore
from .core.controller import Config
from .lms import discovery
from .lms.client import LmsClient, LmsUnreachable, Observation, _Resolver
from .stream.server import redact
from .system import SystemMonitor
from .supervision import SessionJournal
from .webui.auth import Auth

TELEMETRY_S = 300          # MQTT/system metrics every 5 min (DECISIONS #11, #36)
SYSTEM_SAMPLE_S = 30       # full system sample for the UI
THERMAL_CHECK_S = 10       # fast, independent over-temperature check (DECISIONS #36): a warning never waits for telemetry
HISTORY_SAMPLES = 240      # 2 h of 30 s samples for the trend graphs (PRODUCT_SPEC: "CPU graphs secondary")


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


def local_ip_for(host: str, port: int, resolver: "_Resolver | None" = None, connect=None) -> str | None:
    """The local address the bridge would use to reach ``host`` (= what LMS/players must use to reach the stream). No packet is sent.
    ``None`` when there is no route yet (Wi-Fi not up, no DHCP lease): the caller must then not advertise anything - the old
    fallback 127.0.0.1 sent every remote LMS an unreachable URL until the next restart (review H3). A host name is looked up
    through ``resolver`` (bounded, cached) because a UDP connect would otherwise block in getaddrinfo."""
    try:
        target = resolver.address() if resolver is not None else host
        fam = socket.AF_INET6 if ":" in target else socket.AF_INET
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            (connect or (lambda sock, addr: sock.connect(addr)))(s, (target, port))
            return s.getsockname()[0]
    except (OSError, LmsUnreachable):
        return None


class LogBuffer:
    """Recent log lines in RAM (bounded) + persistent critical faults (bounded file), always token-redacted."""

    def __init__(self, data_dir: str, size: int = 600, faults_keep: int = 200, echo: Callable[[str], None] | None = print):
        self.lines: collections.deque = collections.deque(maxlen=size)
        self.faults_path, self.faults_keep, self.echo = os.path.join(data_dir, "faults.jsonl"), faults_keep, echo
        self._lock = threading.Lock()
        self.secrets: list[str] = []
        self.forward: Callable[[str], None] | None = None

    def _clean(self, msg: str) -> str:
        m = privacy.scrub(redact(str(msg)))            # MAC addresses -> per-device pseudonyms (review M10)
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
        fwd = self.forward
        if fwd is not None:
            fwd(m)                                     # remote syslog: already redacted, non-blocking

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
        salt = self.store.secret("log_salt")
        if not salt:
            salt = secrets.token_hex(16)
            self.store.set_secret("log_salt", salt)
        privacy.configure(bytes.fromhex(salt))
        self.logs.secrets = [self.store.secret("stream_token") or ""]
        self.auth = Auth(self.store, log=self.logs, **({"iterations": auth_iterations} if auth_iterations else {}))
        self.lms_factory, self.discover, self.advertise_cfg = lms_factory, discover, advertise_host
        self.monitor = monitor or SystemMonitor(data_dir=data_dir)
        self.mqtt_factory, self.mqtt = mqtt_factory, None
        self.mqtt_failed = False                        # enabled but could not be started (shown as degraded)
        self.syslog = None
        self.access = None                              # LocalAccess (names, HTTPS, mDNS) - set by the appliance entry point
        c = self.store.all()
        rc = RuntimeConfig(lms_url=lms_url(c["lms_host"], c["lms_port"]) if c["lms_host"] else "http://127.0.0.1:9",
                           advertise_host="" if advertise_host == "auto" else advertise_host, stream_port=stream_port, stream_format=c["stream_format"],
                           icy_metadata=c["icy_metadata"], pairing_s=float(c["pairing_seconds"]))
        cc = Config(target_player=c["target_player"], audio_allowed=c["audio_allowed"], override_lms=c["override_lms"],
                    pause_strategy=c["pause_strategy"])
        self.runtime = BridgeRuntime(rc, cc, source=source, lms=self._lms_for(c), log=self.logs, token=self.store.secret("stream_token"))
        self._resolvers: dict[tuple, _Resolver | None] = {}
        if advertise_host == "auto":
            self.runtime.advertise = self.stream_host           # per session: follows Wi-Fi coming up and DHCP changes
        self.journal = SessionJournal(data_dir)
        self.runtime.on_controller_event = self._journal_event
        self.source = source
        self.store.on_change(self._apply)
        self._listeners: list[Callable[[str, dict], None]] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_key = None
        self.started_at = time.time()
        self.last_system: dict = {}
        self._thermal = {"state": "unknown", "temperature_c": None, "checked_at": None}
        self.watch_heartbeat = time.monotonic()
        self.watch_ident: int | None = None
        self.history: collections.deque = collections.deque(maxlen=HISTORY_SAMPLES)    # (t, cpu %, temp C, memory %) for the trend graphs

    # ------------------------------------------------------------------ lifecycle
    def _journal_event(self, name: str, detail: str) -> None:
        """Keep <data>/session.json in sync with the LMS item the bridge owns (crash recovery, see supervision.py)."""
        ctl = self.runtime.controller
        if name == "session_start" and ctl.session is not None:
            self.journal.record(ctl.cfg.target_player, ctl.session.url)
        elif name in ("session_release", "session_parked", "ownership_lost", "stream_died", "restart_after_outage"):
            # released by us, or no longer ours: nothing to clean up after a crash. A release is reported after its LMS call
            # (outside the controller lock), so a newer session may already own the journal: keep that entry.
            s, j = ctl.session, self.journal.pending()
            if not (s is not None and not s.released_at_lms and ctl.ownership == "owned" and j and j.get("url") == s.url):
                self.journal.clear()

    def recover_stale_session(self) -> str:
        """After a crash: release the URL a previous process left at LMS (URL-addressed: harmless if anything else plays now)."""
        j = self.journal.pending()
        if not j:
            return "none"
        if isinstance(self.runtime.lms, UnconfiguredLms):
            self.journal.clear_if(j["url"])
            return "dropped_unconfigured"
        try:
            self.runtime.controller.io.call(self.runtime.lms.release, j["player"], j["url"])
        except LmsUnreachable:
            return "pending"                             # retried by the watch loop until LMS is reachable
        self.journal.clear_if(j["url"])                  # a session started meanwhile keeps its own entry
        self.logs("recovered: released the stream item a previous (crashed) process left at Lyrion")
        return "released"

    def start(self) -> None:
        self.recover_stale_session()
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
        self._restart_syslog()
        self.logs(f"service started (version {__version__})")

    def stop(self) -> None:
        self._stop.set()
        if self.mqtt:
            self.mqtt.stop()
        if self.syslog:
            self.logs("service stopping")
            self.syslog.stop()
        if self.access is not None:
            self.access.stop()                         # mDNS goodbye
        self.runtime.stop()

    # ------------------------------------------------------------------ config -> runtime
    def _lms_for(self, c: dict):
        if not c["lms_host"] or not c["target_player"]:
            return UnconfiguredLms()
        return self.lms_factory(lms_url(c["lms_host"], c["lms_port"]))

    def stream_host(self) -> str | None:
        """Host LMS and its players use to fetch the stream, computed per session (route toward the LMS host)."""
        if self.advertise_cfg != "auto":
            return self.advertise_cfg
        host, port = self.store.get("lms_host"), self.store.get("lms_port")
        if not host:
            return None
        key = (host, port)
        if key not in self._resolvers:
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
                self._resolvers = {key: None}                 # IP literal: nothing to look up
            except OSError:
                self._resolvers = {key: _Resolver(host, int(port))}
        return local_ip_for(host, port, resolver=self._resolvers[key])

    def _step(self, what: str, fn: Callable, *args) -> None:
        """One independent part of applying settings: a failure is logged and the remaining parts still apply (review H4)."""
        try:
            fn(*args)
        except Exception as e:
            self.logs(f"{what} failed: {type(e).__name__}: {e}")

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
            self._step("audio permission", ctl.set_audio_allowed, ch["audio_allowed"])
        if "stream_format" in ch or "icy_metadata" in ch:
            self._step("stream reconfiguration", rt.reconfigure_stream, ch.get("stream_format"), ch.get("icy_metadata"))
        if {"lms_host", "lms_port"} & set(ch) or ("target_player" in ch and (not c["target_player"] or isinstance(rt.lms, UnconfiguredLms))):
            self._step("Lyrion server change", lambda: rt.set_lms(self._lms_for(c), c["target_player"] if "target_player" in ch else None))
        elif "target_player" in ch:
            self._step("target change", ctl.set_target, c["target_player"])
        if any(k.startswith("mqtt_") for k in ch):
            self._restart_mqtt()
        if any(k.startswith("syslog_") for k in ch) or "device_name" in ch:
            self._restart_syslog()
        if self.access is not None and {"local_hostname", "mdns_enabled", "mdns_backend", "extra_hostnames", "tls_mode", "device_name"} & set(ch):
            self._step("local access reconfiguration", self.access.reconfigure, set(ch))
        self._emit("settings_changed", {"keys": sorted(ch)})

    def _restart_syslog(self) -> None:
        if self.syslog:
            self.logs.forward = None
            self.syslog.stop(flush_s=0.5)
            self.syslog = None
        st = self.store
        if st.get("syslog_enabled") and st.get("syslog_host"):
            from .system.syslog import RemoteSyslog
            try:
                self.syslog = RemoteSyslog(st.get("syslog_host"), st.get("syslog_port"), st.get("syslog_ca_pem"),
                                           hostname=(st.get("local_hostname") or "hifi-bridge"))
                self.syslog.start()
            except ssl.SSLError as e:
                self.syslog = None
                self.logs(f"remote syslog: invalid CA certificate ({e})")
                return
            except Exception as e:                       # an optional integration never aborts start or settings (review H4)
                self.syslog = None
                self.logs(f"remote syslog could not start: {type(e).__name__}: {e}")
                return
            self.logs.forward = self.syslog.emit

    def _restart_mqtt(self) -> None:
        if self.mqtt:
            old, self.mqtt = self.mqtt, None
            try:
                old.stop()
            except Exception as e:
                self.logs(f"MQTT stop failed: {type(e).__name__}: {e}")
        self.mqtt_failed = False
        if self.mqtt_factory and self.store.get("mqtt_enabled") and self.store.get("mqtt_host"):
            try:
                m = self.mqtt_factory(self)
                m.start()
                self.mqtt = m
            except Exception as e:                       # an optional integration never aborts start or settings (review H4)
                self.mqtt_failed = True
                self.logs(f"MQTT could not start: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ events
    def on_event(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def off_event(self, fn: Callable[[str, dict], None]) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def _emit(self, kind: str, data: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, data)
            except Exception as e:
                self.logs(f"event listener error: {type(e).__name__}: {e}")

    def record_history(self, s: dict) -> None:
        self.history.append((s.get("sampled_at"), s.get("cpu_percent"), s.get("temperature_c"), (s.get("memory") or {}).get("used_percent")))

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
        self.watch_ident = threading.get_ident()
        while not self._stop.wait(0.5):
            self.watch_heartbeat = time.monotonic()
            try:
                now = time.monotonic()
                if now >= next_thermal:
                    next_thermal = now + THERMAL_CHECK_S
                    self.check_thermal()
                    if self.journal.pending() and self.runtime.controller.session is None:
                        self.recover_stale_session()
                if now >= next_sys:
                    next_sys = now + SYSTEM_SAMPLE_S
                    self.last_system = self.monitor.sample()
                    self.record_history(self.last_system)
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

    # ------------------------------------------------------------------ health (supervision, /healthz)
    HEARTBEAT_STALE_S = 5.0
    LMS_CALL_HANG_S = 60.0          # an LMS call is bounded by the client's timeouts (lms/client.py); longer = a real hang

    def health(self) -> dict:
        """'failed' = a vital internal loop is dead or hung -> the service supervisor must restart the process (watchdog stops pinging).
        'degraded' = running but something outside is wrong (LMS, MQTT, thermal) -> no restart, shown in the UI."""
        now = time.monotonic()
        rt = self.runtime
        threads = {t.name: t.is_alive() for t in rt._threads}
        checks = {
            # waiting in a slow LMS call (outage, DNS) is not a hang: degraded, never a restart (review H2)
            "control_loop": bool(threads.get("controller")) and rt.controller.io.alive(
                rt.loop_ident, rt.heartbeat, self.HEARTBEAT_STALE_S, self.LMS_CALL_HANG_S, now),
            "feeder": bool(threads.get("feeder")),
            "service_watch": any(t.is_alive() for t in self._threads) and rt.controller.io.alive(
                self.watch_ident, self.watch_heartbeat, self.HEARTBEAT_STALE_S, self.LMS_CALL_HANG_S, now),
            "stream_server": rt.stream.httpd is not None,
        }
        st = self.status()
        status = "failed" if not all(checks.values()) else ("degraded" if st["degraded"] else "ok")
        return {"status": status, "checks": checks, "degraded": st["degraded"], "version": __version__,
                "uptime_s": int(time.time() - self.started_at)}

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
        if b["blocked_reason"] == "stream_address_unavailable":
            degraded.append("stream_address_unavailable")
        if self._thermal["state"] in ("warning", "critical"):
            degraded.append("thermal_" + self._thermal["state"])
        if (self.mqtt and not self.mqtt.state().get("connected")) or (self.mqtt is None and self.mqtt_failed):
            degraded.append("mqtt_disconnected")
        if self.runtime.pairing.error:
            degraded.append("pairing_adapter_error")
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
    def open_pairing(self) -> bool:
        """False when the Bluetooth adapter refused the change (retried in the background, shown as degraded)."""
        ok = self.runtime.pairing.open()
        self.logs("pairing window opened" if ok else f"pairing window: the adapter refused to open ({self.runtime.pairing.error}); retrying")
        return ok

    def close_pairing(self) -> bool:
        ok = self.runtime.pairing.close()
        self.logs("pairing window closed" if ok else f"pairing window: the adapter refused to close ({self.runtime.pairing.error}); retrying")
        return ok

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
        """Owner download: everything needed to debug, nothing secret. No stream token or password hash; every MAC address
        (phones, players) is replaced by its salted per-device pseudonym as in the logs; device labels and phone names are left
        out (review M10). Tests: test_service.py, test_review_service.py."""
        cfg = self.store.export_redacted()
        labels = cfg["settings"].get("device_labels") or {}
        cfg["settings"]["device_labels"] = f"{len(labels)} label(s), not included"
        st = self.status()
        if st["bluetooth"].get("connected_device"):
            st["bluetooth"]["connected_device"] = {"address": st["bluetooth"]["connected_device"]["address"]}
        return privacy.scrub_data({
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": __version__,
            "python": platform.python_version(), "platform": platform.platform(),
            "config": cfg, "status": st,
            "stream": dict(self.runtime.stream.stats), "lms_commands": [c[0] for c in getattr(self.runtime.lms, "commands_sent", [])][-50:],
            "controller_events": [(round(t, 2), n, str(d)) for t, n, d in list(self.runtime.controller.events)[-100:]],
            "logs": [(round(t, 2), m) for t, m in self.logs.recent(300)], "faults": self.logs.faults(),
            "note": "stream token, password hash and other secrets are never included; MAC addresses are pseudonymised",
        })
