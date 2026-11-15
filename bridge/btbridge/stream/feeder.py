"""Live feeder: keeps the stream timeline at real time (never stalls, never runs ahead).

Measured in the lab (see docs/FINDINGS.md): if a live HTTP stream stalls, the
player underruns, LMS pauses it and only un-pauses ~5.5 s after data returns.
So while a stream session is active the feeder inserts digital silence whenever
the source delivers nothing (phone paused / A2DP suspended / jitter), and drops
frames when a source runs persistently ahead of the bridge clock (clock drift,
bursts), keeping the buffer bounded.
"""
from __future__ import annotations

import threading
import time

from ..core.ring import PcmRing

RATE = 44100
FRAME_BYTES = 4


class LiveFeeder:
    def __init__(self, ring: PcmRing, rate: int = RATE, clock=time.monotonic,
                 tolerance_ms: int = 40, max_ahead_ms: int = 150, max_fill_ms: int = 1000):
        self.ring, self.rate, self.clock = ring, rate, clock
        self.tol = tolerance_ms / 1000.0
        self.max_ahead = max_ahead_ms / 1000.0
        self.max_fill = max_fill_ms / 1000.0
        self.lock = threading.Lock()
        self.next_due = None             # time at which written audio would run dry at 1x
        self.segment_start = 0           # ring position where the current real-PCM segment began
        self.fill_enabled = False
        # counters (diagnostics; exposed in status)
        self.silence_frames = 0
        self.dropped_frames = 0
        self.pushed_frames = 0

    # -- called from the source thread ------------------------------------
    def push(self, pcm: bytes) -> None:
        n = len(pcm) // FRAME_BYTES
        if n == 0:
            return
        with self.lock:
            now = self.clock()
            if self.next_due is None:
                self.next_due = now
                self.segment_start = self.ring.head
            behind = now - self.next_due
            if behind > self.tol:                          # gap in the source
                if self.fill_enabled:
                    self._fill(min(behind, self.max_fill))
                self.next_due = now
                self.segment_start = self.ring.head        # new real-PCM segment (no stale lead-in across a gap)
            elif behind < -self.max_ahead:                 # source ahead of real time
                drop = min(n, int((-behind - self.max_ahead) * self.rate))
                if drop:
                    pcm = pcm[drop * FRAME_BYTES:]
                    n -= drop
                    self.dropped_frames += drop      # dropped audio never enters the timeline
            if n:
                self.ring.write(pcm)
                self.pushed_frames += n
                self.next_due += n / self.rate

    # -- called periodically (every ~10 ms) -------------------------------
    def tick(self) -> None:
        with self.lock:
            if not self.fill_enabled:
                return
            now = self.clock()
            if self.next_due is None:
                self.next_due = now
            behind = now - self.next_due
            if behind > self.tol:
                self._fill(min(behind, self.max_fill))
                self.next_due = now if behind >= self.max_fill else self.next_due + min(behind, self.max_fill)
                self.segment_start = self.ring.head

    def _fill(self, seconds: float) -> None:
        frames = int(seconds * self.rate)
        if frames > 0:
            self.ring.write(bytes(frames * FRAME_BYTES))
            self.silence_frames += frames

    def set_fill(self, enabled: bool) -> None:
        with self.lock:
            self.fill_enabled = enabled
            if enabled:
                self.next_due = self.clock() if self.next_due is None else self.next_due

    def lead_in_start(self, lead_ms: int) -> int:
        """Ring position a *new* client should start from: up to ``lead_ms`` of the latest live
        segment, never older audio, never across a gap, nothing if the source is idle/stale."""
        with self.lock:
            now = self.clock()
            if self.next_due is None or now - self.next_due > 0.5 or not self.ring.last_write_mono \
                    or time.monotonic() - self.ring.last_write_mono > 0.5:
                return self.ring.head
            start = max(self.ring.oldest, self.segment_start, self.ring.head - int(lead_ms * self.rate / 1000))
            return min(start, self.ring.head)

    def run(self, stop: threading.Event, period: float = 0.01) -> None:
        while not stop.wait(period):
            self.tick()
