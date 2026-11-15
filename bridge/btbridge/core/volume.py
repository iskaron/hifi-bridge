"""Phone volume -> LMS volume policy (pure function; no I/O).

Goals (docs/PRODUCT_SPEC.md): never an unpredictable loud jump; preserve the
room's relative volume.  Therefore:
  * the first value after connect is only a *reference* (phones often report their
    own arbitrary level on connect) -> no change;
  * later changes are applied as *relative deltas* (``mixer volume +N``) scaled
    to LMS 0..100, so a group's relative levels survive;
  * each event moves at most ``max_step`` (the excess of a larger jump is dropped); the result never exceeds ``ceiling``
    (the ceiling only limits *increases*: a user already above it is not forced down).
"""
from __future__ import annotations

AVRCP_MAX = 127


class VolumePolicy:
    def __init__(self, max_step: int = 10, ceiling: int = 85):
        self.max_step, self.ceiling = max_step, ceiling
        self.ref: int | None = None

    def reset(self) -> None:
        self.ref = None

    def on_phone_volume(self, value: int, current_lms: int | None) -> int | None:
        """Return a signed LMS delta to apply, or None for no change."""
        value = max(0, min(AVRCP_MAX, int(value)))
        if self.ref is None:
            self.ref = value
            return None
        raw = round((value - self.ref) * 100 / AVRCP_MAX)
        if raw == 0:
            return None
        delta = max(-self.max_step, min(self.max_step, raw))
        # The reference follows the phone fully: the excess of a capped jump is DROPPED, never
        # replayed on later events (replaying it made a small phone volume-down raise LMS volume;
        # found by the integration test, regression-tested in test_components.py).
        self.ref = value
        if current_lms is not None and delta > 0:
            delta = min(delta, max(0, self.ceiling - current_lms))
        return delta or None
