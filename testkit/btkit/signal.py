"""Deterministic, index-addressable synthetic stereo signal.

Frame ``i`` of the signal depends only on ``i`` (counter-based hash noise plus
two tones), so any captured window can be re-aligned to the generator *exactly*
and compared sample-for-sample.  That is what lets the lab prove sample
integrity (no dropped / repeated / altered frames) end to end, instead of only
checking that "something audible" arrived.

Format: 44.1 kHz, 16-bit, stereo, little endian (what SBC decodes to in A2DP).
"""
from __future__ import annotations

import numpy as np

RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2
FRAME_BYTES = CHANNELS * SAMPLE_BYTES

_M64 = np.uint64(0xFFFFFFFFFFFFFFFF)


def _splitmix(x: np.ndarray) -> np.ndarray:
    x = (x + np.uint64(0x9E3779B97F4A7C15))
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def frames(start: int, count: int, seed: int = 0) -> np.ndarray:
    """Return ``count`` frames starting at absolute frame index ``start``.

    Shape (count, 2), dtype int16.  Amplitude is kept well below full scale so a
    +/-1 LSB difference never hides behind clipping.
    """
    idx = np.arange(start, start + count, dtype=np.uint64)
    with np.errstate(over="ignore"):
        h = _splitmix(idx ^ (np.uint64(seed) << np.uint64(40)))
    noise_l = ((h & np.uint64(0xFFF)).astype(np.int64) - 2048)          # +-2048
    noise_r = (((h >> np.uint64(12)) & np.uint64(0xFFF)).astype(np.int64) - 2048)
    t = idx.astype(np.float64) / RATE
    tone_l = np.rint(6000 * np.sin(2 * np.pi * 440.0 * t)).astype(np.int64)
    tone_r = np.rint(6000 * np.sin(2 * np.pi * 660.0 * t)).astype(np.int64)
    out = np.empty((count, 2), dtype=np.int16)
    out[:, 0] = (tone_l + noise_l).astype(np.int16)
    out[:, 1] = (tone_r + noise_r).astype(np.int16)
    return out


def frames_bytes(start: int, count: int, seed: int = 0) -> bytes:
    return frames(start, count, seed).tobytes()


def locate(window: np.ndarray, search_from: int, search_to: int, seed: int = 0) -> int | None:
    """Find the absolute frame index at which ``window`` (N,2 int16) starts.

    Exact match against the generator over [search_from, search_to).  Uses the
    first 8 frames as key, then verifies the rest of a short prefix.
    Returns None when no exact match exists (e.g. altered samples).
    """
    n = min(len(window), 64)
    if n < 8:
        return None
    ref = frames(search_from, search_to - search_from + n, seed)
    key = window[:8]
    cand = np.where((ref[: len(ref) - 7, 0] == key[0, 0]) & (ref[: len(ref) - 7, 1] == key[0, 1]))[0]
    for c in cand:
        if c + n <= len(ref) and np.array_equal(ref[c : c + n], window[:n]):
            return int(search_from + c)
    return None
