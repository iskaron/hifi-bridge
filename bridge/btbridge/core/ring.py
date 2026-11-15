"""Bounded PCM ring buffer with independent reader cursors (live-edge semantics)."""
from __future__ import annotations

import threading
import time


class PcmRing:
    """Fixed-capacity frame ring. Memory is bounded by ``capacity_frames * frame_bytes``.

    Writers append; readers keep their own absolute frame position.  A reader that
    falls more than ``capacity`` behind is skipped forward to the oldest retained
    frame (stale audio is dropped, never replayed late) and the skip is counted.
    """

    def __init__(self, capacity_frames: int, frame_bytes: int = 4):
        self.cap = capacity_frames
        self.fb = frame_bytes
        self.buf = bytearray(capacity_frames * frame_bytes)
        self.head = 0                      # total frames ever written
        self.cond = threading.Condition()
        self._partial = b""
        self.closed = False
        self.last_write_mono = 0.0

    @property
    def oldest(self) -> int:
        return max(0, self.head - self.cap)

    def write(self, data: bytes) -> int:
        """Append whole frames; a trailing partial frame is kept for the next write."""
        with self.cond:
            if self._partial:
                data = self._partial + data
            n = len(data) // self.fb
            self._partial = data[n * self.fb:]
            if n == 0:
                return 0
            data = data[: n * self.fb]
            if n >= self.cap:               # only the newest ``cap`` frames can matter
                data = data[(n - self.cap) * self.fb:]
                skipped = n - self.cap
                self.head += skipped
                n = self.cap
            start = (self.head % self.cap) * self.fb
            first = min(len(data), len(self.buf) - start)
            self.buf[start:start + first] = data[:first]
            if first < len(data):
                self.buf[0:len(data) - first] = data[first:]
            self.head += n
            self.last_write_mono = time.monotonic()
            self.cond.notify_all()
            return n

    def read(self, pos: int, max_frames: int, timeout: float = 0.5):
        """Return ``(new_pos, data, skipped_frames)``; blocks up to ``timeout`` if no data."""
        with self.cond:
            if self.head <= pos and not self.closed:
                self.cond.wait(timeout)
            skipped = 0
            if pos < self.oldest:
                skipped = self.oldest - pos
                pos = self.oldest
            n = min(max_frames, self.head - pos)
            if n <= 0:
                return pos, b"", skipped
            start = (pos % self.cap) * self.fb
            first = min(n * self.fb, len(self.buf) - start)
            out = bytes(self.buf[start:start + first])
            if first < n * self.fb:
                out += bytes(self.buf[0:n * self.fb - first])
            return pos + n, out, skipped

    def close(self) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify_all()
