"""Demo audio content: generated in code (no third-party recordings), deterministic, clean, clearly not silence."""
import hashlib

import numpy as np
import pytest

from hifidemo import music as M


def as_array(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).astype(np.float64) / 32768.0


def test_catalogue_has_distinct_generated_tracks_with_metadata():
    assert len(M.TRACKS) >= 4
    assert len({t.id for t in M.TRACKS}) == len(M.TRACKS)
    for t in M.TRACKS:
        assert t.title and t.artist and t.album
        assert 30 <= t.seconds <= 120
        assert "generated" in M.LICENCE.lower()


@pytest.mark.parametrize("track", M.TRACKS, ids=lambda t: t.id)
def test_track_renders_as_clean_stereo_44k1(track):
    pcm = M.render(track)
    assert len(pcm) == int(round(track.seconds * M.RATE)) * 4
    x = as_array(pcm)
    peak = np.abs(x).max()
    assert 0.85 <= peak <= 0.9                        # -1 dBFS: loud but not clipping
    rms_db = 20 * np.log10(np.sqrt(np.mean(x ** 2)))
    assert -16.0 <= rms_db <= -11.0, rms_db           # loudness policy: the demo must not be quiet (was -17.8 dBFS before) nor harsh
    assert abs(x.mean()) < 0.01                       # no DC offset
    assert np.abs(x[0]).max() < 0.01 and np.abs(x[-1]).max() < 0.01   # faded in and out (no clicks at the ends)
    assert not np.array_equal(x[:, 0], x[:, 1])       # real stereo


def test_rendering_is_deterministic_and_tracks_differ():
    digests = [hashlib.sha256(M.render(t)).hexdigest() for t in M.TRACKS]
    assert digests == [hashlib.sha256(M.render(t)).hexdigest() for t in M.TRACKS]
    assert len(set(digests)) == len(digests)


def test_notification_is_short_and_audible():
    x = as_array(M.notification())
    assert 0.3 <= len(x) / M.RATE <= 1.5
    assert np.abs(x).max() > 0.2


def test_radio_is_endless_continuous_and_contains_a_jingle():
    r = M.Radio()
    first = r.read(M.RATE * 4 * 3)                    # 3 s
    assert len(first) == M.RATE * 4 * 3
    total = sum(len(r.read(M.RATE * 4)) for _ in range(int(r.cycle_seconds) + 5))
    assert total == M.RATE * 4 * (int(r.cycle_seconds) + 5)   # wraps around without gaps
    assert r.jingle_seconds > 0
    assert np.sqrt(np.mean(as_array(first) ** 2)) > 0.02


def test_radio_reader_start_position_follows_the_wall_clock():
    """Every listener joins the live programme (like a real radio), not the start of the loop."""
    a, b = M.Radio(clock=lambda: 1000.0), M.Radio(clock=lambda: 1010.0)
    assert (b.position - a.position) % a.frames == 10 * M.RATE
