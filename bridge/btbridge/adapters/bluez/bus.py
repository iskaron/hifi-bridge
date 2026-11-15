"""Bus abstraction between the BlueZ adapter logic and a concrete D-Bus library.

The adapter logic (``model.py``/``adapter.py``) only sees plain Python values and the small
``BlueZBus`` interface below, so it can be exercised with scripted events (UNIT), against a
*fake BlueZ service on a private D-Bus daemon* (INTEGRATION, SIMULATED-BLUEZ) and, later,
against a real ``bluetoothd`` on hardware (REAL-HARDWARE, UNTESTED).

Property values are already unwrapped from D-Bus variants (``uint16 -> int``, ``a{sv} -> dict``).
Object paths and interface names follow the BlueZ D-Bus API documentation (docs/BLUEZ_ADAPTER_DESIGN.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

BLUEZ_SERVICE = "org.bluez"
IF_ADAPTER = "org.bluez.Adapter1"
IF_DEVICE = "org.bluez.Device1"
IF_PLAYER = "org.bluez.MediaPlayer1"
IF_TRANSPORT = "org.bluez.MediaTransport1"
IF_AGENT_MANAGER = "org.bluez.AgentManager1"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"     # remote device acts as A2DP *source* (a phone)

Props = dict[str, Any]


@dataclass(frozen=True)
class PropertiesChanged:
    path: str
    interface: str
    changed: Props
    invalidated: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterfacesAdded:
    path: str
    interfaces: dict[str, Props] = field(default_factory=dict)


@dataclass(frozen=True)
class InterfacesRemoved:
    path: str
    interfaces: tuple[str, ...] = ()


BusEvent = PropertiesChanged | InterfacesAdded | InterfacesRemoved
AgentHandler = Callable[[str, str, tuple], None]     # (method, device_path, args); raise Rejected to refuse


class Rejected(Exception):
    """Raised by an agent handler to answer with org.bluez.Error.Rejected."""


class BusError(Exception):
    """A D-Bus call failed (service missing, method error, timeout)."""


class BlueZBus(Protocol):
    def managed_objects(self) -> dict[str, dict[str, Props]]: ...
    def subscribe(self, handler: Callable[[BusEvent], None]) -> None: ...
    def call(self, path: str, interface: str, method: str, signature: str = "", args: tuple = ()) -> Any: ...
    def set_property(self, path: str, interface: str, name: str, signature: str, value: Any) -> None: ...
    def register_agent(self, path: str, capability: str, handler: AgentHandler) -> None: ...
    def close(self) -> None: ...
