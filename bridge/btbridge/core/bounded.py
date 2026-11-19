"""In-memory audit trails that must not grow without bound on a device that runs for months (review M7)."""
from __future__ import annotations


class BoundedList(list):
    """A list that keeps only the newest ``maxlen`` items. Unlike ``collections.deque`` it still supports slicing, which
    callers and tests use (``events[-100:]``). Trimming happens in chunks, so ``append`` stays amortised O(1)."""

    def __init__(self, maxlen: int, items=()):
        super().__init__(items)
        self.maxlen = maxlen
        self._trim()

    def _trim(self) -> None:
        if len(self) > self.maxlen + max(1, self.maxlen // 4):
            del self[:len(self) - self.maxlen]

    def append(self, item) -> None:
        super().append(item)
        self._trim()

    def extend(self, items) -> None:
        super().extend(items)
        self._trim()

    def __iadd__(self, items):
        self.extend(items)
        return self
