"""Audio monitor for the demo: the PCM that a Squeezelite player actually outputs, made audible in the browser.

Squeezelite (``-o -``) writes decoded PCM to stdout without a clock. A *virtual DAC* (``run_dac``) consumes it at exactly the sample rate,
like a sound card would, and publishes every chunk with the wall-clock time of its consumption. Monitor clients (the console, which relays
to the browser) subscribe over TCP. A slow client never slows the DAC: its bounded queue drops the oldest chunks instead.

Wire format, after one JSON header line: repeated frames ``b"HB" + <float64 t_dac> + <uint32 n> + n bytes of s16le stereo PCM``.
This is the output of the player (behind Lyrion and Squeezelite), not the phone's source signal.
"""
from __future__ import annotations

import json
import math
import os
import queue
import socketserver
import struct
import threading
import time

RATE, CHANNELS, FRAME_BYTES = 44100, 2, 4
MAGIC = b"HB"
HEAD = struct.Struct("<2sdI")
FLOOR_DBFS = -90.0


def frame(t: float, pcm: bytes) -> bytes:
    return HEAD.pack(MAGIC, t, len(pcm)) + pcm


class FrameParser:
    def __init__(self):
        self.buf = b""

    def feed(self, data: bytes) -> list[tuple[float, bytes]]:
        self.buf += data
        out = []
        while len(self.buf) >= HEAD.size:
            magic, t, n = HEAD.unpack_from(self.buf)
            if magic != MAGIC or n > 1 << 20:
                raise ValueError("not a monitor stream")
            if len(self.buf) < HEAD.size + n:
                break
            out.append((t, self.buf[HEAD.size: HEAD.size + n]))
            self.buf = self.buf[HEAD.size + n:]
        return out


def level(pcm: bytes) -> dict:
    """Peak and RMS of s16le PCM in dBFS (both channels)."""
    import numpy as np
    x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64) / 32768.0
    if not len(x):
        return {"peak_dbfs": FLOOR_DBFS, "rms_dbfs": FLOOR_DBFS}

    def db(v):
        return round(max(FLOOR_DBFS, 20 * math.log10(v)) if v > 0 else FLOOR_DBFS, 1)
    return {"peak_dbfs": db(float(np.abs(x).max())), "rms_dbfs": db(float(np.sqrt(np.mean(x * x))))}


class Broadcaster:
    def __init__(self, max_queue_s: float = 1.0, chunk_s: float = 0.01):
        self.maxsize = max(2, int(max_queue_s / chunk_s) + 1)
        self.listeners: list[queue.Queue] = []
        self.dropped = 0
        self.last_level = {"peak_dbfs": FLOOR_DBFS, "rms_dbfs": FLOOR_DBFS}
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(self.maxsize)
        with self._lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def publish(self, t: float, pcm: bytes) -> None:
        with self._lock:
            listeners = list(self.listeners)
        for q in listeners:
            while True:
                try:
                    q.put_nowait((t, pcm))
                    break
                except queue.Full:
                    try:
                        q.get_nowait()                    # drop the oldest: the listener is too slow
                        self.dropped += 1
                    except queue.Empty:
                        pass


class DacTap:
    """``feed`` target for ``run_dac``: publishes each consumed chunk and keeps a level reading."""

    rate, frame_bytes = RATE, FRAME_BYTES

    def __init__(self, broadcaster: Broadcaster):
        self.b = broadcaster
        self.frames = 0
        self._n = 0

    def feed(self, data: bytes, t: float | None = None) -> None:
        t = time.time() if t is None else t
        self.frames += len(data) // FRAME_BYTES
        self._n += 1
        if self._n % 10 == 0:                              # 10 Hz is plenty for a meter
            self.b.last_level = level(data)
        self.b.publish(t, data)


def run_dac(fd: int, tap: DacTap, chunk_s: float = 0.010) -> None:
    """Consume ``fd`` at exactly the sample rate (resynchronises after > 1 s of starvation). Returns at EOF."""
    nbytes = int(RATE * chunk_s) * FRAME_BYTES
    next_t = time.monotonic()
    while True:
        buf = b""
        while len(buf) < nbytes:
            d = os.read(fd, nbytes - len(buf))
            if not d:
                if buf:
                    tap.feed(buf)
                return
            buf += d
        tap.feed(buf)
        next_t += chunk_s
        now = time.monotonic()
        if next_t > now:
            time.sleep(next_t - now)
        elif now - next_t > 1.0:
            next_t = now


def serve(b: Broadcaster, port: int, host: str = "0.0.0.0", info: dict | None = None):  # nosec B104 - internal demo network only
    header = (json.dumps({"rate": RATE, "channels": CHANNELS, "format": "s16le", **(info or {})}) + "\n").encode()

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            q = b.subscribe()
            try:
                self.request.sendall(header)
                while True:
                    try:
                        t, pcm = q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    self.request.sendall(frame(t, pcm))
            except OSError:
                pass
            finally:
                b.unsubscribe(q)

    class S(socketserver.ThreadingTCPServer):
        daemon_threads, allow_reuse_address = True, True
    srv = S((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="monitor").start()
    return srv
