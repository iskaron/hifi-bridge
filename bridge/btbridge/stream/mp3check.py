"""Owner/CI check of the live MP3 encoder: ``python3 -m btbridge.stream.mp3check [--seconds N]``.

Runs the real ``lame`` child exactly as the stream server does (320 kbit/s CBR, 44.1 kHz stereo), feeds it synthetic stereo audio in
real time (20 ms chunks, like the Bluetooth capture) and reports: the encoder binary and version, whether the output really is
MPEG-1 Layer III 320 kbit/s CBR, the data rate, the CPU the encoder used (percent of ONE core) and its memory. Nothing is written
to disk and no audio is played. Exit code 0 = usable, 1 = not installed or wrong output.

Run it on the target board (the Pi 3 B+) while the bridge is idle and again while it streams: the numbers on a development host are
not transferable.
"""
from __future__ import annotations

import argparse
import math
import re
import struct
import subprocess
import sys
import time

from . import encoders, mp3frames

RATE = 44100


def _chunk(k: int, n: int) -> bytes:
    out = bytearray()
    for i in range(k, k + n):
        out += struct.pack("<hh", int(7000 * math.sin(2 * math.pi * 440 * i / RATE)), int(7000 * math.sin(2 * math.pi * 660 * i / RATE)))
    return bytes(out)


def _rss_kb(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status") as f:
            m = re.search(r"VmRSS:\s+(\d+) kB", f.read())
        return int(m.group(1)) if m else None
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=20.0, help="how long to encode in real time (default 20)")
    a = ap.parse_args(argv)
    exe = encoders.mp3_encoder_path()
    if not exe:
        print("MP3 encoder: NOT INSTALLED (the 'lame' command was not found).\n  Install it with: sudo apt install lame\n"
              "  WAV and FLAC are not affected; the MP3 option stays unavailable until lame is installed.")
        return 1
    ver = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[:1]   # nosec B603
    print(f"MP3 encoder: {exe}  {ver[0] if ver else '?'}")
    try:
        enc = encoders.Mp3Encoder()
    except encoders.EncoderUnavailable as e:
        print(f"MP3 encoder: cannot start: {e}")
        return 1
    out, k, step, t0 = [], 0, RATE // 50, time.monotonic()
    worst = 0.0
    rss = 0
    try:
        enc.encode(_chunk(0, RATE))                       # the 1 s lead-in a new client gets
        k = RATE
        cpu0 = enc.cpu_seconds()
        t_start = time.monotonic()
        nxt = t_start
        while time.monotonic() - t_start < a.seconds:
            c0 = time.monotonic()
            out.append(enc.encode(_chunk(k, step)))
            worst = max(worst, time.monotonic() - c0)
            k += step
            nxt += step / RATE
            time.sleep(max(0.0, nxt - time.monotonic()))
            rss = max(rss, _rss_kb(enc.pid) or 0)
        wall = time.monotonic() - t_start
        cpu = enc.cpu_seconds()
        data = b"".join(out)
    finally:
        enc.close()
    frames, end = mp3frames.walk(data, mp3frames.find_first_frame(data) if data else 0)
    ok = bool(frames) and {f.bitrate_kbps for f in frames} == {320} and {f.sample_rate for f in frames} == {44100} \
        and {f.mode for f in frames} == {"stereo"}
    kbps = len(data) * 8 / wall / 1000
    print(f"output:      {len(frames)} frames, MPEG-1 Layer III, {sorted({f.bitrate_kbps for f in frames})} kbit/s, "
          f"{sorted({f.sample_rate for f in frames})} Hz, {sorted({f.mode for f in frames})}  -> {'OK' if ok else 'WRONG'}")
    print(f"data rate:   {kbps:.0f} kbit/s over {wall:.1f} s (WAV would be 1411 kbit/s)")
    if cpu is not None and cpu0 is not None:
        print(f"encoder CPU: {100 * (cpu - cpu0) / wall:.1f} % of one core (the bridge itself, PipeWire and BlueZ come on top)")
    else:
        print("encoder CPU: not available on this system")
    print(f"encoder RAM: {rss / 1024:.1f} MiB resident; slowest hand-over to the encoder {worst * 1000:.0f} ms")
    print("RESULT: " + ("usable" if ok else "NOT usable"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
