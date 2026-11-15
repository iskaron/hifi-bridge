"""Test tracks for real phones (copy the WAV files to the phone and play them in any audio app).

``indexed`` track: 100 ms blocks = 90 ms tone chord + 10 ms silence.  Each chord encodes the block index with 16 data bits + 4 check bits
plus a pilot tone (700 Hz, always on).  Every bit is a *pair* of neighbouring tones (175 Hz apart) of which exactly one sounds, so the
decoder compares neighbours and is insensitive to gain and frequency tilt (codec, speaker, microphone, room).
Decoding (``analyze.py``) yields the index of every block -> drops, repeats, gaps, and (with two timestamped recordings) latency.
"""
from __future__ import annotations

import json
import wave

import numpy as np

RATE = 44100
BLOCK_S, TONE_S = 0.100, 0.090
DATA_BITS, CHECK_BITS = 16, 4
PILOT_HZ = 700.0
PAIRS = [(1000.0 + 350.0 * k, 1175.0 + 350.0 * k) for k in range(DATA_BITS + CHECK_BITS)]   # 1000 .. 7825 Hz
AMP = 0.045                                                               # per tone; 21 tones sound at once, sum stays < 0 dBFS


def check_bits(index: int) -> int:
    """4-bit check value: popcount and nibble sum, so single bit errors are always caught."""
    return (bin(index).count("1") + (index & 0xF) + ((index >> 4) & 0xF) + ((index >> 8) & 0xF) + (index >> 12)) & 0xF


def bits_for(index: int) -> list[int]:
    word = (index & 0xFFFF) | (check_bits(index & 0xFFFF) << DATA_BITS)
    return [(word >> k) & 1 for k in range(DATA_BITS + CHECK_BITS)]


def block(index: int, rate: int = RATE) -> np.ndarray:
    n_tone, n_block = int(TONE_S * rate), int(BLOCK_S * rate)
    t = np.arange(n_tone) / rate
    sig = AMP * np.sin(2 * np.pi * PILOT_HZ * t)
    rng = np.random.default_rng(index)                      # deterministic phases keep the crest factor low
    for (f0, f1), b in zip(PAIRS, bits_for(index)):
        sig += AMP * np.sin(2 * np.pi * (f1 if b else f0) * t + rng.uniform(0, 2 * np.pi))
    ramp = int(0.003 * rate)                                # 3 ms fades: no clicks, keeps the codec happy
    env = np.ones(n_tone); env[:ramp] = np.linspace(0, 1, ramp); env[-ramp:] = np.linspace(1, 0, ramp)
    out = np.zeros(n_block); out[:n_tone] = sig * env
    return out


def indexed_track(seconds: float, first_index: int = 1, rate: int = RATE) -> np.ndarray:
    n = int(round(seconds / BLOCK_S))
    return np.concatenate([block(first_index + i, rate) for i in range(n)])


def to_int16_stereo(x: np.ndarray) -> np.ndarray:
    m = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    return np.stack([m, m], axis=1)


def write_wav(path: str, x: np.ndarray, rate: int = RATE) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(to_int16_stereo(x).tobytes())


def make_phone_tracks(out_dir: str, continuity_min: float = 30) -> dict:
    """The set of files for the phone (plan C/E): long continuity track, short start/stop track, quiet-passage track."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    tracks = {
        "bab_continuity_30min.wav": (indexed_track(continuity_min * 60), f"E1/E3 continuity + drift; blocks 1..{int(continuity_min * 600)}"),
        "bab_startstop_2min.wav": (indexed_track(120, first_index=20001), "E2 play/pause/resume timing; blocks 20001..21200"),
        "bab_quiet_passage.wav": (np.concatenate([indexed_track(20, 30001), np.zeros(int(15 * RATE)), indexed_track(20, 30201)]),
                                  "C: 15 s of digital silence inside playing media must NOT count as pause"),
    }
    manifest = {}
    for name, (x, purpose) in tracks.items():
        write_wav(os.path.join(out_dir, name), x)
        manifest[name] = {"purpose": purpose, "seconds": round(len(x) / RATE, 3), "rate": RATE, "block_s": BLOCK_S}
    with open(os.path.join(out_dir, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    return manifest
