"""Injectable clocks so timing logic (grace periods, filters, pairing) is testable."""
from __future__ import annotations

import time


class SystemClock:
    def now(self) -> float:      # monotonic seconds
        return time.monotonic()

    def wall(self) -> float:     # wall-clock seconds (for log/measurement timestamps only)
        return time.time()


class FakeClock:
    """Deterministic clock for unit tests."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def wall(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt
