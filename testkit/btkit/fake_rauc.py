"""SIMULATED RAUC: a minimal ``de.pengutronix.rauc`` D-Bus service for hardware-free tests of the bridge's RAUC client and update flow.

Shaped after the REAL RAUC 1.13 service (replies captured with ``rauc service`` on a private bus, tests/os/test_ota_rauc_real.py):
GetAll properties, GetSlotStatus, GetPrimary, Mark, InspectBundle (nested variants as RAUC sends them), InstallBundle with Progress
updates and the Completed signal. Like real RAUC, InspectBundle does not check the compatible string.

It is NOT RAUC: no bundle is parsed, nothing is written to a slot, there is no bootloader. ``install_plan`` scripts what an installation
does (success, failure with a RAUC message, or a daemon crash without Completed). Run it on a private bus (``PrivateBus``) only.
"""
from __future__ import annotations

import os
import threading
import time

from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_call, new_method_return, new_signal  # noqa: F401
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

SERVICE, IFACE = "de.pengutronix.rauc", "de.pengutronix.rauc.Installer"
ERR = "org.gtk.GDBus.UnmappedGError.Quark._g_2dio_2derror_2dquark.Code1"     # what GDBus sends for an unmapped GError


def v(x):
    if isinstance(x, tuple):
        return x
    if isinstance(x, bool):
        return ("b", x)
    if isinstance(x, int):
        return ("u", x)
    return ("s", str(x))


