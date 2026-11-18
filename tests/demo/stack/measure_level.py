"""Measures the demo's audio LEVEL at the virtual DAC of a room (the monitor's tap, behind Lyrion and Squeezelite) - not a pytest test.

  python3 tests/demo/stack/measure_level.py [--room Wohnzimmer] [--volumes 50,70,100] [--out measurements/demo_loudness.json]

Run inside the demo image on the demo network (scripts/measure_demo_level.sh does that against a separate compose project). It plays the
demo radio (the same generated programme the phones' tracks come from) in the room at several Lyrion volumes and reports peak and RMS in
dBFS of the PCM the player outputs, next to the level of the source signal. This is software level measurement; it says nothing about
the loudness of real speakers.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

import numpy as np

from demo_env import capture, console, lms, lms_status, wait
from hifidemo import music


def dbfs(x: float) -> float:
    return round(20 * math.log10(max(x, 1e-9)), 1)


def level_of(pcm: bytes) -> dict:
    x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64) / 32768.0
    return {"peak_dbfs": dbfs(float(np.abs(x).max())), "rms_dbfs": dbfs(float(np.sqrt(np.mean(x * x))))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="Wohnzimmer")
    ap.add_argument("--volumes", default="")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    players = lms("", "players", 0, 10)["players_loop"]
    pid = next(p["playerid"] for p in players if p["name"] == a.room)
    initial = int(lms_status(a.room).get("mixer volume", -1))
    volumes = [int(v) for v in a.volumes.split(",") if v] or [initial]
    src_pcm = music.render(music.RADIO_SHOWS[0])
    res = {"room": a.room, "initial_lyrion_volume": initial, "source_radio_show": level_of(src_pcm),
           "source_tracks": {t.id: level_of(music.render(t)) for t in music.TRACKS}, "at_volume": {}}
    for v in volumes:
        lms(pid, "mixer", "volume", v)
        console("/api/lms/radio", {"player": pid})
        wait(lambda: lms_status(a.room).get("mode") == "play", 20, what="radio plays")
        time.sleep(3)
        frames = capture(a.room, a.seconds)
        pcm = b"".join(p for _, p in frames)
        pcm = pcm[len(pcm) // 4 // 4 * 4:]                          # skip the start (frame-aligned)
        res["at_volume"][str(v)] = {**level_of(pcm), "mixer_volume_reported": int(lms_status(a.room).get("mixer volume", -1))}
        lms(pid, "stop")
        time.sleep(1)
    lms(pid, "mixer", "volume", initial)
    print(json.dumps(res, indent=1))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
