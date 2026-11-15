"""BlueZ D-Bus trace recorder: every PropertiesChanged / InterfacesAdded / InterfacesRemoved of ``org.bluez`` (plus a snapshot at start and
operator markers) is written as one JSON line with wall-clock and monotonic timestamps.  Uses the bridge's ``BlueZBus`` implementation,
so what is recorded is exactly what the bridge's translator would see.  MAC addresses are pseudonymised by default."""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from btbridge.adapters.bluez.bus import (InterfacesAdded, InterfacesRemoved, PropertiesChanged, ServiceAppeared,
                                         ServiceVanished)

from .anon import Anonymizer


class TraceRecorder:
    def __init__(self, bus, path: str, label: str = "REAL-HARDWARE", anonymize: bool = True, meta: dict | None = None,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic):
        self.bus, self.path, self.label, self.clock, self.mono = bus, path, label, clock, mono
        self.anon = Anonymizer(anonymize)
        self.meta = meta or {}
        self._fh = None
        self._lock = threading.Lock()
        self.count = 0

    def _write(self, rec: dict) -> None:
        rec = {"t": round(self.clock(), 6), "mono": round(self.mono(), 6), **rec}
        with self._lock:
            self._fh.write(json.dumps(self.anon.value(rec), sort_keys=True) + "\n")
            self._fh.flush()
            self.count += 1

    def start(self) -> None:
        self._fh = open(self.path, "w", encoding="utf-8")
        self._write({"type": "Header", "label": self.label, "anonymized": self.anon.enabled, "format": 1, **self.meta})
        self.bus.subscribe(self._on_event)
        self._write({"type": "Snapshot", "objects": self.bus.managed_objects()})

    def _on_event(self, ev) -> None:
        if isinstance(ev, PropertiesChanged):
            self._write({"type": "PropertiesChanged", "path": ev.path, "interface": ev.interface, "changed": ev.changed,
                         "invalidated": list(ev.invalidated)})
        elif isinstance(ev, InterfacesAdded):
            self._write({"type": "InterfacesAdded", "path": ev.path, "interfaces": ev.interfaces})
        elif isinstance(ev, InterfacesRemoved):
            self._write({"type": "InterfacesRemoved", "path": ev.path, "interfaces": list(ev.interfaces)})
        elif isinstance(ev, (ServiceAppeared, ServiceVanished)):
            self._write({"type": type(ev).__name__})

    def marker(self, text: str) -> None:
        """Operator annotation, e.g. 'phone: Spotify play', 'expect:media', 'expect:no-media' (used by replay checks)."""
        self._write({"type": "Marker", "text": text})

    def stop(self) -> None:
        if self._fh:
            self._write({"type": "End", "events": self.count})
            self._fh.close()
            self._fh = None
        try:
            self.bus.close()
        except Exception:
            pass


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
