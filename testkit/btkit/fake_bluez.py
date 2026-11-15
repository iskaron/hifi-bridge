"""SIMULATED BlueZ: a minimal ``org.bluez`` D-Bus service for hardware-free integration tests of the bridge's D-Bus layer.

It implements only the parts of the BlueZ D-Bus API that the bridge uses, *as written in the BlueZ documentation*:
ObjectManager.GetManagedObjects + InterfacesAdded/Removed, Properties.Get/GetAll/Set + PropertiesChanged,
Device1.Disconnect, AgentManager1.RegisterAgent/RequestDefaultAgent, Agent1 calls towards the bridge.

It is NOT bluetoothd: no radio, no pairing protocol, no phone quirks.  Passing against it proves the bridge's D-Bus client
code (match rules, variant parsing, property writes, agent replies, reconnect handling) - not BlueZ/phone compatibility.
Run on a *private* dbus-daemon (``PrivateBus``), never on the host's system bus.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_call, new_method_return, new_signal
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

IF_PROPS = "org.freedesktop.DBus.Properties"
IF_OBJMGR = "org.freedesktop.DBus.ObjectManager"
ADAPTER = "/org/bluez/hci0"
SIGS = {bool: "b", str: "s", int: "q"}          # q = uint16 (Volume), plain ints in props are uint32 'u' below


def variant(v, sig=None):
    if sig:
        return (sig, v)
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, int):
        return ("u", v)
    if isinstance(v, str):
        return ("s", v)
    if isinstance(v, list):
        return ("as", v)
    if isinstance(v, dict):
        return ("a{sv}", {k: variant(x) for k, x in v.items()})
    raise TypeError(v)


class PrivateBus:
    """A private ``dbus-daemon --session`` (own socket, own config): never touches host buses."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="btbus-")
        self.sock = os.path.join(self.dir, "bus.sock")
        self.address = f"unix:path={self.sock}"
        cfg = os.path.join(self.dir, "bus.conf")
        open(cfg, "w").write(f"""<busconfig><type>session</type><listen>{self.address}</listen>
<auth>EXTERNAL</auth><policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/><allow user="*"/></policy></busconfig>""")
        self.proc = subprocess.Popen(["dbus-daemon", "--config-file", cfg, "--nofork", "--nopidfile"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not os.path.exists(self.sock):
            if time.time() - t0 > 5:
                raise RuntimeError("private dbus-daemon did not start")
            time.sleep(0.02)

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)


class FakeBluez:
    def __init__(self, address: str):
        self.address = address
        self.objects: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple] = []                  # (path, interface, method, body) received from the bridge
        self.agent: tuple[str, str] | None = None     # (bridge unique name, agent path)
        self.agent_results: list[tuple] = []
        self.conn = None
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self.up = False

    # ---- lifecycle (start/stop simulate bluetoothd restarts) ----
    def start(self, keep_objects: bool = False):
        self.conn = open_dbus_connection(bus=self.address)
        self.conn.send_and_get_reply(message_bus.RequestName("org.bluez"))
        if not keep_objects:
            self.objects = {ADAPTER: {"org.bluez.Adapter1": {"Address": variant("00:11:22:33:44:55"), "Powered": variant(True),
                                                             "Pairable": variant(True), "Discoverable": variant(False),
                                                             "PairableTimeout": variant(0), "DiscoverableTimeout": variant(180)}}}
        self.agent = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-bluez")
        self._thread.start()
        self.up = True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self.conn:
            self.conn.close()
        self.conn, self.up = None, False

    # ---- scenario controls ----
    def _emit(self, path, iface, member, sig, body):
        with self._lock:
            self.conn.send_message(new_signal(DBusAddress(path, interface=iface), member, sig, body))

    def add_object(self, path, ifaces: dict[str, dict]):
        v = {i: {k: (x if isinstance(x, tuple) else variant(x)) for k, x in p.items()} for i, p in ifaces.items()}
        self.objects[path] = v
        self._emit("/", IF_OBJMGR, "InterfacesAdded", "oa{sa{sv}}", (path, v))

    def remove_object(self, path, ifaces=None):
        gone = list((self.objects.get(path) or {}).keys()) if ifaces is None else list(ifaces)
        for i in gone:
            self.objects.get(path, {}).pop(i, None)
        if not self.objects.get(path):
            self.objects.pop(path, None)
        self._emit("/", IF_OBJMGR, "InterfacesRemoved", "oas", (path, gone))

    def set_props(self, path, iface, **props):
        pv = {k: (v if isinstance(v, tuple) else variant(v)) for k, v in props.items()}   # a tuple is a ready-made (signature, value) variant
        self.objects.setdefault(path, {}).setdefault(iface, {}).update(pv)
        self._emit(path, IF_PROPS, "PropertiesChanged", "sa{sv}as", (iface, pv, []))

    def add_device(self, n=1, connected=False, uuids=("0000110a-0000-1000-8000-00805f9b34fb",), paired=True):
        path = self.dev(n)
        p = {"Address": f"AA:BB:CC:DD:EE:{n:02X}", "Connected": connected, "Paired": paired, "Alias": f"phone{n}"}
        if uuids is not None:
            p["UUIDs"] = list(uuids)
        self.add_object(path, {"org.bluez.Device1": p})
        return path

    @staticmethod
    def dev(n=1):
        return f"{ADAPTER}/dev_AA_BB_CC_DD_EE_{n:02X}"

    def agent_call(self, member, device_path, sig="o", body=None, timeout=5):
        """Act like bluetoothd calling the bridge's agent; returns ('ok',) or ('error', name)."""
        assert self.agent, "no agent registered"
        sender, apath = self.agent
        msg = new_method_call(DBusAddress(apath, bus_name=sender, interface="org.bluez.Agent1"), member, sig, body if body is not None else (device_path,))
        with self._lock:
            conn = open_dbus_connection(bus=self.address)       # separate connection: do not interfere with the service loop
        try:
            reply = conn.send_and_get_reply(msg, timeout=timeout)
        finally:
            conn.close()
        if reply.header.message_type == MessageType.error:
            res = ("error", reply.header.fields[HeaderFields.error_name])
        else:
            res = ("ok",)
        self.agent_results.append((member, res))
        return res

    # ---- service loop ----
    def _serve(self):
        while not self._stop.is_set():
            try:
                msg = self.conn.receive(timeout=0.2)
            except TimeoutError:
                continue
            except Exception:
                return
            if msg.header.message_type != MessageType.method_call:
                continue
            try:
                self._handle(msg)
            except Exception as e:                                  # reply with an error instead of dying
                with self._lock:
                    self.conn.send_message(new_error(msg, "org.bluez.Error.Failed", "s", (f"{type(e).__name__}: {e}",)))

    def _handle(self, msg):
        f = msg.header.fields
        path, iface, member, body = f.get(HeaderFields.path), f.get(HeaderFields.interface), f.get(HeaderFields.member), msg.body
        self.calls.append((path, iface, member, body))
        with self._lock:
            if iface == IF_OBJMGR and member == "GetManagedObjects":
                self.conn.send_message(new_method_return(msg, "a{oa{sa{sv}}}", (self.objects,)))
            elif iface == IF_PROPS and member == "Set":
                i, name, val = body
                self.objects.setdefault(path, {}).setdefault(i, {})[name] = val
                self.conn.send_message(new_method_return(msg))
                self.conn.send_message(new_signal(DBusAddress(path, interface=IF_PROPS), "PropertiesChanged", "sa{sv}as", (i, {name: val}, [])))
            elif iface == IF_PROPS and member == "Get":
                i, name = body
                self.conn.send_message(new_method_return(msg, "v", (self.objects[path][i][name],)))
            elif iface == IF_PROPS and member == "GetAll":
                self.conn.send_message(new_method_return(msg, "a{sv}", (self.objects[path][body[0]],)))
            elif iface == "org.bluez.Device1" and member == "Disconnect":
                self.conn.send_message(new_method_return(msg))
                self.set_props(path, "org.bluez.Device1", Connected=False)
            elif iface == "org.bluez.Adapter1" and member == "RemoveDevice":
                dev = body[0]
                if dev not in self.objects:
                    self.conn.send_message(new_error(msg, "org.bluez.Error.DoesNotExist", "s", ("no such device",)))
                    return
                self.conn.send_message(new_method_return(msg))
                if self.objects[dev].get("org.bluez.Device1", {}).get("Connected", ("b", False))[1]:
                    self.set_props(dev, "org.bluez.Device1", Connected=False)
                self.remove_object(dev)
            elif iface == "org.bluez.AgentManager1" and member == "RegisterAgent":
                self.agent = (f[HeaderFields.sender], body[0])
                self.conn.send_message(new_method_return(msg))
            elif iface == "org.bluez.AgentManager1" and member == "RequestDefaultAgent":
                self.conn.send_message(new_method_return(msg))
            else:
                self.conn.send_message(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (f"{iface}.{member}",)))
