"""Minimal MPEG audio frame-header reader: proves that a byte stream really is MPEG-1 Layer III at a constant bitrate.

Used by the unit tests, the integration tests and ``python3 -m btbridge.stream.mp3check``. It reads headers only (it does not
decode); a decoder round trip is covered separately by the tests.
"""
from __future__ import annotations

from dataclasses import dataclass

_BITRATES_V1_L3 = (None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None)
_RATES_V1 = (44100, 48000, 32000, None)
_MODES = ("stereo", "joint", "dual", "mono")


@dataclass(frozen=True)
class FrameHeader:
    bitrate_kbps: int
    sample_rate: int
    mode: str
    padding: bool
    size: int


def parse_header(b: bytes) -> FrameHeader | None:
    """The MPEG-1 Layer III header at the start of ``b`` (4 bytes needed), or None if ``b`` does not start with one."""
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    version, layer = (b[1] >> 3) & 3, (b[1] >> 1) & 3
    if version != 3 or layer != 1:                          # 3 = MPEG-1, 1 = Layer III
        return None
    br, sr = _BITRATES_V1_L3[b[2] >> 4], _RATES_V1[(b[2] >> 2) & 3]
    if br is None or sr is None:
        return None
    pad = bool((b[2] >> 1) & 1)
    return FrameHeader(br, sr, _MODES[b[3] >> 6], pad, 144 * br * 1000 // sr + (1 if pad else 0))


def walk(data: bytes, start: int = 0) -> tuple[list[FrameHeader], int]:
    """Consecutive frames from ``start`` (which must be a frame start). Returns (frames, offset after the last complete frame)."""
    out, i = [], start
    while True:
        h = parse_header(data[i:i + 4])
        if h is None or i + h.size > len(data):
            return out, i
        out.append(h)
        i += h.size


def find_first_frame(data: bytes, limit: int = 4096) -> int:
    """Offset of the first position from which at least three consecutive valid frames follow (skips an ID3v2 tag or junk); -1 if none."""
    for i in range(min(limit, len(data))):
        if parse_header(data[i:i + 4]) and len(walk(data, i)[0]) >= 3:
            return i
    return -1