class FakeRauc:
    def __init__(self, address: str, compatible: str = "hifi-bridge-rpi3", booted: str = "A"):
        self.address = address
        self.compatible, self.booted = compatible, booted
        self.operation, self.last_error, self.progress = "idle", "", (0, "", 0)
        self.slots = {
            "rootfs.0": {"class": "rootfs", "bootname": "A", "type": "ext4", "device": "/dev/mmcblk0p2"},
            "rootfs.1": {"class": "rootfs", "bootname": "B", "type": "ext4", "device": "/dev/mmcblk0p3"},
        }
        self.boot_status = {"rootfs.0": "good", "rootfs.1": "bad"}
        self.primary = self._by_bootname(booted)
        self.bundles: dict[str, dict | str] = {}      # path -> {"compatible", "version"} or an error message (bad signature ...)
        self.install_plan: dict = {"result": 0}        # {"result": 0|1, "error": "...", "steps": n, "crash": bool}
        self.accept_any, self.lab_version = False, "0.4.0"     # lab: any existing file counts as a bundle of lab_version
        self.calls: list[tuple] = []
        self.conn = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def _by_bootname(self, b: str) -> str:
        return next(n for n, s in self.slots.items() if s["bootname"] == b)

    # ---- lifecycle ----
    def start(self):
        self.conn = open_dbus_connection(bus=self.address)
        self.conn.send_and_get_reply(message_bus.RequestName(SERVICE))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-rauc")
        self._thread.start()

    def stop(self):
        """Also simulates a daemon crash: the name disappears, no Completed signal is sent."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self.conn:
            self.conn.close()
        self.conn = None

    # ---- state as RAUC reports it ----
    def props(self) -> dict:
        return {"Operation": ("s", self.operation), "LastError": ("s", self.last_error), "Progress": ("(isi)", self.progress),
                "Compatible": ("s", self.compatible), "Variant": ("s", ""), "BootSlot": ("s", self.booted)}

    def slot_status(self) -> list:
        out = []
        for name, s in self.slots.items():
            st = "booted" if s["bootname"] == self.booted else "inactive"
            d = {k: v(x) for k, x in s.items()}
            d.update({"state": ("s", st), "boot-status": ("s", self.boot_status[name])})
            out.append((name, d))
        return out

    def _resolve(self, ident: str) -> str:
        booted = self._by_bootname(self.booted)
        if ident == "booted":
            return booted
        if ident == "other":
            return next(n for n in self.slots if n != booted)
        if ident not in self.slots:
            raise ValueError(f"No slot with name '{ident}' found")
        return ident

    # ---- service loop ----
    def _serve(self):
        while not self._stop.is_set():
            try:
                msg = self.conn.receive(timeout=0.1)
            except TimeoutError:
                continue
            except Exception:
                return
            if msg.header.message_type == MessageType.method_call:
                try:
                    self._handle(msg)
                except Exception as e:
                    with self._lock:
                        self.conn.send_message(new_error(msg, ERR, "s", (str(e),)))

    def _reply(self, msg, sig="", body=()):
        with self._lock:
            self.conn.send_message(new_method_return(msg, sig, body))

    def _handle(self, msg):
        f = msg.header.fields
        iface, member, body = f.get(HeaderFields.interface), f.get(HeaderFields.member), msg.body
        self.calls.append((member, body))
        if iface == "org.freedesktop.DBus.Properties" and member == "GetAll":
            self._reply(msg, "a{sv}", (self.props(),))
        elif member == "GetSlotStatus":
            self._reply(msg, "a(sa{sv})", (self.slot_status(),))
        elif member == "GetPrimary":
            self._reply(msg, "s", (self.primary,))
        elif member == "Mark":
            state, ident = body
            name = self._resolve(ident)
            if state == "good":
                self.boot_status[name], text = "good", f"marked slot {name} as good"
            elif state == "bad":
                self.boot_status[name], text = "bad", f"marked slot {name} as bad"
                if self.primary == name:
                    self.primary = self._resolve("booted") if name != self._resolve("booted") else self._resolve("other")
            elif state == "active":
                self.boot_status[name], self.primary, text = "good", name, f"activated slot {name}"
            else:
                raise ValueError(f"unknown mark state '{state}'")
            self._reply(msg, "ss", (name, text))
        elif member == "InspectBundle":
            b = self.bundles.get(body[0])
            if b is None and self.accept_any and os.path.isfile(body[0]):
                b = {"compatible": self.compatible, "version": self.lab_version}
            if b is None:
                raise ValueError(f"Invalid bundle format: Failed to open bundle for reading: Error opening file {body[0]}: No such file")
            if isinstance(b, str):
                raise ValueError(b)
            info = {"update": ("v", ("a{sv}", {"compatible": ("s", b["compatible"]), "version": ("s", b["version"]),
                                              "description": ("s", b.get("description", ""))})),
                    "bundle": ("v", ("a{sv}", {"format": ("s", "verity")})), "manifest-hash": ("s", "0" * 64),
                    "images": ("v", ("aa{sv}", [{"slot-class": ("s", "rootfs"), "size": ("t", 1024)}]))}
            self._reply(msg, "a{sv}", (info,))
        elif member == "InstallBundle":
            if self.operation != "idle":
                raise ValueError("Already processing a different method")
            self._reply(msg)
            threading.Thread(target=self._install, args=(body[0],), daemon=True).start()
        else:
            with self._lock:
                self.conn.send_message(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (f"{iface}.{member}",)))

    def _install(self, path: str):
        plan = dict(self.install_plan)
        self.operation, self.last_error, self.progress = "installing", "", (0, "Installing", 1)
        steps = int(plan.get("steps", 3))
        for i in range(1, steps + 1):
            time.sleep(plan.get("step_s", 0.05))
            if self._stop.is_set():
                return
            self.progress = (int(100 * i / (steps + 1)), f"step {i}", 2)
        if plan.get("crash"):
            self.stop()                                          # daemon dies mid-install: no Completed signal
            return
        result = int(plan.get("result", 0))
        if result == 0:
            target = self._resolve("other")
            b = self.bundles.get(path)
            if isinstance(b, dict):
                self.slots[target]["bundle.version"] = b["version"]
                self.slots[target]["bundle.compatible"] = b["compatible"]
            self.boot_status[target], self.primary = "good", target
            self.progress = (100, "Installing done.", 1)
        else:
            self.last_error = plan.get("error", "Installation error")
            self.progress = (100, "Installing failed.", 1)
        self.operation = "idle"
        with self._lock:
            self.conn.send_message(new_signal(DBusAddress("/", interface=IFACE), "Completed", "i", (result,)))
