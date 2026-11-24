"""UNIT (lab harness): loading a time window of a player's PCM-tap capture must cost the window, not the lab's whole history.

Found 2026-11-24: after three days of lab uptime the capture of player1 had grown to 281 MB PCM + 43 MB index; `analyze.load()` parsed
all of it (3 s, holding the GIL), so the synthetic radio served by a thread of the SAME test process fell behind real time and the
player output went silent - inside the very window the test then measured ("audible_fraction 0.78-0.94 > 0.97", 4 integration tests,
also on the unchanged code of develop b648936). Proof run: one load = 3.07 s, test-process threads stalled up to 0.44 s, radio silent
for 4 s. Label: UNIT (synthetic capture files written by the real PcmTap)."""
import json
import os
import random
import time

import numpy as np
import pytest

from btkit import analyze as A
from btkit.pcm_tap import PcmTap


def naive_load(path, since=0.0, until=1e18):
    """The previous implementation (parse everything), kept as the reference the windowed loader must equal."""
    rows = []
    with open(os.path.join(path, "index.jsonl")) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    raw = np.fromfile(os.path.join(path, "pcm.raw"), dtype=np.int16)
    pcm = raw[: len(raw) // 2 * 2].reshape(-1, 2)
    a = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 3))
    m = (a[:, 0] >= since) & (a[:, 0] <= until) if len(a) else np.zeros(0, bool)
    a = a[m]
    off, nb = a[:, 2].astype(np.int64), a[:, 1].astype(np.int64)
    st = off >= 0
    if st.any():
        lo, hi = int(off[st].min()), int((off[st] + nb[st]).max())
        pcm = pcm[lo // A.FB: hi // A.FB]
        off = np.where(st, off - lo, -1)
    else:
        pcm = pcm[:0]
    return A.Capture(a[:, 0], nb, off, pcm)


def write_capture(d, seconds=120.0, t0=1_795_000_000.0, seed=1):
    """A capture as the real tap writes it: 10 ms reads, audio with silent stretches (coalesced, not stored)."""
    rnd = random.Random(seed)
    tap = PcmTap(str(d))
    t, k = t0, 0
    while t < t0 + seconds:
        silent = (int(t - t0) // 7) % 3 == 2                                  # every third 7-s block is silence
        n = 441
        data = b"\0" * (n * 4) if silent else np.full((n, 2), (k % 30000) + 1, dtype=np.int16).tobytes()
        t += n / 44100 + rnd.uniform(-0.0005, 0.0005)
        tap.feed(data, t)
        k += 1
    tap.pcm.close(); tap.idx.close()
    with open(os.path.join(d, "index.jsonl"), "a") as fh:
        fh.write("[1795000999.0, 17")                                         # a half-written last line, as while the tap runs
    return t0


def same(a, b):
    assert np.array_equal(a.t_end, b.t_end) and np.array_equal(a.nbytes, b.nbytes) and np.array_equal(a.offset, b.offset)
    assert np.array_equal(a.pcm, b.pcm)


def test_windowed_load_equals_the_full_parse(tmp_path):
    t0 = write_capture(tmp_path)
    rnd = random.Random(7)
    windows = [(0.0, 1e18), (t0 - 50, t0 + 3), (t0 + 119.9, 1e18), (t0 + 500, 1e18), (t0 + 20.5, t0 + 21.0)]
    windows += [(t0 + rnd.uniform(0, 120), None) for _ in range(20)]
    for since, until in windows:
        until = until if until is not None else since + rnd.uniform(0.01, 30)
        same(A.load(str(tmp_path), since=since, until=until), naive_load(str(tmp_path), since=since, until=until))


def test_a_window_only_silence_and_an_empty_capture(tmp_path):
    t0 = write_capture(tmp_path)
    c = A.load(str(tmp_path), since=t0 + 15.0, until=t0 + 20.0)              # inside a silent block (14-21 s)
    same(c, naive_load(str(tmp_path), since=t0 + 15.0, until=t0 + 20.0))
    e = tmp_path / "empty"; e.mkdir(); (e / "index.jsonl").write_text(""); (e / "pcm.raw").write_bytes(b"")
    assert A.load(str(e)).n == 0 and len(A.load(str(e)).pcm) == 0


@pytest.mark.parametrize("hours", [3.0])
def test_load_cost_does_not_grow_with_the_lab_history(tmp_path, hours):
    """A few seconds out of hours of history: milliseconds, not the seconds the full parse takes."""
    d = tmp_path / "long"; d.mkdir()
    n = int(hours * 3600 * 100)                                              # 100 index rows per second, like the real tap
    t0 = 1_795_000_000.0
    with open(d / "index.jsonl", "w") as fh:
        fh.writelines(f"[{t0 + i / 100:.6f}, 1764, {i * 1764}]\n" for i in range(n))
    with open(d / "pcm.raw", "wb") as fh:
        fh.truncate(n * 1764)                                                # sparse: size matters, content does not
    t = time.perf_counter()
    c = A.load(str(d), since=t0 + hours * 3600 - 8, until=t0 + hours * 3600 - 1)
    dt = time.perf_counter() - t
    assert c.n == 701 and len(c.pcm) == 701 * 441
    assert dt < 0.25, dt
