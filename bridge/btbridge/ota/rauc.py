"""RAUC over D-Bus (``de.pengutronix.rauc.Installer`` on object ``/``), with jeepney.

The bridge runs unprivileged; RAUC (root) does every privileged step. The D-Bus policy in the image (os/br2-external overlay,
``de.pengutronix.rauc-hifi-bridge.conf``) lets only root and the ``hifi-bridge`` user talk to RAUC; upstream RAUC allows everybody.

Interface as documented in RAUC's ``de.pengutronix.rauc.Installer.xml`` (verified against the real RAUC 1.13 service in
tests/os/test_ota_rauc_real.py): methods ``InstallBundle(s, a{sv})``, ``InspectBundle(s, a{sv}) -> a{sv}``, ``Mark(s, s) -> (s, s)``,
``GetSlotStatus() -> a(sa{sv})``, ``GetPrimary() -> s``; properties ``Operation``, ``LastError``, ``Progress (isi)``, ``Compatible``,
``Variant``, ``BootSlot``; signal ``Completed(i)``.

Values come back as plain Python (variants unwrapped). Two error types:
  * ``RaucUnavailable`` - RAUC cannot be reached (no bus, no service, no jeepney, access denied by the bus policy);
  * ``RaucError`` - RAUC answered with an error (bad signature, wrong compatible, unknown slot ...); the text is RAUC's message.
"""
from __future__ import annotations

import threading

SERVICE, IFACE, PATH = "de.pengutronix.rauc", "de.pengutronix.rauc.Installer", "/"
UNAVAILABLE_ERRORS = ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner",
                      "org.freedesktop.DBus.Error.AccessDenied", "org.freedesktop.DBus.Error.NoReply",
                      "org.freedesktop.DBus.Error.Disconnected", "org.freedesktop.DBus.Error.Spawn.ServiceNotFound")


class RaucUnavailable(Exception):
    pass


class RaucError(Exception):
    pass


def plain(sig: str, value):
    """Convert a D-Bus value with signature ``sig`` (as jeepney returns it) into plain Python: variants unwrapped, recursively."""
    if sig == "v":
        return plain(value[0], value[1])
    if sig.startswith("a{"):
        vsig = sig[3:-1]
        return {k: plain(vsig, v) for k, v in value.items()}
    if sig.startswith("a"):
        return [plain(sig[1:], v) for v in value]
    return value


class RaucClient:
    """Blocking client. Thread-safe (one connection, one lock); reconnects after RAUC or the bus went away."""

    def __init__(self, bus: str = "SYSTEM", timeout: float = 30.0):
        self.bus, self.timeout = bus, timeout
        self._conn = None
        self._completed = None
        self._lock = threading.RLock()

    # ---- connection ----
    def _connect(self):
        if self._conn is None:
            try:
                from jeepney import MatchRule
                from jeepney.bus_messages import message_bus
                from jeepney.io.blocking import open_dbus_connection
                conn = open_dbus_connection(bus=self.bus)
                rule = MatchRule(type="signal", interface=IFACE, member="Completed", path=PATH)
                self._completed = conn.filter(rule, bufsize=16).queue
                conn.send_and_get_reply(message_bus.AddMatch(rule), timeout=self.timeout)
            except Exception as e:                                  # no bus, no jeepney, auth failure ...
                raise RaucUnavailable(f"D-Bus not reachable: {type(e).__name__}: {e}") from e
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn, self._completed = None, None

    def _call(self, member: str, sig: str = "", args: tuple = (), iface: str = IFACE):
        from jeepney import DBusAddress, MessageType, new_method_call
        with self._lock:
            conn = self._connect()
            msg = new_method_call(DBusAddress(PATH, bus_name=SERVICE, interface=iface), member, sig, args)
            try:
                reply = conn.send_and_get_reply(msg, timeout=self.timeout)
            except (OSError, EOFError, TimeoutError) as e:            # bus or RAUC died mid-call
                self.close()
                raise RaucUnavailable(f"{member}: {type(e).__name__}: {e}") from e
            if reply.header.message_type == MessageType.error:
                from jeepney import HeaderFields
                name = reply.header.fields.get(HeaderFields.error_name, "")
                text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else name
                if name in UNAVAILABLE_ERRORS:
                    raise RaucUnavailable(f"{member}: {name}: {text}")
                raise RaucError(text)
            return reply.body

    # ---- API ----
    def properties(self) -> dict:
        """Operation, LastError, Progress (percent, message, depth), Compatible, Variant, BootSlot."""
        body = self._call("GetAll", "s", (IFACE,), iface="org.freedesktop.DBus.Properties")
        props = plain("a{sv}", body[0])
        props["Progress"] = tuple(props.get("Progress") or (0, "", 0))
        return props

    def slots(self) -> list[dict]:
        """GetSlotStatus as a list of dicts, each with its slot name under ``name`` (sorted by name)."""
        out = [{"name": name, **plain("a{sv}", st)} for name, st in self._call("GetSlotStatus")[0]]
        return sorted(out, key=lambda s: s["name"])

    def primary(self) -> str:
        return self._call("GetPrimary")[0]

    def inspect(self, path: str) -> dict:
        """InspectBundle: RAUC verifies the signature against its keyring (as ``rauc info``); raises RaucError if it is not valid.
        It does NOT check the compatible string (verified with real RAUC 1.13) - the caller must compare it."""
        return plain("a{sv}", self._call("InspectBundle", "sa{sv}", (path, {}))[0])

    def install(self, path: str) -> None:
        """Start an installation (returns at once; completion arrives as the Completed signal, see ``completed()``)."""
        self._call("InstallBundle", "sa{sv}", (path, {}))

    def mark(self, state: str, slot: str) -> tuple[str, str]:
        if state not in ("good", "bad", "active"):
            raise ValueError(state)
        name, message = self._call("Mark", "ss", (state, slot))
        return name, message

    def completed(self) -> int | None:
        """The result of a Completed signal received since the last call (0 = success), or None. Never blocks."""
        with self._lock:
            if self._conn is None:
                return None
            try:
                while True:
                    self._conn.recv_messages(timeout=0)
            except TimeoutError:
                pass
            except (OSError, EOFError) as e:
                self.close()
                raise RaucUnavailable(f"connection lost: {e}") from e
            if self._completed:
                return int(self._completed.popleft().body[0])
            return None
