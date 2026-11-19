"""Background work for the GUI: a function runs in a Python thread with a cancel event and a progress callback; results arrive as Qt
signals (queued to the GUI thread). Sizes are passed as Python objects (cards are larger than 2**31 bytes)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class Task(QObject):
    progress = Signal(str, object, object)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()
        self.running = False

    def start(self) -> "Task":
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self) -> None:
        try:
            result = self.fn(self.cancel, lambda stage, done, total: self.progress.emit(stage, done, total))
        except Exception as e:                       # noqa: BLE001 - every failure is shown to the user
            self.running = False
            self.failed.emit(e)
        else:
            self.running = False
            self.succeeded.emit(result)
