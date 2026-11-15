"""BlueZ/PipeWire ``AudioSourceAdapter`` - composition of bus + translator + PCM capture + pairing.

  * Control plane (connections, AVRCP, volume, pairing): ``BlueZBus`` + ``BluezModel``.
  * Audio plane: ``PcmCapture`` is started while the active phone's transport is pending/active and stopped on idle/disconnect.
  * Pairing: ``set_pairable`` mirrors the bridge's 5-minute pairing window to ``Adapter1.Pairable``/``Discoverable``;
    the pairing agent accepts authorization only while that window is open.

STATUS: logic verified hardware-free (UNIT with scripted events; INTEGRATION against a *fake* BlueZ service on a private
D-Bus daemon).  NOT verified against a real bluetoothd, real phones or a Pi.  Never reported as a working hardware adapter
until docs/HARDWARE_VALIDATION_PLAN.md has been executed.
"""
from __future__ import annotations

import threading
from typing import Callable

from .bus import IF_ADAPTER, BlueZBus, BusError, Rejected, ServiceAppeared
from .model import BluezModel
from .pcm import PcmCapture

AGENT_PATH = "/org/btbridge/agent"
ADAPTER_PATH_DEFAULT = "/org/bluez/hci0"


class BluezAdapter:
    def __init__(self, bus: BlueZBus, pcm: PcmCapture, adapter_path: str = ADAPTER_PATH_DEFAULT,
                 log: Callable[[str], None] = lambda m: None):
        self.bus, self.pcm, self.adapter_path, self.log = bus, pcm, adapter_path, log
        self.cb = None
        self.model: BluezModel | None = None
        self.pairable = False
        self.active_address: str | None = None
        self.errors: list[str] = []
        self._lock = threading.RLock()
        self._started = False

    # ---- AudioSourceAdapter ------------------------------------------------------------------------------
    def attach(self, cb) -> None:
        self.cb = cb
        adapter = self

        class Wrapped:                                      # same callbacks, plus PCM lifecycle
            @staticmethod
            def on_connect(dev):
                ok = cb.on_connect(dev)
                if ok is not False:
                    adapter.active_address = dev
                return ok

            @staticmethod
            def on_disconnect(dev):
                adapter.pcm.stop()
                adapter.active_address = None
                cb.on_disconnect(dev)

            @staticmethod
            def on_transport(state):
                if state in ("pending", "active") and adapter.active_address:
                    adapter.pcm.start(adapter.active_address, cb.on_pcm)
                elif state == "idle":
                    adapter.pcm.stop()
                cb.on_transport(state)

            on_avrcp = staticmethod(lambda s: cb.on_avrcp(s))
            on_volume = staticmethod(lambda v: cb.on_volume(v))
            on_metadata = staticmethod(lambda t, a, b: cb.on_metadata(t, a, b))
            on_pcm = staticmethod(lambda b: cb.on_pcm(b))

        self.model = BluezModel(Wrapped, self.bus, self.log)

    def start(self) -> None:
        assert self.model is not None, "attach() first"
        with self._lock:
            self.bus.subscribe(self._on_event)              # subscribe first, snapshot second: no missed event window
            self.bus.register_agent(AGENT_PATH, "NoInputNoOutput", self._agent)
            self.model.load(self.bus.managed_objects())
            self._started = True
            self._apply_pairable(False)                     # always start closed; the pairing window opens explicitly

    def stop(self) -> None:
        self.pcm.stop()
        try:
            self._apply_pairable(False)
        except BusError:
            pass
        self.bus.close()

    def set_pairable(self, enabled: bool) -> None:
        self.pairable = bool(enabled)
        self._apply_pairable(self.pairable)

    def disconnect_device(self, device_id: str) -> None:
        m = self.model
        if m is None:
            return
        for path, props in m.devices.items():
            if str(props.get("Address", "")).upper() == device_id.upper() and props.get("Connected"):
                self.bus.call(path, "org.bluez.Device1", "Disconnect")

    # ---- internals -----------------------------------------------------------------------------------------
    def _on_event(self, ev) -> None:
        with self._lock:
            if self.model is not None:
                try:
                    if isinstance(ev, ServiceAppeared):          # bluetoothd (re)started: redo agent, snapshot and pairing state
                        self.bus.reregister_agent()
                        self._apply_pairable(self.pairable)
                        self.model.load(self.bus.managed_objects())
                        return
                    self.model.handle(ev)
                except Exception as e:                      # never kill the bus reader thread
                    self.errors.append(f"{type(e).__name__}: {e}")
                    self.log(f"event error: {type(e).__name__}: {e}")

    def _apply_pairable(self, on: bool) -> None:
        try:
            self.bus.set_property(self.adapter_path, IF_ADAPTER, "Pairable", "b", on)
            self.bus.set_property(self.adapter_path, IF_ADAPTER, "Discoverable", "b", on)
            if on:
                self.bus.set_property(self.adapter_path, IF_ADAPTER, "PairableTimeout", "u", 0)       # the bridge owns the 5 min window
                self.bus.set_property(self.adapter_path, IF_ADAPTER, "DiscoverableTimeout", "u", 0)
        except BusError as e:
            self.errors.append(str(e))
            self.log(f"adapter property error: {e}")
            raise

    def _agent(self, method: str, device_path: str, args: tuple) -> None:
        """BlueZ Agent1 (capability NoInputNoOutput = 'Just Works'): authorise only while the pairing window is open
        or for an already known/connected device."""
        known = self.model is not None and device_path in self.model.devices and self.model.devices[device_path].get("Paired")
        if method in ("RequestAuthorization", "AuthorizeService", "RequestConfirmation"):
            if self.pairable or known:
                return
            raise Rejected(f"{method} outside the pairing window")
        raise Rejected(method)
