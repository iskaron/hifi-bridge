"""Construction of the real adapter for a target device.  Only called when the operator explicitly asks for ``--source bluez``;
nothing in the lab or the tests ever connects to a system bus.  Raises ``BusError`` with a clear message instead of
pretending to work when D-Bus/jeepney are unavailable."""
from __future__ import annotations

from typing import Callable

from .adapter import ADAPTER_PATH_DEFAULT, BluezAdapter
from .jeepney_bus import JeepneyBus
from .pcm import PipeWireCapture


def create_adapter(bus: str = "SYSTEM", adapter_path: str = ADAPTER_PATH_DEFAULT, log: Callable[[str], None] = print) -> BluezAdapter:
    return BluezAdapter(JeepneyBus(bus=bus, log=log), PipeWireCapture(log=log), adapter_path=adapter_path, log=log)
