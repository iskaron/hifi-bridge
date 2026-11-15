"""Time-limited pairing window (default 5 min, accepted decision)."""
from __future__ import annotations

from .clock import SystemClock


class PairingWindow:
    def __init__(self, duration_s: float = 300.0, clock=None, on_change=None):
        self.duration, self.clock, self.on_change = duration_s, clock or SystemClock(), on_change
        self._deadline: float | None = None
        self._last = False

    def open(self) -> None:
        self._deadline = self.clock.now() + self.duration
        self._notify()

    def close(self) -> None:
        self._deadline = None
        self._notify()

    def is_open(self) -> bool:
        return self._deadline is not None and self.clock.now() < self._deadline

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.clock.now()) if self.is_open() else 0.0

    def tick(self) -> None:
        """Call periodically so expiry reaches the adapter (``set_pairable(False)``)."""
        self._notify()

    def _notify(self) -> None:
        cur = self.is_open()
        if cur != self._last:
            self._last = cur
            if self.on_change:
                self.on_change(cur)
