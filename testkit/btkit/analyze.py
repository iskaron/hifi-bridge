"""Analyze a PCM-tap capture against the deterministic synthetic signal.

Time base: every timestamp is CLOCK_REALTIME inside containers sharing one host
kernel clock, so cross-container comparisons are consistent.  These are
player-output *PCM arrival* times, not acoustic times.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

from . import signal as sig

FB = sig.FRAME_BYTES


@dataclass
class Capture:
    t_end: np.ndarray      # wall time at end of each read
    nbytes: np.ndarray
    offset: np.ndarray     # offset in pcm.raw or -1 (silence not stored)
    pcm: np.ndarray        # int16 (n,2) stored samples

    @property
    def n(self):
        return len(self.t_end)


SEEK_MARGIN_S = 10.0     # rows are written in time order; a coalesced-silence row may carry a slightly earlier time than its predecessor


def _row_time(line: bytes) -> float | None:
    try:
        return float(json.loads(line)[0])
    except (ValueError, IndexError, TypeError):
        return None


def _index_start(f, since: float, size: int) -> int:
    """Byte position from which reading the index finds every row with t >= since: binary search over the file (the tap appends
    rows in time order), backed off by SEEK_MARGIN_S. Costs O(log size) reads instead of parsing the lab's whole history."""
    target = since - SEEK_MARGIN_S
    lo, hi = 0, size
    while hi - lo > 65536:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()                                  # the rest of a line cut by the seek
        pos = f.tell()
        t = _row_time(f.readline())
        if t is None or t >= target:
            hi = mid
        else:
            lo = pos
    return lo


