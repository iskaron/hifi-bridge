"""Scripted BlueZ bus for UNIT tests.  Traces are HAND-WRITTEN from the BlueZ D-Bus API documentation,
NOT recorded from a real phone or bluetoothd (label: SIMULATED)."""
from btbridge.adapters.bluez.bus import (A2DP_SOURCE_UUID, IF_DEVICE, IF_PLAYER, IF_TRANSPORT, BusError, InterfacesAdded,
                                         InterfacesRemoved, PropertiesChanged)

ADAPTER = "/org/bluez/hci0"


def dev_path(n=1):
    return f"{ADAPTER}/dev_AA_BB_CC_DD_EE_{n:02X}"


def addr(n=1):
    return f"AA:BB:CC:DD:EE:{n:02X}"


def device_props(n=1, connected=False, uuids=(A2DP_SOURCE_UUID,), paired=True):
    p = {"Address": addr(n), "Connected": connected, "Paired": paired}
    if uuids is not None:
        p["UUIDs"] = list(uuids)
    return p


class FakeBus:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.handler = None
        self.calls, self.props, self.agent = [], [], None
        self.closed = False
        self.fail_calls = False

    def managed_objects(self):
        return self.objects

    def subscribe(self, handler):
        self.handler = handler

    def call(self, path, interface, method, signature="", args=()):
        if self.fail_calls:
            raise BusError("simulated failure")
        self.calls.append((path, interface, method))

    def set_property(self, path, interface, name, signature, value):
        self.props.append((path, interface, name, value))

    def register_agent(self, path, capability, handler):
        self.agent = (path, capability, handler)

    def reregister_agent(self):
        self.agent_reregistered = getattr(self, 'agent_reregistered', 0) + 1

    def close(self):
        self.closed = True

    # ---- event helpers ----
    def emit(self, ev):
        self.handler(ev)

    def changed(self, path, iface, **props):
        self.emit(PropertiesChanged(path, iface, props))

    def added(self, path, iface, props):
        self.emit(InterfacesAdded(path, {iface: props}))

    def removed(self, path, iface):
        self.emit(InterfacesRemoved(path, (iface,)))

    def add_device(self, n=1, **kw):
        self.added(dev_path(n), IF_DEVICE, device_props(n, **kw))

    def connect(self, n=1):
        self.changed(dev_path(n), IF_DEVICE, Connected=True)

    def disconnect(self, n=1):
        self.changed(dev_path(n), IF_DEVICE, Connected=False)

    def add_transport(self, n=1, state="idle", volume=None, idx=0):
        p = {"Device": dev_path(n), "State": state}
        if volume is not None:
            p["Volume"] = volume
        self.added(f"{dev_path(n)}/fd{idx}", IF_TRANSPORT, p)

    def transport(self, n=1, idx=0, **props):
        self.changed(f"{dev_path(n)}/fd{idx}", IF_TRANSPORT, **props)

    def add_player(self, n=1, status="paused", track=None, idx=0):
        p = {"Device": dev_path(n), "Status": status}
        if track is not None:
            p["Track"] = track
        self.added(f"{dev_path(n)}/player{idx}", IF_PLAYER, p)

    def player(self, n=1, idx=0, **props):
        self.changed(f"{dev_path(n)}/player{idx}", IF_PLAYER, **props)


class Recorder:
    """Records SourceCallbacks invocations."""
    def __init__(self, accept=True):
        self.events, self.accept = [], accept
        self.on_connect = lambda d: (self.events.append(("connect", d)), self.accept)[1]
        self.on_disconnect = lambda d: self.events.append(("disconnect", d))
        self.on_transport = lambda s: self.events.append(("transport", s))
        self.on_avrcp = lambda s: self.events.append(("avrcp", s))
        self.on_volume = lambda v: self.events.append(("volume", v))
        self.on_metadata = lambda t, a, b: self.events.append(("meta", t, a, b))
        self.on_pcm = lambda b: self.events.append(("pcm", len(b)))

    def kinds(self):
        return [e[0] for e in self.events]
