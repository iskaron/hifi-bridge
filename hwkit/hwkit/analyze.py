"""Decode the ``indexed`` test track from a recording (bridge PCM capture, LMS player output, or a microphone at the speaker).

Returns one entry per detected block: (time of the block start in the recording [s], index or None if undecodable).
From that: continuity (drops/repeats/gaps) and - given two recordings on the same clock - latency per block.
"""
from __future__ import annotations

import wave

import numpy as np

from .signals import BLOCK_S, DATA_BITS, PAIRS, PILOT_HZ, TONE_S, check_bits


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit WAV supported")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), rate


def _tone_power(seg: np.ndarray, rate: int, freqs: list[float]) -> np.ndarray:
    win = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg * win))
    hz = np.fft.rfftfreq(len(seg), 1 / rate)
    return np.array([spec[max(0, np.searchsorted(hz, f) - 2): np.searchsorted(hz, f) + 3].max() for f in freqs])


def decode_block(seg: np.ndarray, rate: int) -> int | None:
    flat = [f for pair in PAIRS for f in pair]
    p = _tone_power(seg, rate, [PILOT_HZ] + flat)
    pilot, tones = p[0], p[1:].reshape(-1, 2)
    if pilot < 1e-3 * len(seg):                       # no pilot -> silence / not our signal
        return None
    lo, hi = tones[:, 0], tones[:, 1]
    if np.any(np.maximum(lo, hi) < 2.0 * np.minimum(lo, hi)):   # an ambiguous pair -> refuse rather than guess
        return None
    word = sum(int(h > l) << k for k, (l, h) in enumerate(zip(lo, hi)))
    idx, chk = word & 0xFFFF, word >> DATA_BITS
    return idx if chk == check_bits(idx) else None


def find_blocks(x: np.ndarray, rate: int) -> list[tuple[float, int | None]]:
    """Locate block starts from the pilot-tone envelope, then decode each block's chord."""
    hop = int(0.002 * rate)
    n = len(x) // hop
    if n < 10:
        return []
    t = np.arange(hop) / rate
    ref = np.exp(-2j * np.pi * PILOT_HZ * t)
    frames = x[: n * hop].reshape(n, hop)
    # pilot magnitude per 2 ms hop (phase-insensitive, using a 10 ms sliding sum)
    mag = np.abs(frames @ ref)
    env = np.convolve(mag, np.ones(5), mode="same")
    thr = 0.3 * np.percentile(env, 95) if env.max() > 0 else 1
    on = env > thr
    starts = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    if on[0]:
        starts = np.concatenate([[0], starts])
    out = []
    n_tone = int(TONE_S * rate)
    margin = int(0.010 * rate)
    for s in starts:
        a = s * hop + margin                             # skip the fade-in / onset uncertainty
        seg = x[a: a + n_tone - 2 * margin]
        if len(seg) < n_tone // 2:
            continue
        out.append((s * hop / rate, decode_block(seg, rate)))
    # Envelope ripple or a recording that begins inside a block can yield two onsets for ONE block. Same index (or an undecodable
    # detection) less than 0.9 blocks before the next detection -> keep only the later, properly aligned one.
    dedup = []
    for t, i in out:
        if dedup and t - dedup[-1][0] < 0.9 * BLOCK_S and (dedup[-1][1] == i or dedup[-1][1] is None):
            dedup[-1] = (t, i)
            continue
        dedup.append((t, i))
    return dedup


def continuity(blocks: list[tuple[float, int | None]]) -> dict:
    idx = [(t, i) for t, i in blocks if i is not None]
    res = {"blocks": len(blocks), "decoded": len(idx), "undecodable": len(blocks) - len(idx), "drops": 0, "dropped_blocks": 0,
           "repeats": 0, "gaps": [], "first": idx[0][1] if idx else None, "last": idx[-1][1] if idx else None}
    for (t0, a), (t1, b) in zip(idx, idx[1:]):
        if b == a + 1:
            dt = t1 - t0
            if dt > BLOCK_S * 1.5:                       # same sequence but late: a silence gap was inserted
                res["gaps"].append({"at_s": round(t0, 3), "after_index": a, "gap_s": round(dt - BLOCK_S, 3)})
        elif b > a + 1:
            res["drops"] += 1; res["dropped_blocks"] += b - a - 1
        else:
            res["repeats"] += 1
    res["clean"] = res["drops"] == 0 and res["repeats"] == 0 and not res["gaps"] and res["decoded"] > 0
    return res


def latency(reference: list[tuple[float, int | None]], ref_t0: float, later: list[tuple[float, int | None]], later_t0: float) -> dict:
    """Per-index delay between two recordings whose start times (``*_t0``, same clock, seconds) are known."""
    r = {i: ref_t0 + t for t, i in reference if i is not None}
    d = sorted((later_t0 + t) - r[i] for t, i in later if i is not None and i in r)
    if not d:
        return {"matched": 0}
    return {"matched": len(d), "median_s": round(d[len(d) // 2], 4), "min_s": round(d[0], 4), "max_s": round(d[-1], 4),
            "p95_s": round(d[int(0.95 * (len(d) - 1))], 4)}
