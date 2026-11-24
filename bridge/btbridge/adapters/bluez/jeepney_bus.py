"""``BlueZBus`` implementation on the pure-Python ``jeepney`` D-Bus library (MIT, optional dependency).

Hardware-free status: exercised against a *fake* BlueZ service on a private D-Bus daemon (Docker; label
INTEGRATION/SIMULATED-BLUEZ).  NOT yet run against a real ``bluetoothd``.  Choosing jeepney is provisional: it is small and
dependency-free, but its availability in the Buildroot image must be checked in phase 2 (alternative: any D-Bus binding
implementing the ``BlueZBus`` interface).
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from .bus import (BLUEZ_SERVICE, IF_AGENT_MANAGER, AgentHandler, BusError, BusEvent, InterfacesAdded, InterfacesRemoved,
                  PropertiesChanged, Rejected, ServiceAppeared, ServiceVanished)

IF_PROPS = "org.freedesktop.DBus.Properties"
IF_OBJMGR = "org.freedesktop.DBus.ObjectManager"
IF_AGENT = "org.bluez.Agent1"


def unwrap(variant: Any) -> Any:
    """jeepney variant ``(signature, value)`` -> plain Python (nested ``a{sv}`` dicts unwrapped recursively)."""
    sig, val = variant
    if sig == "a{sv}":
        return {k: unwrap(v) for k, v in val.items()}
    if sig == "v":
        return unwrap(val)
    return val


def unwrap_props(d: dict) -> dict:
    return {k: unwrap(v) for k, v in d.items()}


class JeepneyBus:
    def __init__(self, bus: str = "SYSTEM", timeout: float = 5.0, log: Callable[[str], None] = lambda m: None):
        try:
            from jeepney.io.threading import DBusRouter, open_dbus_connection
        except ImportError as e:                                        # optional dependency
            raise BusError("python 'jeepney' is not installed") from e
        try:
            self._conn = open_dbus_connection(bus=bus)
            self.router = DBusRouter(self._conn)
        except Exception as e:
            raise BusError(f"cannot connect to the D-Bus {bus!r}: {e}") from e
        self.timeout, self.log = timeout, log
        self._q: queue.Queue = queue.Queue()
        self._handler: Callable[[BusEvent], None] | None = None
        self._agent: tuple[str, AgentHandler, str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._filters: list = []

    # ---- calls -----------------------------------------------------------------------------------------------
    def _send(self, msg):
        from jeepney import MessageType
        try:
            reply = self.router.send_and_get_reply(msg, timeout=self.timeout)
        except Exception as e:
            raise BusError(f"{type(e).__name__}: {e}") from e
        if reply.header.message_type == MessageType.error:
            from jeepney import HeaderFields
            raise BusError(f"{reply.header.fields.get(HeaderFields.error_name, 'error')}: {reply.body}")
        return reply

    def _addr(self, path: str, interface: str):
        from jeepney import DBusAddress
        return DBusAddress(path, bus_name=BLUEZ_SERVICE, interface=interface)

    def managed_objects(self) -> dict[str, dict[str, dict]]:
        from jeepney import new_method_call
        reply = self._send(new_method_call(self._addr("/", IF_OBJMGR), "GetManagedObjects"))
        return {path: {iface: unwrap_props(props) for iface, props in ifaces.items()} for path, ifaces in reply.body[0].items()}

    def call(self, path: str, interface: str, method: str, signature: str = "", args: tuple = ()) -> Any:
        from jeepney import new_method_call
        return self._send(new_method_call(self._addr(path, interface), method, signature, args)).body

    def set_property(self, path: str, interface: str, name: str, signature: str, value: Any) -> None:
        from jeepney import new_method_call
        self._send(new_method_call(self._addr(path, IF_PROPS), "Set", "ssv", (interface, name, (signature, value))))

    # ---- signals + agent -------------------------------------------------------------------------------------
    def subscribe(self, handler: Callable[[BusEvent], None]) -> None:
        from jeepney import MatchRule
        from jeepney.bus_messages import message_bus
        self._handler = handler
        # (bus-side rule with the well-known sender, local filter without it): the bus resolves "org.bluez" to the current
        # owner, but jeepney's local filter compares the *unique* sender name, so the local rule must not carry a sender.
        def pair(**kw):
            return MatchRule(sender=BLUEZ_SERVICE, **kw), MatchRule(**kw)
        rules = [pair(type="signal", interface=IF_PROPS, member="PropertiesChanged", path_namespace="/org/bluez"),
                 pair(type="signal", interface=IF_OBJMGR, member="InterfacesAdded"),
                 pair(type="signal", interface=IF_OBJMGR, member="InterfacesRemoved"),
                 (MatchRule(type="signal", sender="org.freedesktop.DBus", interface="org.freedesktop.DBus", member="NameOwnerChanged"),) * 2]
        for bus_rule, local_rule in rules:
            self._send(message_bus.AddMatch(bus_rule))
            self._filters.append(self.router.filter(local_rule, queue=self._q))
        self._thread = threading.Thread(target=self._reader, daemon=True, name="bluez-bus")
        self._thread.start()

    def register_agent(self, path: str, capability: str, handler: AgentHandler) -> None:
        from jeepney import MatchRule
        self._agent = (path, handler, capability)
        self._filters.append(self.router.filter(MatchRule(type="method_call", path=path), queue=self._q))
        self._register_agent_with_bluez()

    def _register_agent_with_bluez(self) -> None:
        path, _, capability = self._agent
        self.call("/org/bluez", IF_AGENT_MANAGER, "RegisterAgent", "os", (path, capability))
        self.call("/org/bluez", IF_AGENT_MANAGER, "RequestDefaultAgent", "o", (path,))

    def reregister_agent(self) -> None:
        if self._agent:
            self._register_agent_with_bluez()

    def _reader(self) -> None:
        from jeepney import HeaderFields, MessageType
        while not self._stop.is_set():
            try:
                msg = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                f = msg.header.fields
                if msg.header.message_type == MessageType.method_call:
                    self._agent_call(msg)
                    continue
                member = f.get(HeaderFields.member)
                ev: BusEvent | None = None
                if member == "PropertiesChanged":
                    iface, changed, inval = msg.body
                    ev = PropertiesChanged(f[HeaderFields.path], iface, unwrap_props(changed), tuple(inval))
                elif member == "InterfacesAdded":
                    path, ifaces = msg.body
                    ev = InterfacesAdded(path, {i: unwrap_props(p) for i, p in ifaces.items()})
                elif member == "InterfacesRemoved":
                    path, ifaces = msg.body
                    ev = InterfacesRemoved(path, tuple(ifaces))
                elif member == "NameOwnerChanged":
                    name, _old, new = msg.body
                    if name == BLUEZ_SERVICE:
                        ev = ServiceAppeared() if new else ServiceVanished()
                if ev is not None and self._handler is not None:
                    self._handler(ev)
            except Exception as e:                                       # keep the reader alive
                self.log(f"bus reader error: {type(e).__name__}: {e}")

    def _agent_call(self, msg) -> None:
        from jeepney import HeaderFields, new_error, new_method_return
        f = msg.header.fields
        member = f.get(HeaderFields.member, "")
        if self._agent is None or f.get(HeaderFields.interface) not in (IF_AGENT, None):
            self.router.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (member,)))
            return
        handler = self._agent[1]
        device = msg.body[0] if msg.body and isinstance(msg.body[0], str) else ""
        try:
            if member in ("Release", "Cancel"):
                self.router.send(new_method_return(msg))
                return
            handler(member, device, tuple(msg.body))
            self.router.send(new_method_return(msg))
        except Rejected as e:
            self.router.send(new_error(msg, "org.bluez.Error.Rejected", "s", (str(e),)))
        except Exception as e:
            self.log(f"agent handler error: {type(e).__name__}: {e}")
            self.router.send(new_error(msg, "org.bluez.Error.Failed", "s", (str(e),)))

    def close(self) -> None:
        self._stop.set()
        for f in self._filters:
            try:
                f.close()
            except Exception:
                pass
        for closer in (self.router.close, self._conn.close):       # router first: stops its receiver thread cleanly
            try:
                closer()
            except Exception:
                pass
