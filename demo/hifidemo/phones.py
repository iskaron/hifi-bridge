"""SIMULATED phones for the demo. A phone only does what a real phone can do over the air: see the bridge while its pairing window is open,
pair, connect, send A2DP audio in real time, report AVRCP status/metadata/absolute volume, play notification sounds, lose the link.
It never talks to the bridge product directly: everything goes through the virtual bluetoothd (vbluez) and from there through the
product's own BlueZ adapter.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request

from . import decode, music

RATE, FRAME = music.RATE, 4


# ---- radio side (real implementations; tests use fakes) ------------------------------------------------------
class AirClient:
    def __init__(self, base: str, timeout: float = 5.0):
        self.base, self.timeout = base.rstrip("/"), timeout

    def get(self, path: str, **q) -> dict:
        from urllib.parse import urlencode
        url = self.base + path + ("?" + urlencode(q) if q else "")
        with urllib.request.urlopen(url, timeout=self.timeout) as r:  # nosec B310 - fixed http URL on the internal demo network
            return json.loads(r.read())

    def post(self, action: str, **body) -> dict:
        req = urllib.request.Request(f"{self.base}/air/{action}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # nosec B310 - fixed http URL on the internal demo network
            return json.loads(r.read())


class PcmLink:
    """The phone's A2DP audio towards the bridge (TCP stand-in for the radio link)."""

    def __init__(self, host: str, port: int, mac: str):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.sendall(mac.encode() + b"\n")

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---- music library -----------------------------------------------------------------------------------------
class Library:
    """The built-in generated tracks plus the user's own uploads (decoded once, stored in the demo volume)."""

    def __init__(self, upload_dir: str):
        self.dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _meta_path(self):
        return os.path.join(self.dir, "library.json")

    def _uploads(self) -> list[dict]:
        try:
            with open(self._meta_path()) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return []

    def items(self) -> list[dict]:
        builtin = [{"id": f"builtin:{t.id}", "title": t.title, "artist": t.artist, "album": t.album, "seconds": round(t.seconds, 1),
                    "source": "builtin"} for t in music.TRACKS]
        return builtin + self._uploads()

    def get(self, item_id: str) -> dict:
        for it in self.items():
            if it["id"] == item_id:
                return it
        raise KeyError(item_id)

    def pcm(self, item_id: str) -> bytes:
        if item_id.startswith("builtin:"):
            return music.render(next(t for t in music.TRACKS if t.id == item_id[8:]))
        with open(os.path.join(self.dir, item_id.split(":", 1)[1] + ".pcm"), "rb") as fh:
            return fh.read()

    def add(self, pcm: bytes, name: str) -> dict:
        with self._lock:
            ups = self._uploads()
            n = max([int(u["id"].split(":")[1]) for u in ups] + [0]) + 1
            with open(os.path.join(self.dir, f"{n}.pcm"), "wb") as fh:
                fh.write(pcm)
            item = {"id": f"upload:{n}", "title": decode.title_from_name(name), "artist": "Own file", "album": "Uploads",
                    "seconds": round(len(pcm) / (RATE * FRAME), 1), "source": "upload"}
            ups.append(item)
            tmp = self._meta_path() + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(ups, fh, indent=1)
            os.replace(tmp, self._meta_path())
            return item


class PhoneStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def load(self, pid: str) -> dict:
        try:
            with open(self.path) as fh:
                return json.load(fh).get(pid, {})
        except (OSError, ValueError):
            return {}

    def save(self, pid: str, state: dict) -> None:
        with self._lock:
            try:
                with open(self.path) as fh:
                    allst = json.load(fh)
            except (OSError, ValueError):
                allst = {}
            allst[pid] = state
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(allst, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)


