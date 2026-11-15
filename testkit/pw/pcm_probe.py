"""Index-addressable PCM pattern + analyser used by the PipeWire capture tests (lab only, stdlib).

Every frame carries its own 30-bit index: L = idx & 0x7fff, R = (idx >> 15) & 0x7fff (both non-negative s16).
A capture can therefore be classified exactly: leading/trailing silence, inserted silence, dropped frames (forward jump),
repeated frames (backward jump), byte misalignment (a header in front of the data) and foreign/converted data."""
from __future__ import annotations

import struct

FRAME = 4


def pattern(i0: int, n: int) -> bytes:
    return b"".join(struct.pack("<hh", (i & 0x7FFF), (i >> 15) & 0x7FFF) for i in range(i0, i0 + n))


def _index(l: int, r: int) -> int | None:
    if l < 0 or r < 0:
        return None
    return l | (r << 15)


def analyse(data: bytes, max_align: int = 64) -> dict:
    """Find the byte offset at which the pattern starts (0 = headerless) and classify the stream from there."""
    best = None
    for off in range(0, min(max_align, len(data)) + 1, 2):
        n = (len(data) - off) // FRAME
        if n < 2:
            continue
        fr = struct.unpack("<%dh" % (2 * n), data[off:off + FRAME * n])
        idx = [_index(fr[2 * k], fr[2 * k + 1]) for k in range(n)]
        good = sum(1 for a, b in zip(idx, idx[1:]) if a is not None and b == a + 1)
        if best is None or good > best[0]:
            best = (good, off, idx)
    if best is None:
        return {"bytes": len(data), "frames": 0, "verdict": "empty"}
    good, off, idx = best
    zero = lambda k: idx[k] == 0
    nz = next((k for k in range(len(idx)) if not zero(k)), len(idx))
    # start of payload = first frame that begins a run of >= 8 consecutive indices; non-silent frames before it are garbage
    first = next((k for k in range(nz, len(idx) - 8) if idx[k] is not None and all(idx[k + j] == idx[k] + j for j in range(8))), len(idx))
    last = next((k for k in range(len(idx) - 1, -1, -1) if not zero(k)), -1)
    magic = data[:4]
    header = {b".snd": "sun-au", b"dns.": "sun-au-little-endian", b"RIFF": "wav", b"fLaC": "flac", b"OggS": "ogg"}.get(magic)
    res = {"bytes": len(data), "header": header, "garbage_prefix_frames": sum(1 for k in range(nz, first) if not zero(k)), "offset": off, "frames": len(idx), "leading_silence_frames": first,
           "trailing_silence_frames": len(idx) - 1 - last if last >= 0 else 0, "consecutive_pairs": good,
           "inserted_silence_runs": 0, "inserted_silence_frames": 0, "drops": 0, "dropped_frames": 0, "repeats": 0, "foreign_frames": 0}
    k, prev = first, None
    while k <= last:
        v = idx[k]
        if v == 0:                                   # silence run inside the stream
            j = k
            while j <= last and idx[j] == 0:
                j += 1
            res["inserted_silence_runs"] += 1; res["inserted_silence_frames"] += j - k
            k = j
            continue
        if v is None:
            res["foreign_frames"] += 1
        elif prev is not None:
            if v > prev + 1:
                res["drops"] += 1; res["dropped_frames"] += v - prev - 1
            elif v <= prev:
                res["repeats"] += 1
        if v is not None:
            prev = v
        k += 1
    res["first_index"] = idx[first] if first < len(idx) else None
    res["payload_frames"] = max(0, last - first + 1)
    res["headerless"] = header is None and res["garbage_prefix_frames"] == 0 and off == 0
    res["bit_exact"] = (res["headerless"] and res["foreign_frames"] == 0 and res["repeats"] == 0 and res["drops"] == 0
                        and res["payload_frames"] > 0)
    return res
