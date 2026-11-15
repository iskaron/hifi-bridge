"""Pure translator: BlueZ object-model events -> ``AudioSourceAdapter`` callbacks.

No I/O, no threads, no D-Bus library: input are ``BusEvent``s and an initial object snapshot, output
are the callbacks of ``adapters/base.py`` plus ``Device1.Disconnect`` calls through the bus.  The rules
are derived from BlueZ *documentation*; real phone behaviour is UNVERIFIED (deferred hardware validation).

Rules
  * Exactly one active source.  A second connecting device is refused with ``Device1.Disconnect`` and never
    evicts the first (docs/PRODUCT_SPEC.md).  A refused device stays ignored until it disconnects.
  * Devices whose known ``UUIDs`` lack the A2DP-source UUID are ignored (keyboards, headsets...).  If UUIDs are
    not (yet) known the device is treated as a phone - a hardware-validation item.
  * ``MediaTransport1.State``/``Volume`` and ``MediaPlayer1.Status``/``Track`` are forwarded only for the active
    device; objects that appear *before* the device's ``Connected`` event are replayed on connect.
  * Removal of a transport -> ``on_transport('idle')``; removal of a player -> ``on_avrcp(None)``.
"""
from __future__ import annotations

from typing import Callable

from .bus import (A2DP_SOURCE_UUID, IF_DEVICE, IF_PLAYER, IF_TRANSPORT, BlueZBus, BusError, BusEvent,
                  InterfacesAdded, InterfacesRemoved, PropertiesChanged)

PLAYER_STATUS = {"playing": "playing", "paused": "paused", "stopped": "stopped",
                 "forward-seek": "playing", "reverse-seek": "playing"}      # 'error'/unknown -> None
TRANSPORT_STATES = ("idle", "pending", "active")


def device_id(props: dict, path: str) -> str:
    """Stable id of a device: its Bluetooth address, else derived from the object path (dev_AA_BB_...)."""
    addr = props.get("Address")
    if addr:
        return str(addr).upper()
    leaf = path.rsplit("/", 1)[-1]
    return leaf[4:].replace("_", ":").upper() if leaf.startswith("dev_") else path


class BluezModel:
    def __init__(self, cb, bus: BlueZBus, log: Callable[[str], None] = lambda m: None):
        self.cb, self.bus, self.log = cb, bus, log
        self.devices: dict[str, dict] = {}
        self.transports: dict[str, dict] = {}
        self.players: dict[str, dict] = {}
        self.active: str | None = None                  # object path of the active device
        self.ignored: set[str] = set()                  # refused or not-a-phone devices (until they disconnect)
        self._last_track: tuple | None = None

    # ---- input ---------------------------------------------------------------------------------------------
    def load(self, objects: dict[str, dict[str, dict]]) -> None:
        """Initial snapshot (``GetManagedObjects``): devices before transports/players so replay works."""
        for iface in (IF_DEVICE, IF_TRANSPORT, IF_PLAYER):
            for path, ifaces in objects.items():
                if iface in ifaces:
                    self._apply(path, iface, dict(ifaces[iface]), added=True)

    def handle(self, ev: BusEvent) -> None:
        if isinstance(ev, PropertiesChanged):
            self._apply(ev.path, ev.interface, dict(ev.changed), added=False)
        elif isinstance(ev, InterfacesAdded):
            for iface in (IF_DEVICE, IF_TRANSPORT, IF_PLAYER):
                if iface in ev.interfaces:
                    self._apply(ev.path, iface, dict(ev.interfaces[iface]), added=True)
        elif isinstance(ev, InterfacesRemoved):
            self._removed(ev.path, ev.interfaces)

    # ---- internals -------------------------------------------------------------------------------------------
    def _apply(self, path: str, iface: str, props: dict, added: bool) -> None:
        if iface == IF_DEVICE:
            self._device(path, props, added)
        elif iface == IF_TRANSPORT:
            self.transports.setdefault(path, {}).update(props)
            self._forward_transport(path, props)
        elif iface == IF_PLAYER:
            self.players.setdefault(path, {}).update(props)
            self._forward_player(path, props)

    def _device(self, path: str, props: dict, added: bool) -> None:
        d = self.devices.setdefault(path, {})
        was = bool(d.get("Connected", False))
        d.update(props)
        now = bool(d.get("Connected", False))
        if now and not was:
            self._connected(path)
        elif was and not now:
            self._disconnected(path)

    def _connected(self, path: str) -> None:
        d = self.devices[path]
        if path in self.ignored or path == self.active:
            return
        uuids = [str(u).lower() for u in d.get("UUIDs", [])]
        if uuids and A2DP_SOURCE_UUID not in uuids:
            self.ignored.add(path)
            self.log(f"ignoring non-audio-source device {device_id(d, path)}")
            return
        if self.active is not None:                     # one source at a time; never evict the first
            self._refuse(path, "second source")
            return
        if self.cb.on_connect(device_id(d, path)) is False:
            self._refuse(path, "refused by bridge")
            return
        self.active = path
        self._replay(path)

    def _refuse(self, path: str, why: str) -> None:
        self.ignored.add(path)
        self.log(f"refusing {device_id(self.devices.get(path, {}), path)}: {why}")
        try:
            self.bus.call(path, IF_DEVICE, "Disconnect")
        except BusError as e:
            self.log(f"Disconnect failed: {e}")

    def _disconnected(self, path: str) -> None:
        self.ignored.discard(path)
        if path != self.active:
            return
        self.active, self._last_track = None, None
        self.cb.on_disconnect(device_id(self.devices.get(path, {}), path))

    def _belongs_to_active(self, obj_props: dict) -> bool:
        return self.active is not None and obj_props.get("Device") == self.active

    def _replay(self, dev_path: str) -> None:
        for p, props in list(self.transports.items()):
            if props.get("Device") == dev_path:
                self._forward_transport(p, props)
        for p, props in list(self.players.items()):
            if props.get("Device") == dev_path:
                self._forward_player(p, props)

    def _forward_transport(self, path: str, changed: dict) -> None:
        full = self.transports[path]
        if not self._belongs_to_active(full):
            return
        if "State" in changed and changed["State"] in TRANSPORT_STATES:
            self.cb.on_transport(changed["State"])
        if "Volume" in changed:
            try:
                self.cb.on_volume(max(0, min(127, int(changed["Volume"]))))
            except (TypeError, ValueError):
                pass

    def _forward_player(self, path: str, changed: dict) -> None:
        full = self.players[path]
        if not self._belongs_to_active(full):
            return
        if "Status" in changed:
            self.cb.on_avrcp(PLAYER_STATUS.get(str(changed["Status"])))
        if "Track" in changed and isinstance(changed["Track"], dict):
            t = changed["Track"]
            key = (str(t.get("Title", "")), str(t.get("Artist", "")), str(t.get("Album", "")))
            if any(key) and key != self._last_track:
                self._last_track = key
                self.cb.on_metadata(*key)

    def _removed(self, path: str, ifaces: tuple) -> None:
        if IF_TRANSPORT in ifaces and path in self.transports:
            gone = self.transports.pop(path)
            if self._belongs_to_active(gone):
                self.cb.on_transport("idle")
        if IF_PLAYER in ifaces and path in self.players:
            gone = self.players.pop(path)
            if self._belongs_to_active(gone):
                self.cb.on_avrcp(None)
        if IF_DEVICE in ifaces and path in self.devices:        # device object removed = unpaired / gone
            if self.devices[path].get("Connected"):
                self.devices[path]["Connected"] = False
                self._disconnected(path)
            self.devices.pop(path, None)
            self.ignored.discard(path)
