"""Wiring: source adapter -> fusion/feeder/ring -> stream server; controller -> LMS."""
from __future__ import annotations

import math
import secrets
import struct
import threading
import time
from dataclasses import dataclass

from .core.clock import SystemClock
from .core.controller import Config, Controller
from .core.fusion import MediaFusion
from .core.pairing import PairingWindow
from .core.ring import PcmRing
from .lms.client import LmsClient
from .stream.feeder import LiveFeeder, RATE
from .stream.server import StreamServer, redact


DIAG_TAIL_S = 2.2        # > measured player lead (~1.56 s) so a diagnostic tone is not cut short


@dataclass
class RuntimeConfig:
    lms_url: str = "http://lms:9000"
    advertise_host: str = "bridge"        # hostname/IP the LMS server + players use to reach us
    stream_port: int = 8090
    stream_format: str = "wav"            # wav | flac
    ring_seconds: float = 6.0
    lead_in_ms: int = 1000
    icy_metadata: bool = False
    pairing_s: float = 300.0


class BridgeRuntime:
    def __init__(self, rcfg: RuntimeConfig, ccfg: Config, source=None, clock=None, lms=None, log=print, token: str | None = None):
        self.rcfg, self.ccfg = rcfg, ccfg
        self.clock = clock or SystemClock()
        self.logf = log
        self.ring = PcmRing(int(rcfg.ring_seconds * RATE))
        self.feeder = LiveFeeder(self.ring)
        self.token = token or secrets.token_urlsafe(24)
        self._old_tokens: list[str] = []        # still redacted from logs after a rotation
        self.stream = StreamServer(self.ring, self.feeder, self.token, rcfg.stream_format, rcfg.lead_in_ms,
                                   rcfg.icy_metadata, log=lambda m: self.log(m))
        self.lms = lms or LmsClient(rcfg.lms_url)
        self.fusion = MediaFusion(self.clock)
        self.pairing = PairingWindow(rcfg.pairing_s, self.clock, on_change=self._pairable_changed)
        self.port = 0
        self.controller = Controller(ccfg, self.lms, self.stream, self.clock, self._url_for, log=self._clog)
        self.source = source
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.heartbeat = time.monotonic()
        self.on_controller_event = None                  # optional hook (service: crash-recovery journal)
        self.timeline: list[tuple] = []         # (wall, event, detail) probes; no tokens

    # -- logging (always redacted) -------------------------------------------
    def log(self, msg: str) -> None:
        m = redact(str(msg))
        for t in (self.token, *self._old_tokens):
            m = m.replace(t, "***")
        self.logf(m)

    # -- in-place reconfiguration (owner settings; never rebuilds the runtime, so a connected phone stays connected) --
    def set_lms(self, lms) -> None:
        """Switch to another LMS server: release OUR stream on the old server (URL-addressed, as always), then continue on the new one."""
        c = self.controller
        with c.lock:
            c._release("server_change")
            c.lms = self.lms = lms
            c.reachability, c.lost_at, c.stale_urls = "unknown", None, []
            c.ownership = "none"
            if c._wanted():
                c._maybe_takeover(fresh=True)

    def reconfigure_stream(self, fmt: str | None = None, icy: bool | None = None) -> None:
        """Format/ICY apply to new sessions; a running session of ours is restarted with the new format."""
        c = self.controller
        with c.lock:
            changed = (fmt is not None and fmt != self.stream.fmt) or (icy is not None and icy != self.stream.icy)
            if not changed:
                return
            owned = c.session is not None and c.ownership == "owned"
            if owned:
                c._release("stream_reconfigure")
            if fmt is not None:
                self.stream.fmt = self.rcfg.stream_format = fmt
            if icy is not None:
                self.stream.icy = self.rcfg.icy_metadata = icy
            if owned and c._wanted():
                c._maybe_takeover(fresh=True)

    def rotate_token(self, new_token: str) -> None:
        """New stream URLs use the new token; the running session continues (docs/OPEN_QUESTIONS.md #11)."""
        self._old_tokens.append(self.token)
        self.token = new_token
        self.stream.rotate_token(new_token)

    def _clog(self, name, detail=""):
        self.timeline.append((time.time(), name, str(detail)))
        self.log(f"[ctl] {name} {detail}")
        if self.on_controller_event:
            try:
                self.on_controller_event(name, str(detail))
            except Exception as e:                      # journaling must never break the control path
                self.logf(f"controller event hook error: {type(e).__name__}: {e}")

    def _url_for(self, sid: str) -> str:
        return self.stream.url(self.rcfg.advertise_host, self.port, sid)

    def _pairable_changed(self, on: bool) -> None:
        if self.source:
            self.source.set_pairable(on)

    # -- source callbacks -----------------------------------------------------
    def _on_connect(self, dev: str) -> bool:
        ok = self.controller.on_connected(dev)
        if ok:
            self.fusion.reset()
        return ok

    def _on_disconnect(self, dev: str) -> None:
        self.controller.on_disconnected(dev)
        self.fusion.reset()

    def _refresh_media(self) -> None:
        self.controller.on_media(self.fusion.playing())

    def _on_transport(self, state: str) -> None:
        self.fusion.on_transport(state)
        self._refresh_media()

    def _on_avrcp(self, status) -> None:
        self.fusion.on_avrcp(status)
        self._refresh_media()

    def _on_pcm(self, pcm: bytes) -> None:
        first = self.fusion.last_pcm is None
        self.fusion.on_pcm()
        self.feeder.push(pcm)
        if first:
            self._refresh_media()

    def _on_metadata(self, title: str, artist: str, album: str) -> None:
        self.controller.on_metadata(f"{artist} - {title}" if artist else title)

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self.port = self.stream.start(port=self.rcfg.stream_port)
        self._threads = [threading.Thread(target=self.feeder.run, args=(self._stop,), daemon=True, name="feeder"),
                         threading.Thread(target=self._loop, daemon=True, name="controller")]
        for t in self._threads:
            t.start()
        if self.source:
            class CB:
                on_connect = staticmethod(self._on_connect)
                on_disconnect = staticmethod(self._on_disconnect)
                on_transport = staticmethod(self._on_transport)
                on_avrcp = staticmethod(self._on_avrcp)
                on_volume = staticmethod(self.controller.on_volume)
                on_metadata = staticmethod(self._on_metadata)
                on_pcm = staticmethod(self._on_pcm)
            self.callbacks = CB
            self.source.attach(CB)
            self.source.start()

    def _loop(self) -> None:
        while not self._stop.wait(0.1):
            self.heartbeat = time.monotonic()           # supervision: a hung control loop (e.g. a deadlock) stops this
            try:
                self.pairing.tick()
                self._refresh_media()           # fusion has time-based rules (PCM timeout)
                self.controller.tick()
            except Exception as e:              # never let the control loop die
                self.log(f"[ctl] tick error: {type(e).__name__}: {e}")

    def stop(self, stop_source: bool = True) -> None:
        self._stop.set()
        if self.source and stop_source:
            self.source.stop()
        c = self.controller
        with c.lock:
            c._release("shutdown")
        self.stream.stop()

    def diagnostic_test(self, duration_s: float = 3.0) -> str:
        """Owner diagnostic: 440 Hz test tone through the real stream path. Refused if the target is busy."""
        ok, reason, sid = self.controller.begin_diagnostic()
        if not ok:
            return "refused:" + reason
        threading.Thread(target=self._tone, args=(sid, duration_s), daemon=True, name="diag-tone").start()
        return "started"

    def _tone(self, sid: str, duration_s: float) -> None:
        n = RATE // 50                                   # 20 ms chunks, real-time paced
        t_next, i = time.monotonic(), 0
        while time.monotonic() - t_next < duration_s and not self._stop.is_set():
            buf = bytearray()
            for k in range(n):
                v = int(8000 * math.sin(2 * math.pi * 440 * (i + k) / RATE))
                buf += struct.pack("<hh", v, v)
            i += n
            self.feeder.push(bytes(buf))
            time.sleep(0.02)
        # the player still holds ~1.5 s of lead (LMS start threshold, see docs/FINDINGS.md): keep the
        # session (silence-filled) until the tone has actually been played, then release only our session
        self._stop.wait(DIAG_TAIL_S)
        self.controller.end_diagnostic(sid)

    def status(self) -> dict:
        s = self.controller.status()
        s.update(pairing_open=self.pairing.is_open(), feeder={
            "silence_frames": self.feeder.silence_frames, "dropped_frames": self.feeder.dropped_frames,
            "pushed_frames": self.feeder.pushed_frames}, stream=dict(self.stream.stats))
        return s
