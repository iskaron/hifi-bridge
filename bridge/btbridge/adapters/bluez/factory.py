"""Construction of the real adapter for a target device.  Only called when the operator explicitly asks for ``--source bluez``;
nothing in the lab or the tests ever connects to a system bus.  Raises ``BusError`` with a clear message instead of
pretending to work when D-Bus/jeepney are unavailable."""
from __future__ import annotations

from typing import Callable

from .adapter import ADAPTER_PATH_DEFAULT, BluezAdapter
from .jeepney_bus import JeepneyBus
from .pcm import PipeWireCapture


def create_adapter(bus: str = "SYSTEM", adapter_path: str = ADAPTER_PATH_DEFAULT, log: Callable[[str], None] = print) -> BluezAdapter:
    from .pcm import choose_capture, node_name_for, source_present
    name, argv_for_node = choose_capture()
    log(f"capture tool: {name}")
    capture = PipeWireCapture(argv_for=lambda addr: argv_for_node(node_name_for(addr)), log=log,
                              present=lambda addr: source_present(node_name_for(addr)) if name == "parec" else True)
    return BluezAdapter(JeepneyBus(bus=bus, log=log), capture, adapter_path=adapter_path, log=log)
