"""Time-limited pairing window (default 5 min, accepted decision).

The window's state is only counted as applied once the adapter accepted it (``on_change`` returned). A refused change (D-Bus
error) is kept as ``error``, retried by ``tick`` every ``RETRY_S`` and reported, so the UI and the adapter never silently disagree
(review M5: a failed close used to leave the adapter discoverable indefinitely while the UI showed "closed")."""
from __future__ import annotations

import threading

from .clock import SystemClock

RETRY_S = 2.0


class PairingWindow:
    def __init__(self, duration_s: float = 300.0, clock=None, on_change=None):
        self.duration, self.clock, self.on_change = duration_s, clock or SystemClock(), on_change
        self._deadline: float | None = None
        self._last = False                     # state the adapter accepted last
        self.error: str | None = None          # last refused change (None = adapter follows the window)
        self._retry_at = 0.0
        self._lock = threading.Lock()

    def open(self) -> bool:
        """Open the window. False when the adapter refused it (retried by ``tick``)."""
        with self._lock:
            self._deadline = self.clock.now() + self.duration
            return self._notify(force=True)

    def close(self) -> bool:
        with self._lock:
            self._deadline = None
            return self._notify(force=True)

    def is_open(self) -> bool:
        return self._deadline is not None and self.clock.now() < self._deadline

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.clock.now()) if self.is_open() else 0.0

    def tick(self) -> None:
        """Call periodically so expiry reaches the adapter (``set_pairable(False)``) and refused changes are retried."""
        with self._lock:
            self._notify(force=False)

    def _notify(self, force: bool) -> bool:
        cur = self.is_open()
        if cur == self._last:
            self.error = None
            return True
        if not force and self.error is not None and self.clock.now() < self._retry_at:
            return False
        if self.on_change:
            try:
                self.on_change(cur)
            except Exception as e:             # the adapter refused: keep the old applied state, retry later
                self.error = f"{type(e).__name__}: {e}"
                self._retry_at = self.clock.now() + RETRY_S
                return False
        self._last, self.error = cur, None
        return True
