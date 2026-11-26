"""Opt-in audio-path trace (OFF by default): ``HIFI_STREAM_TRACE=1``.

Written for the G1 hardware finding "music goes silent for 10-15 s, comes back exactly when a new ``GET ... 206`` appears"
(docs/STREAM_TRACE.md). It answers, per second and per HTTP connection, where the audio stops:

  (a) ingress   - did the bridge's OWN capture child deliver PCM, and was it non-silent (peak, share of non-zero samples)?
  (b) buffer    - did the feeder insert silence / drop frames; how far behind the live edge is each reader?
  (c) delivery  - how many bytes each HTTP connection sent, whether they were audio or silence, whether a write is blocked
                  (and for how long: a write that never returns is shown while it is still stuck);
  (d) end       - when and why a connection ended (peer closed / reset / write timeout / session ended / error);
  (e) Lyrion    - what the controller's normal 1 s poll saw (mode, elapsed time, duration), so a restarted stream is visible.

Bounded and private: at most one summary line per period (default 1 s) while anything is active, plus one line per connection
start and end. No audio is logged, only levels. No token or URL: the session id (already in the [ctl] log), the ``Range`` header
reduced to ``bytes=<n>-<m>``, and peers as ``peer1``, ``peer2`` ... (never an address). Levels are computed on a sample of the
PCM (every 4th/16th sample), which costs a few ms per second on a Pi 3 and nothing when the trace is off (all hooks are skipped).
"""
from __future__ import annotations

import math
import os
import re
import threading
import time

ENV = "HIFI_STREAM_TRACE"
RATE, FRAME_BYTES = 44100, 4
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def enabled_from_env(environ=os.environ) -> bool:
    return str(environ.get(ENV, "")).strip().lower() in ("1", "yes", "true", "on")


def safe_range(value: str | None) -> str:
    """The Range header as ``bytes=<n>-<m>`` (digits only) or ``other``/``-``: never echo arbitrary header text."""
    if not value:
        return "-"
    m = _RANGE_RE.match(value.strip())
    return f"bytes={m.group(1)}-{m.group(2)}" if m else "other"


class Level:
    """Peak and share of non-zero samples of s16le PCM, on every ``step``-th sample (bounded cost)."""
    __slots__ = ("peak", "nonzero", "samples")

    def __init__(self):
        self.peak = self.nonzero = self.samples = 0

    def add(self, pcm: bytes, step: int) -> None:
        n = len(pcm) // 2
        if n == 0:
            return
        mv = memoryview(pcm)[: n * 2].cast("h")[::step]
        vals = mv.tolist()
        if not vals:
            return
        self.samples += len(vals)
        self.nonzero += len(vals) - vals.count(0)
        self.peak = max(self.peak, max(vals), -min(vals))

    def text(self) -> str:
        if not self.samples:
            return "peak=- nz=-"
        db = "-inf" if self.peak == 0 else f"{20 * math.log10(min(self.peak, 32767) / 32767):.1f}"
        return f"peak={db}dBFS nz={self.nonzero / self.samples:.2f}"


class _Conn:
    def __init__(self, rid: str, sid: str, peer: str, t0: float):
        self.rid, self.sid, self.peer, self.t0 = rid, sid, peer, t0
        self.bytes_total = 0
        self.bytes_period = 0
        self.level = Level()
        self.lag_frames = 0
        self.pos: int | None = None                 # ring position this connection has sent up to (lag is computed live from it)
        self.skipped_period = 0
        self.write_started: float | None = None     # monotonic start of the write in progress (None = not in a write)
        self.write_max_period = 0.0
        self.first_byte_at: float | None = None