def load(path: str, since: float = 0.0, until: float = 1e18) -> Capture:
    """The rows of [since, until] and their stored PCM. Reads only that window (binary search in the time-ordered index, one ranged
    read of pcm.raw): the cost does not grow with the lab's uptime. Equal to parsing everything (tests/unit/test_capture_load.py)."""
    rows = []
    with open(os.path.join(path, "index.jsonl"), "rb") as f:
        size = os.fstat(f.fileno()).st_size
        start = _index_start(f, since, size) if since > 0 else 0
        f.seek(start)
        if start:
            f.readline()                              # start at a line boundary
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue                              # a half-written last line while the tap is writing
            if row[0] > until + SEEK_MARGIN_S:
                break
            rows.append(row)
    a = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 3))
    m = (a[:, 0] >= since) & (a[:, 0] <= until) if len(a) else np.zeros(0, bool)
    a = a[m]
    off = a[:, 2].astype(np.int64)
    nb = a[:, 1].astype(np.int64)
    st = off >= 0
    if st.any():                      # read only the stored PCM of the window and re-base offsets
        lo, hi = int(off[st].min()), int((off[st] + nb[st]).max())
        lo -= lo % FB
        raw = np.fromfile(os.path.join(path, "pcm.raw"), dtype=np.int16, offset=lo, count=(hi - lo) // 2)
        pcm = raw[: len(raw) // 2 * 2].reshape(-1, 2)[: (hi - lo) // FB]
        off = np.where(st, off - lo, -1)
    else:
        pcm = np.zeros((0, 2), dtype=np.int16)
    return Capture(a[:, 0], nb, off, pcm)


def nonsilent_spans(c: Capture, min_gap_s: float = 0.25):
    """Return [(t_start, t_end, first_stored_offset_bytes, nbytes_total)] spans of non-silent PCM."""
    spans = []
    cur = None
    for t, nb, off in zip(c.t_end, c.nbytes, c.offset):
        silent = True
        if off >= 0:
            seg = c.pcm[off // FB : (off + nb) // FB]
            silent = not seg.any()
        if silent:
            continue
        t0 = t - nb / (sig.RATE * FB)
        if cur and t0 - cur[1] <= min_gap_s:
            cur[1] = t
            cur[3] += nb
        else:
            if cur:
                spans.append(tuple(cur))
            cur = [t0, t, off, nb]
    if cur:
        spans.append(tuple(cur))
    return spans


def frame_timeline(c: Capture):
    """Yield (t_wall_of_frame0, stored_offset_frames, nframes) per read; frame time assumes the read ended at t_end."""
    for t, nb, off in zip(c.t_end, c.nbytes, c.offset):
        yield t - nb / (sig.RATE * FB), off // FB if off >= 0 else -1, nb // FB


def integrity(c: Capture, block: int = 4410, seed: int = 0, search=(0, 20_000_000)):
    """Match stored non-silent PCM to the generator and report discontinuities.

    Returns dict: start_index, blocks, matched, mismatched, jumps [(block_no, delta_frames)].
    A jump delta of 0 means contiguous; >0 = frames skipped (lost), <0 = repeated.
    """
    stored = c.pcm
    nz = np.where(stored.any(axis=1))[0]
    if len(nz) == 0:
        return {"blocks": 0, "error": "no non-silent PCM"}
    first = int(nz[0])
    start = sig.locate(stored[first : first + 256], *search, seed=seed)
    if start is None:
        return {"blocks": 0, "error": "could not align to reference (samples altered?)", "first_stored_frame": first}
    jumps, matched, mism, blocks = [], 0, 0, 0
    expect = start
    pos = first
    while pos + block <= len(stored):
        win = stored[pos : pos + block]
        if not win.any():            # silence: skip forward by block, recover alignment afterwards
            pos += block
            expect += block
            continue
        blocks += 1
        ref = sig.frames(expect, block, seed)
        if np.array_equal(ref, win):
            matched += 1
            pos += block
            expect += block
            continue
        mism += 1
        # try to re-locate within +-3 s
        got = sig.locate(win[:256], max(0, expect - 3 * sig.RATE), expect + 3 * sig.RATE, seed=seed)
        if got is None:
            jumps.append((blocks, None))
            pos += block
            expect += block
        else:
            jumps.append((blocks, got - expect))
            expect = got + block
            pos += block
    return {"start_index": start, "blocks": blocks, "matched": matched, "mismatched": mism, "jumps": jumps,
            "frames_checked": blocks * block}


def stored_frame_time(c: Capture, p: int) -> float | None:
    """Wall time at which stored frame ``p`` (index into c.pcm) was consumed by the virtual DAC."""
    for t, nb, off in zip(c.t_end, c.nbytes, c.offset):
        if off < 0:
            continue
        a, n = off // FB, nb // FB
        if a <= p < a + n:
            return float(t - (a + n - p) / sig.RATE)
    return None


def time_of_index(c: Capture, start_index: int, idx: int, first_stored: int | None = None) -> float | None:
    """Wall time of generator frame ``idx`` in capture ``c`` (assumes contiguous playout from start_index)."""
    nz = np.where(c.pcm.any(axis=1))[0]
    if len(nz) == 0:
        return None
    first = int(nz[0]) if first_stored is None else first_stored
    return stored_frame_time(c, first + (idx - start_index))


def edges(c: Capture):
    """(t_first_nonsilent_start, t_last_nonsilent_end) of player-output PCM in the window; None if silent."""
    first = last = None
    for t, nb, off in zip(c.t_end, c.nbytes, c.offset):
        if off < 0:
            continue
        if c.pcm[off // FB:(off + nb) // FB].any():
            if first is None:
                first = float(t - nb / (sig.RATE * FB))
            last = float(t)
    return first, last


def audible_fraction(c: Capture, t0: float, t1: float) -> float:
    """Fraction of [t0, t1] during which the virtual DAC output was non-silent (10 ms resolution)."""
    tot = aud = 0.0
    for t, nb, off in zip(c.t_end, c.nbytes, c.offset):
        d = nb / (sig.RATE * FB)
        a, b = t - d, t
        lo, hi = max(a, t0), min(b, t1)
        if hi <= lo:
            continue
        tot += hi - lo
        if off >= 0 and c.pcm[off // FB:(off + nb) // FB].any():
            aud += hi - lo
    return aud / (t1 - t0) if t1 > t0 else 0.0
