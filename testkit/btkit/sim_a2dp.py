"""SIMULATED phone + A2DP/AVRCP link.  Implements the same adapter interface a real
BlueZ adapter would, so the bridge pipeline above it is exercised unchanged.

NOT real Bluetooth: no radio, SBC codec, pairing, AVRCP stack or phone quirks are
involved.  What it does model: a decoded 44.1 kHz s16le PCM stream delivered in
real time (default 20 ms chunks, optional jitter), AVRCP playing/paused status,
A2DP transport idle/active transitions with a configurable suspend delay,
absolute-volume events, metadata and short notification sounds.

Phone-side controls (call from tests): connect / play / pause / notification /
disconnect / set_volume / set_metadata / stall.  ``probes`` records wall-clock
timing probes (t_play_event, t_first_pcm, t_pause_event, t_last_pcm ...).
"""
from __future__ import annotations

import random
import threading
import time

from . import signal as sig


class SimulatedA2dpSource:
    def __init__(self, chunk_ms: int = 20, jitter_ms: float = 0.0, avrcp: bool = True,
                 suspend_delay_s: float = 0.0, seed: int = 0, start_frame: int = 0, device: str = "sim-phone-1",
                 drift_ppm: float = 0.0):
        self.chunk_ms, self.jitter_ms, self.avrcp_supported = chunk_ms, jitter_ms, avrcp
        self.suspend_delay_s, self.seed, self.device = suspend_delay_s, seed, device
        self.drift_ppm = drift_ppm            # >0: phone clock runs fast (sends faster than real time)
        self.cpu_s = 0.0                      # CPU seconds consumed by the simulator thread itself (to subtract from process CPU)
        self.frame = start_frame               # generator position (continues across pause/resume like a track)
        self.cb = None
        self.connected = False
        self.pairable = False
        self._sending = threading.Event()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.probes: dict[str, float] = {}
        self.sent: list[tuple[float, int, int]] = []     # (wall, first_frame, n_frames)
        self.refused = 0
        self.stall_until = 0.0

    # --- adapter interface ---------------------------------------------------
    def attach(self, cb):
        self.cb = cb

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="sim-a2dp")
        self._thread.start()

    def stop(self):
        self._quit.set()
        self._sending.clear()

    def set_pairable(self, enabled: bool):
        self.pairable = enabled

    # --- device management (SIMULATED; same interface as the BlueZ adapter) ------
    def paired_devices(self):
        devs = getattr(self, "_paired", None)
        if devs is None:
            devs = self._paired = {self.device: {"address": self.device, "name": "Simulated phone", "trusted": True}}
        return [dict(d, connected=(a == self.device and self.connected)) for a, d in sorted(devs.items())]

    def remove_device(self, address: str) -> bool:
        self.paired_devices()
        if address not in self._paired:
            return False
        if address == self.device and self.connected:
            self.disconnect()
        del self._paired[address]
        return True

    def set_alias(self, name: str):
        self.alias = name

    def disconnect_device(self, device_id: str):
        if device_id == self.device:
            self.disconnect()

    # --- phone-side controls ---------------------------------------------------
    def connect(self) -> bool:
        ok = self.cb.on_connect(self.device)
        if ok:
            self.connected = True
            self.probes["t_connect"] = time.time()
            if self.avrcp_supported:
                self.cb.on_avrcp("stopped")     # an AVRCP-capable phone reports its player state on connect
        else:
            self.refused += 1
        return bool(ok)

    def disconnect(self):
        self._sending.clear()
        if self.connected:
            self.connected = False
            self.probes["t_disconnect"] = time.time()
            self.cb.on_disconnect(self.device)

    def play(self):
        if not self.connected:
            return
        self.probes["t_play_event"] = time.time()
        self.probes.pop("t_first_pcm", None)
        self.cb.on_transport("active")
        if self.avrcp_supported:
            self.cb.on_avrcp("playing")
        self._sending.set()

    def pause(self):
        self.probes["t_pause_event"] = time.time()
        self._sending.clear()
        self.probes["t_last_pcm"] = self.sent[-1][0] if self.sent else 0
        if self.avrcp_supported:
            self.cb.on_avrcp("paused")
        if self.suspend_delay_s:
            threading.Timer(self.suspend_delay_s, lambda: self.connected and not self._sending.is_set() and self.cb.on_transport("idle")).start()
        else:
            self.cb.on_transport("idle")

    def notification(self, duration_s: float = 0.8):
        """Short notification sound: A2DP transport goes active for a moment, AVRCP stays 'paused/stopped'."""
        if not self.connected:
            return
        self.probes["t_play_event"] = time.time()
        self.cb.on_transport("active")
        self._sending.set()
        def end():
            self._sending.clear()
            self.cb.on_transport("idle")
        threading.Timer(duration_s, end).start()

    def set_volume(self, v: int):
        self.cb.on_volume(v)

    def set_metadata(self, title: str, artist: str = "", album: str = ""):
        self.cb.on_metadata(title, artist, album)

    def stall(self, seconds: float):
        """Transport active but no PCM for a while (radio hiccup / phone busy)."""
        self.stall_until = time.monotonic() + seconds

    # --- sender ----------------------------------------------------------------
    def _run(self):
        chunk_frames = sig.RATE * self.chunk_ms // 1000
        rnd = random.Random(self.seed)
        next_t = time.monotonic()
        while not self._quit.is_set():
            if not self._sending.wait(0.05):
                next_t = time.monotonic()
                continue
            now = time.monotonic()
            if now < self.stall_until:
                time.sleep(0.01)
                next_t = time.monotonic()
                continue
            c0 = time.thread_time()
            data = sig.frames_bytes(self.frame, chunk_frames, self.seed)
            self.cpu_s += time.thread_time() - c0
            t = time.time()
            self.sent.append((t, self.frame, chunk_frames))
            self.probes.setdefault("t_first_pcm", t)
            self.frame += chunk_frames
            self.cb.on_pcm(data)
            next_t += self.chunk_ms / 1000.0 * (1 - self.drift_ppm * 1e-6) + (rnd.uniform(-self.jitter_ms, self.jitter_ms) / 1000.0 if self.jitter_ms else 0)
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:
                next_t = time.monotonic()