class StreamTrace:
    def __init__(self, log, period_s: float = 1.0, clock=time.monotonic):
        self.log, self.period_s, self.clock = log, period_s, clock
        self.member_period_s = 2.0
        self.t0 = clock()
        self._lock = threading.Lock()
        self._n = 0
        self._peers: dict[str, str] = {}
        self._conns: dict[str, _Conn] = {}
        self._in_chunks = self._in_bytes = 0
        self._in_level = Level()
        self._in_last: float | None = None
        self._in_gap_max = 0.0
        self._feeder_last = None
        self._thread: threading.Thread | None = None
        self.lines_emitted = 0
        # providers set by the runtime (all optional): feeder counters, ring head, last LMS observation
        self.feeder = None
        self.ring = None
        self.lms_observation = None          # callable -> (monotonic_seconds, Observation, is_our_stream) | None
        self.lms_poll = None                 # callable -> (monotonic_seconds, "ok" | "lms-unreachable" | "target-disconnected") | None
        self._members = None                 # (monotonic_seconds, [{name, connected, signal}]) from start_member_poll

    def _t(self) -> str:
        """Monotonic seconds since the trace started (for ordering) + local wall time (to match what the listener noted)."""
        return f"t={self.clock() - self.t0:.2f} {time.strftime('%H:%M:%S')}"

    def _emit(self, text: str) -> None:
        self.lines_emitted += 1
        self.log(f"[trace] {self._t()} {text}")

    # -- (a) ingress: the bridge's own capture child (called from the capture thread) --------------------------------
    def ingress(self, pcm: bytes) -> None:
        now = self.clock()
        with self._lock:
            self._in_chunks += 1
            self._in_bytes += len(pcm)
            if self._in_last is not None:
                self._in_gap_max = max(self._in_gap_max, now - self._in_last)
            self._in_last = now
            self._in_level.add(pcm, 4)

    # -- (c)/(d) HTTP connections (called from the request threads) ------------------------------------------------
    def conn_start(self, sid: str, peer_ip: str, method: str, status: int, rng: str | None, content_range: str | None,
                   wav_header: bool, start_lag_frames: int | None) -> str:
        with self._lock:
            self._n += 1
            rid = f"r{self._n}"
            peer = self._peers.setdefault(peer_ip, f"peer{len(self._peers) + 1}")
            if method == "GET" and status in (200, 206):
                self._conns[rid] = _Conn(rid, sid, peer, self.clock())
        lag = "-" if start_lag_frames is None else f"{start_lag_frames * 1000 // RATE}ms"
        self._emit(f"{rid} start {method} {status} sid={sid} {peer} range={safe_range(rng)} "
                   f"content-range={content_range or '-'} wav-header={'yes' if wav_header else 'no'} lead={lag}")
        return rid

    def write_begin(self, rid: str, pos: int | None = None) -> None:
        """``pos``: ring position this write starts at; it is the lag basis until a first write completes."""
        c = self._conns.get(rid)
        if c is not None:
            c.write_started = self.clock()
            if c.pos is None:
                c.pos = pos

    def write_end(self, rid: str, sent: int, pcm: bytes, lag_frames: int, skipped: int, pos: int | None = None) -> None:
        """``sent``: bytes written to the socket (incl. ICY metadata / FLAC frames); ``pcm``: the audio they carry (levels)."""
        c = self._conns.get(rid)
        if c is None:
            return
        now = self.clock()
        with self._lock:
            if c.write_started is not None:
                c.write_max_period = max(c.write_max_period, now - c.write_started)
            c.write_started = None
            if c.first_byte_at is None:
                c.first_byte_at = now
            c.bytes_total += sent
            c.bytes_period += sent
            c.lag_frames, c.pos = lag_frames, pos
            c.skipped_period += skipped
            c.level.add(pcm, 16)

    def conn_end(self, rid: str, reason: str) -> None:
        with self._lock:
            c = self._conns.pop(rid, None)
        if c is None:
            return
        now = self.clock()
        stuck = f" write-in-progress={now - c.write_started:.2f}s" if c.write_started is not None else ""
        self._emit(f"{rid} end reason={reason} after={now - c.t0:.2f}s bytes={c.bytes_total} sid={c.sid} {c.peer}{stuck}")

    # -- (b)/(e) periodic summary ----------------------------------------------------------------------------------
    def snapshot(self) -> str | None:
        """One summary line, or None when nothing is active (no ingress, no connection, no session fill)."""
        now = self.clock()
        with self._lock:
            chunks, nbytes, lvl, gap = self._in_chunks, self._in_bytes, self._in_level, self._in_gap_max
            since_last = None if self._in_last is None else now - self._in_last
            self._in_chunks = self._in_bytes = 0
            self._in_level, self._in_gap_max = Level(), 0.0
            conns = []
            head = getattr(self.ring, "head", None)
            for c in self._conns.values():
                if head is not None and c.pos is not None:
                    c.lag_frames = max(0, head - c.pos)             # grows while a write is stuck
                blk = c.write_max_period
                if c.write_started is not None:
                    blk = max(blk, now - c.write_started)
                conns.append(f"{c.rid}:{c.bytes_period}B {c.level.text()} lag={c.lag_frames * 1000 // RATE}ms "
                             f"blk={blk * 1000:.0f}ms{' STUCK' if c.write_started is not None and blk > 1.0 else ''}"
                             f"{f' skip={c.skipped_period}fr' if c.skipped_period else ''}")
                c.bytes_period, c.level, c.write_max_period, c.skipped_period = 0, Level(), 0.0, 0
        fd = ""
        f = self.feeder
        fill = bool(getattr(f, "fill_enabled", False))
        if f is not None:
            cur = (f.pushed_frames, f.silence_frames, f.dropped_frames)
            last = self._feeder_last or cur
            self._feeder_last = cur
            fd = f" | feeder: +{cur[0] - last[0]}fr real +{cur[1] - last[1]}fr silence +{cur[2] - last[2]}fr dropped fill={'on' if fill else 'off'}"
        if not chunks and not conns and not fill:
            return None
        idle = f" idle={since_last:.1f}s" if (since_last is not None and not chunks) else ""
        line = f"in: {chunks}ch {nbytes}B {lvl.text()} gap-max={gap * 1000:.0f}ms{idle}{fd}"
        line += " | out: " + ("; ".join(conns) if conns else "no connection")
        obs = self.lms_observation() if callable(self.lms_observation) else None
        poll = self.lms_poll() if callable(self.lms_poll) else None
        if obs or poll:
            line += " | lms:"
        if obs:
            at, o, ours = obs
            tm = getattr(o, "time", None)
            du = getattr(o, "duration", None)
            line += (f" {o.mode} time={'-' if tm is None else f'{tm:.1f}'} duration={'-' if du is None else f'{du:.0f}'}"
                     f" ours={'yes' if ours else 'no'} seen={now - at:.1f}s ago{' WAITING' if getattr(o, 'waiting', False) else ''}")
        if poll and poll[1] != "ok":
            line += f" poll={poll[1]} {now - poll[0]:.1f}s ago"
        line += self._members_text(now)
        return line

    def _members_text(self, now: float) -> str:
        m = self._members
        if not m:
            return ""
        at, members = m
        parts = []
        for x in members:
            name = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(x.get("name", "?")))[:20]
            sig = f" sig={x['signal']}" if x.get("signal") else ""
            parts.append(f"{name}:{'ok' if x.get('connected') else 'DISC'}{sig}{'(master)' if x.get('master') else ''}")
        age = now - at
        return f" | members: {', '.join(parts) or '-'}{f' ({age:.0f}s old)' if age > 3 * self.member_period_s else ''}"

    def start_member_poll(self, stop: threading.Event, fetch, period_s: float = 2.0) -> None:
        """Poll ``fetch() -> [{name, connected, signal}]`` (LMS sync-group members, read-only) in its own thread, so a slow or
        unreachable LMS never delays the summary line. Failures keep the last result (its age is shown)."""
        self.member_period_s = period_s

        def run():
            while not stop.wait(period_s):
                try:
                    self._members = (self.clock(), list(fetch() or []))
                except Exception:                      # LmsUnreachable or anything else: keep the last result, show its age
                    pass
        threading.Thread(target=run, daemon=True, name="stream-trace-members").start()

    def tick(self) -> None:
        line = self.snapshot()
        if line:
            self._emit(line)

    def start(self, stop: threading.Event) -> None:
        def run():
            while not stop.wait(self.period_s):
                try:
                    self.tick()
                except Exception as e:                 # the trace must never disturb the audio path
                    self.log(f"[trace] error: {type(e).__name__}: {e}")
        self._thread = threading.Thread(target=run, daemon=True, name="stream-trace")
        self._thread.start()
