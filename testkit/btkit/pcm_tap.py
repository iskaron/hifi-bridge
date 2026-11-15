"""PCM tap: a paced *virtual DAC* that timestamps and records player output PCM.

Squeezelite's ``-o -`` stdout output is NOT clocked (measured: it floods the pipe
at memory speed), so the tap consumes at exactly the sample rate, creating
back-pressure like a sound card would.  Recorded time is therefore "when the
virtual DAC consumed this frame", accurate to roughly one pipe buffer
(<= PIPE_BYTES / rate ~ 23 ms) plus squeezelite's own output buffering.  This is
software player-output timing, NOT acoustic/speaker timing.

Index format (JSON lines): [t_wall_end, nbytes, stored_offset | -1].
Digital silence is coalesced (>=0.25 s entries) and, beyond 1 s of continuous
silence, not stored, to bound disk use.
"""
from __future__ import annotations

import fcntl
import json
import os
import time

F_SETPIPE_SZ = 1031
PIPE_BYTES = 4096
SILENCE_STORE_LIMIT_S = 1.0
SILENCE_INDEX_FLUSH_S = 0.25


class PcmTap:
    def __init__(self, outdir: str, rate: int = 44100, frame_bytes: int = 4):
        os.makedirs(outdir, exist_ok=True)
        self.rate, self.frame_bytes = rate, frame_bytes
        self.pcm = open(os.path.join(outdir, "pcm.raw"), "wb", buffering=0)
        self.idx = open(os.path.join(outdir, "index.jsonl"), "w", buffering=1)
        self.stored = 0
        self.silent_run_s = 0.0
        self.pending_sil_bytes = 0
        self.pending_sil_dur = 0.0

    def _flush_silence(self, t):
        if self.pending_sil_bytes:
            self.idx.write(json.dumps([round(t, 6), self.pending_sil_bytes, -1]) + "\n")
            self.pending_sil_bytes = 0
            self.pending_sil_dur = 0.0

    def feed(self, data: bytes, t: float | None = None) -> None:
        t = time.time() if t is None else t
        if not data:
            return
        dur = len(data) / (self.rate * self.frame_bytes)
        if not any(data):
            self.silent_run_s += dur
            if self.silent_run_s > SILENCE_STORE_LIMIT_S:
                self.pending_sil_bytes += len(data)
                self.pending_sil_dur += dur
                if self.pending_sil_dur >= SILENCE_INDEX_FLUSH_S:
                    self._flush_silence(t)
                return
        else:
            self.silent_run_s = 0.0
        # stored chunk: first flush any coalesced silence that ended just before it
        self._flush_silence(t - dur)
        off = self.stored
        self.pcm.write(data)
        self.stored += len(data)
        self.idx.write(json.dumps([round(t, 6), len(data), off]) + "\n")


def set_pipe_size(fd: int, size: int = PIPE_BYTES) -> None:
    try:
        fcntl.fcntl(fd, F_SETPIPE_SZ, size)
    except OSError:
        pass


def paced_read_loop(fd: int, tap: PcmTap, chunk_s: float = 0.010) -> None:
    """Consume ``fd`` at exactly the sample rate (resyncs if starved > 1 s)."""
    nbytes = int(tap.rate * chunk_s) * tap.frame_bytes
    next_t = time.monotonic()
    while True:
        buf = b""
        while len(buf) < nbytes:
            d = os.read(fd, nbytes - len(buf))
            if not d:
                return
            buf += d
        tap.feed(buf)
        next_t += chunk_s
        now = time.monotonic()
        if next_t > now:
            time.sleep(next_t - now)
        elif now - next_t > 1.0:
            next_t = now