# ---- the phone ---------------------------------------------------------------------------------------------
class Phone:
    def __init__(self, pid: str, name: str, mac: str, air, link_factory, store: PhoneStore, library: Library,
                 suspend_delay: float = 2.0, chunk_s: float = 0.02):
        self.id, self.name, self.mac = pid, name, mac.upper()
        self.air, self.link_factory, self.store, self.library = air, link_factory, store, library
        self.suspend_delay, self.chunk_s = suspend_delay, chunk_s
        saved = store.load(pid)
        self.paired = bool(saved.get("paired"))
        self.bridge_name = saved.get("bridge_name", "")
        self.volume = int(saved.get("volume", 80))
        self.index = int(saved.get("index", 0)) % max(1, len(library.items()))
        self.bt_on = True
        self.connected = False
        self.status = "stopped"
        self.transport = "idle"
        self.position = 0                                   # frames into the current track
        self.visible: list[dict] = []
        self.message: str | None = None
        self.last_seq = 0
        self._pcm: bytes | None = None
        self._overlay = b""
        self._link = None
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = threading.Thread(target=self._stream, daemon=True, name=f"phone-{pid}")
        self._thread.start()

    # ---- helpers --------------------------------------------------------------------------------------------
    def _save(self):
        self.store.save(self.id, {"paired": self.paired, "bridge_name": self.bridge_name, "volume": self.volume, "index": self.index})

    def _item(self) -> dict:
        items = self.library.items()
        return items[self.index % len(items)]

    def _send_track(self):
        it = self._item()
        self.air.post("track", mac=self.mac, title=it["title"], artist=it["artist"], album=it["album"],
                      duration_ms=int(it["seconds"] * 1000))

    def _set_transport(self, state: str):
        if self.transport != state:
            self.transport = state
            self.air.post("transport", mac=self.mac, state=state)

    def _cancel_idle(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle(self, delay: float):
        self._cancel_idle()

        def go():
            with self._lock:
                if self.connected and self.status != "playing" and not self._overlay:
                    self._set_transport("idle")
        self._idle_timer = threading.Timer(delay, go)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _close_link(self):
        if self._link:
            self._link.close()
            self._link = None

    def _local_disconnect(self, message: str | None):
        self._cancel_idle()
        self.connected, self.transport, self._overlay = False, "idle", b""
        if self.status == "playing":
            self.status = "paused"
        self.message = message
        self._close_link()

    # ---- what the user can do ----------------------------------------------------------------------------------
    def scan(self) -> list[dict]:
        self.visible = self.air.get("/air/scan")["visible"] if self.bt_on else []
        return self.visible

    def pair(self) -> dict:
        with self._lock:
            if not self.visible:
                return {"ok": False, "message": "not_visible"}
            res = self.air.post("pair", mac=self.mac, name=self.name)["result"]
            if res != "paired":
                return {"ok": False, "message": res}
            self.paired, self.bridge_name, self.message = True, self.visible[0]["name"], "paired"
            self._save()
            return {"ok": True}

    def connect(self) -> dict:
        with self._lock:
            if not self.paired:
                return {"ok": False, "message": "pair_first"}
            res = self.air.post("connect", mac=self.mac)["result"]
            if res == "not_paired":
                self.paired, self.message = False, "pair_again"
                self._save()
                return {"ok": False, "message": "pair_again"}
            if res != "connected":
                self.message = res
                return {"ok": False, "message": res}
            self.connected, self.transport, self.message = True, "idle", "connected"
            self.air.post("volume", mac=self.mac, volume=self.volume)      # absolute volume is announced on connect
            self.air.post("status", mac=self.mac, status=self.status)      # 'stopped' or 'paused' (playing stopped with the link)
            self._send_track()
            return {"ok": True}

    def disconnect(self) -> dict:
        with self._lock:
            if self.connected:
                self.air.post("disconnect", mac=self.mac)
            self._local_disconnect(None)
            return {"ok": True}

    def drop(self) -> dict:
        """Simulated link loss (out of range)."""
        with self._lock:
            if self.connected:
                self.air.post("drop", mac=self.mac)
            self._local_disconnect("link_lost")
            return {"ok": True}

    def forget(self) -> dict:
        with self._lock:
            self.disconnect()
            self.paired, self.bridge_name, self.message = False, "", None
            self._save()
            return {"ok": True}

    def bluetooth(self, on: bool) -> dict:
        with self._lock:
            if not on:
                self.disconnect()
                self.visible = []
            self.bt_on = bool(on)
            return {"ok": True}

    def play(self) -> dict:
        with self._lock:
            if not self.connected:
                return {"ok": False, "message": "not_connected"}
            if self.status == "playing":
                return {"ok": True}
            self._cancel_idle()
            if self.status == "stopped":
                self.position = 0
            self._pcm = self.library.pcm(self._item()["id"])
            self._send_track()
            if self.transport != "active":
                self._set_transport("pending")
                self._set_transport("active")
            self.status = "playing"
            self.air.post("status", mac=self.mac, status="playing")
            self.message = None
            self._wake.set()
            return {"ok": True}

    def pause(self) -> dict:
        with self._lock:
            if self.status != "playing":
                return {"ok": True}
            self.status = "paused"
            if self.connected:
                self.air.post("status", mac=self.mac, status="paused")
                self._schedule_idle(self.suspend_delay)
            return {"ok": True}

    def _change_track(self, step: int) -> dict:
        with self._lock:
            n = len(self.library.items())
            self.index = (self.index + step) % n
            self.position = 0
            self._pcm = self.library.pcm(self._item()["id"]) if self.status == "playing" else None
            self._save()
            if self.connected:
                self._send_track()
            return {"ok": True}

    def next_track(self) -> dict:
        return self._change_track(+1)

    def prev_track(self) -> dict:
        return self._change_track(-1)

    def select(self, item_id: str) -> dict:
        with self._lock:
            ids = [it["id"] for it in self.library.items()]
            if item_id not in ids:
                return {"ok": False, "message": "unknown_track"}
            self.index = ids.index(item_id)
            return self._change_track(0)

    def set_volume(self, v: int) -> dict:
        with self._lock:
            self.volume = max(0, min(127, int(v)))
            self._save()
            if self.connected:
                self.air.post("volume", mac=self.mac, volume=self.volume)
            return {"ok": True}

    def notify(self) -> dict:
        """A messenger notification: the phone opens A2DP for the chime; the media player status does not change."""
        with self._lock:
            if not self.connected:
                return {"ok": False, "message": "not_connected"}
            self._overlay = music.notification()
            if self.status != "playing":
                self._cancel_idle()
                self._set_transport("active")
            self._wake.set()
            return {"ok": True}

    def sync(self) -> None:
        """Process what the bridge did (refused, disconnected, removed us) since the last call."""
        try:
            r = self.air.get("/air/device", mac=self.mac, since=self.last_seq)
        except OSError:
            return
        with self._lock:
            for e in r.get("events", []):
                self.last_seq = max(self.last_seq, e["seq"])
                if e["kind"] in ("disconnected_by_bridge", "removed", "refused") and self.connected:
                    self._local_disconnect(e["kind"])

    def snapshot(self) -> dict:
        with self._lock:
            it = self._item()
            return {"id": self.id, "name": self.name, "mac": self.mac, "bt_on": self.bt_on, "paired": self.paired,
                    "bridge_name": self.bridge_name, "connected": self.connected, "status": self.status, "transport": self.transport,
                    "volume": self.volume, "track": it, "index": self.index, "position_s": round(self.position / RATE, 1),
                    "duration_s": it["seconds"], "visible": self.visible, "message": self.message, "simulated": True}

    def shutdown(self) -> None:
        self._quit.set()
        self._wake.set()
        self._cancel_idle()
        with self._lock:
            self._close_link()
        self._save()

    # ---- the A2DP sender ----------------------------------------------------------------------------------------
    def _next_chunk(self, n: int) -> bytes | None:
        """Called with the lock held. Music (if playing) mixed with a pending notification, or the notification alone."""
        import numpy as np
        music_part = None
        if self.status == "playing" and self.connected:
            if self._pcm is None:
                self._pcm = self.library.pcm(self._item()["id"])
            off = self.position * FRAME
            music_part = self._pcm[off: off + n]
            self.position += len(music_part) // FRAME
            if off + n >= len(self._pcm):                    # end of track: the phone moves on to the next one
                self.index = (self.index + 1) % len(self.library.items())
                self.position, self._pcm = 0, None
                self._save()
                self._send_track()
            if len(music_part) < n:
                music_part += bytes(n - len(music_part))
        over = None
        if self._overlay and self.connected:
            over, self._overlay = self._overlay[:n], self._overlay[n:]
            if len(over) < n:
                over += bytes(n - len(over))
            if not self._overlay and self.status != "playing":
                self._schedule_idle(0.4)
        if music_part is None:
            return over
        if over is None:
            return music_part
        mix = np.frombuffer(music_part, "<i2").astype(np.int32) + np.frombuffer(over, "<i2").astype(np.int32)
        return np.clip(mix, -32768, 32767).astype("<i2").tobytes()

    def _stream(self) -> None:
        n = int(RATE * self.chunk_s) * FRAME
        next_t = time.monotonic()
        while not self._quit.is_set():
            with self._lock:
                chunk = self._next_chunk(n) if self.connected else None
                if chunk is not None and self._link is None:
                    try:
                        self._link = self.link_factory(self.mac)
                    except OSError:
                        chunk = None
                link = self._link
            if chunk is None:
                self._wake.wait(0.1)
                self._wake.clear()
                next_t = time.monotonic()
                continue
            try:
                link.send(chunk)
            except OSError:
                with self._lock:
                    self._close_link()
            next_t += self.chunk_s
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:
                next_t = time.monotonic()
