"""PCM capture on the Pi: record the phone's decoded audio (and optionally a microphone/line input at the speaker) to WAV files with
wall-clock start times in a sidecar JSON, using the bridge's own ``PipeWireCapture`` (parec, no fallback).  stdlib only."""
from __future__ import annotations

import json
import os
import threading
import time
import wave

from btbridge.adapters.bluez.pcm import CHANNELS, RATE, PipeWireCapture, node_name_for, parec_argv, source_present


class WavSink:
    def __init__(self, path: str):
        self.path, self.w = path, wave.open(path, "wb")
        self.w.setnchannels(CHANNELS); self.w.setsampwidth(2); self.w.setframerate(RATE)
        self.first_wall: float | None = None
        self.bytes = 0
        self.silent_chunks = 0
        self.chunks = 0
        self._lock = threading.Lock()

    def __call__(self, pcm: bytes) -> None:
        with self._lock:
            if self.first_wall is None:
                # timestamp of the first sample of this chunk ~= now - chunk duration
                self.first_wall = time.time() - len(pcm) / (RATE * CHANNELS * 2)
            self.w.writeframes(pcm)
            self.bytes += len(pcm); self.chunks += 1
            if not pcm.strip(b"\x00"):
                self.silent_chunks += 1

    def close(self) -> dict:
        with self._lock:
            self.w.close()
            return {"file": os.path.basename(self.path), "first_sample_wall": self.first_wall, "bytes": self.bytes,
                    "seconds": round(self.bytes / (RATE * CHANNELS * 2), 3), "chunks": self.chunks, "silent_chunks": self.silent_chunks}


def record(out_dir: str, address: str | None, seconds: float, node: str | None = None, mic_node: str | None = None,
           capture_factory=PipeWireCapture, label: str = "REAL-HARDWARE") -> dict:
    """Record ``seconds`` from the phone node (``node`` or derived from ``address``) and optionally ``mic_node``."""
    os.makedirs(out_dir, exist_ok=True)
    targets = {}
    if node or address:
        targets["phone"] = node or node_name_for(address)
    if mic_node:
        targets["mic"] = mic_node
    sinks, caps = {}, {}
    for name, n in targets.items():
        sinks[name] = WavSink(os.path.join(out_dir, f"{name}.wav"))
        caps[name] = capture_factory(lambda a, n=n: parec_argv(n), present=lambda a, n=n: source_present(n))
        caps[name].start(n, sinks[name])
    t_start = time.time()
    time.sleep(seconds)
    for c in caps.values():
        c.stop()
    meta = {"label": label, "started_wall": t_start, "requested_s": seconds, "nodes": targets,
            "recordings": {k: s.close() for k, s in sinks.items()}, "capture_tool": "parec (node.dont-fallback)"}
    with open(os.path.join(out_dir, "capture.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    return meta
