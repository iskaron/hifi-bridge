"""Decode a user's own audio file for a virtual phone: WAV (any rate/channels, 16-bit), FLAC, MP3, Ogg Vorbis -> 44.1 kHz stereo s16le.

Decoding happens locally inside the demo container with the image's tools (flac, mpg123, oggdec). Nothing is uploaded anywhere else.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
import wave

import numpy as np

RATE = 44100
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_SECONDS = 15 * 60
SUFFIXES = (".wav", ".flac", ".mp3", ".ogg", ".oga")


class DecodeError(Exception):
    pass


def check_size(n: int) -> None:
    if n > MAX_UPLOAD_BYTES:
        raise DecodeError(f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")


def title_from_name(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)
    return " ".join(base.replace("_", " ").split())[:80] or "Untitled"


def _to_stereo_44k1(x: np.ndarray, rate: int) -> bytes:
    """x: float array (n, channels) in [-1, 1]."""
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.shape[1] > 2:
        x = x[:, :2]
    if rate != RATE and len(x):
        n = int(round(len(x) * RATE / rate))
        src = np.arange(len(x)) / rate
        dst = np.arange(n) / RATE
        x = np.stack([np.interp(dst, src, x[:, c]) for c in range(2)], axis=1)
    x = x[: MAX_SECONDS * RATE]
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def _wav(data: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise DecodeError(f"not a readable WAV file ({e})") from e
    if sw != 2:
        raise DecodeError("only 16-bit WAV is supported")
    if ch == 2 and rate == RATE:
        return raw[: MAX_SECONDS * RATE * 4]
    x = np.frombuffer(raw[: len(raw) - len(raw) % (2 * ch)], dtype="<i2").astype(np.float64).reshape(-1, ch) / 32768.0
    return _to_stereo_44k1(x, rate)


def _tool(argv_for, data: bytes, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hifidemo-") as d:
        src, out = os.path.join(d, "in" + suffix), os.path.join(d, "out.wav")
        with open(src, "wb") as fh:
            fh.write(data)
        r = subprocess.run(argv_for(src, out), capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(out):
            raise DecodeError(f"could not decode the file ({r.stderr.decode(errors='replace').strip()[:160]})")
        with open(out, "rb") as fh:
            return _wav(fh.read())


def decode(data: bytes, name: str) -> bytes:
    check_size(len(data))
    suffix = os.path.splitext(name.lower())[1]
    if suffix not in SUFFIXES:
        raise DecodeError(f"unsupported file type {suffix or '(none)'} (use WAV, FLAC, MP3 or Ogg Vorbis)")
    if suffix == ".wav":
        return _wav(data)
    if suffix == ".flac":
        return _tool(lambda s, o: ["flac", "-s", "-d", "-f", "-o", o, s], data, suffix)
    if suffix == ".mp3":
        return _tool(lambda s, o: ["mpg123", "-q", "-w", o, s], data, suffix)
    return _tool(lambda s, o: ["oggdec", "-Q", "-o", o, s], data, suffix)
