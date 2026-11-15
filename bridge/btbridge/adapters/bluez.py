"""Real BlueZ/PipeWire adapter - NOT IMPLEMENTED in phase 1 (no radio on the dev host).

The mapping to BlueZ D-Bus objects is documented in docs/BLUEZ_ADAPTER_DESIGN.md and is
UNTESTED.  This stub exists so the interface boundary is explicit and a future
implementation drops in without touching core logic.
"""
from __future__ import annotations


class BluezAdapter:
    def __init__(self, *a, **kw):
        raise NotImplementedError("Hardware adapter is deferred hardware validation work; use the simulated source in phase 1.")
