"""Construction of the real adapter for a target device.  Only called when the operator explicitly asks for ``--source bluez``;
nothing in the lab or the tests ever connects to a system bus.  Raises ``BusError`` with a clear message instead of
pretending to work when D-Bus/jeepney are unavailable."""
from __future__ import annotations

from typing import Callable

from .adapter import ADAPTER_PATH_DEFAULT, BluezAdapter
from .jeepney_bus import JeepneyBus
from .pcm import PipeWireCapture


def create_adapter(bus: str = "SYSTEM", adapter_path: str = ADAPTER_PATH_DEFAULT, log: Callable[[str], None] = print) -> BluezAdapter:
    from .pcm import choose_capture, find_phone_node
    name, argv_for_node = choose_capture()
    log(f"capture tool: {name}; capture target: the phone's Audio/Source node found with pw-dump (docs/PIPEWIRE_CAPTURE.md G1)")
    capture = PipeWireCapture(resolve=find_phone_node, argv_for_node=argv_for_node, log=lambda m: log(m))
    return BluezAdapter(JeepneyBus(bus=bus, log=log), capture, adapter_path=adapter_path, log=log)
