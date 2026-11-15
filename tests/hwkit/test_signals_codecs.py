"""Test-track robustness. Codec tests use REAL FFmpeg SBC/AAC encoders+decoders (label: INTEGRATION codec, SIMULATED channel) - this
says the signal survives the codecs; it says nothing about a real phone's encoder settings or radio losses."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from hwkit import analyze as A
from hwkit import signals as S

N = int(S.BLOCK_S * S.RATE)


def test_clean_roundtrip_every_block_decoded():
    x = S.indexed_track(20, first_index=500)
    c = A.continuity(A.find_blocks(x, S.RATE))
    assert c["clean"] and c["decoded"] == 200 and c["first"] == 500 and c["last"] == 699


def test_check_bits_catch_every_single_bit_error():
    for idx in (1, 2, 255, 4096, 65535, 12345):
        word = (idx & 0xFFFF) | (S.check_bits(idx) << 16)
        for k in range(20):
            bad = word ^ (1 << k)
            assert S.check_bits(bad & 0xFFFF) != (bad >> 16), (idx, k)


def test_drops_repeats_and_gaps_are_classified_exactly_under_gain_noise_and_tilt():
    x = S.indexed_track(10, first_index=100)
    y = 0.3 * np.convolve(x, np.ones(5) / 5, mode="same") + np.random.default_rng(1).normal(0, 0.003, len(x))
    y = np.concatenate([np.zeros(int(0.037 * S.RATE)), y[:20 * N], y[23 * N:50 * N], np.zeros(int(0.5 * S.RATE)), y[50 * N:60 * N], y[58 * N:]])
    c = A.continuity(A.find_blocks(y, S.RATE))
    assert c["undecodable"] == 0
    assert (c["drops"], c["dropped_blocks"], c["repeats"]) == (1, 3, 1)
    assert len(c["gaps"]) == 1 and abs(c["gaps"][0]["gap_s"] - 0.5) < 0.01


@pytest.mark.parametrize("cut_ms", [0, 3, 20, 44, 60, 89, 95])
def test_recording_that_starts_inside_a_block_is_not_a_repeat(cut_ms):
    x = S.indexed_track(3, first_index=10)[int(cut_ms / 1000 * S.RATE):]
    c = A.continuity(A.find_blocks(x, S.RATE))
    assert c["repeats"] == 0 and c["drops"] == 0 and c["last"] == 39, c
    assert c["first"] == 10 if cut_ms == 0 else c["first"] in (10, 11)      # a partial first block may still decode (correctly)


def test_latency_between_two_recordings_on_one_clock():
    x = S.indexed_track(5, first_index=1)
    later = np.concatenate([np.zeros(int(0.250 * S.RATE)), 0.2 * x])
    r = A.latency(A.find_blocks(x, S.RATE), 100.0, A.find_blocks(later, S.RATE), 100.5)
    assert r["matched"] == 50 and abs(r["median_s"] - 0.750) < 0.005


def test_undecodable_is_reported_never_guessed():
    x = S.indexed_track(5, first_index=1)
    y = np.convolve(x, np.ones(9) / 9, mode="same")           # deep comb nulls in the tone band
    blocks = A.find_blocks(y, S.RATE)
    decoded = [i for _, i in blocks if i is not None]
    assert all(1 <= i <= 50 for i in decoded)                  # whatever is decoded is correct
    assert decoded == sorted(decoded)


ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the btbridge-hwkit image (ffmpeg)")


def _through(tmp_path, x, *enc_args, ext):
    src, mid, out = tmp_path / "in.wav", tmp_path / f"mid.{ext}", tmp_path / "out.wav"
    S.write_wav(str(src), x)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), *enc_args, str(mid)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mid), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(out)], check=True)
    y, rate = A.read_wav(str(out))
    return A.continuity(A.find_blocks(y, rate))


@ffmpeg
@pytest.mark.parametrize("bitrate", ["328k", "229k"])          # A2DP SBC 'high quality' (bitpool 53) and 'middle quality' (bitpool 35)
def test_survives_real_sbc(tmp_path, bitrate):
    c = _through(tmp_path, S.indexed_track(30, first_index=1000), "-c:a", "sbc", "-b:a", bitrate, ext="sbc")
    assert c["decoded"] >= 298 and c["drops"] == 0 and c["repeats"] == 0 and c["undecodable"] <= 2, c


@ffmpeg
@pytest.mark.parametrize("bitrate", ["256k", "128k"])
def test_survives_real_aac(tmp_path, bitrate):
    c = _through(tmp_path, S.indexed_track(30, first_index=1000), "-c:a", "aac", "-b:a", bitrate, ext="m4a")
    assert c["decoded"] >= 298 and c["drops"] == 0 and c["repeats"] == 0, c


@ffmpeg
def test_survives_48k_phone_rate_plus_sbc_plus_resampling_back(tmp_path):
    c = _through(tmp_path, S.indexed_track(30, first_index=1000), "-ar", "48000", "-c:a", "sbc", "-b:a", "328k", ext="sbc")
    assert c["decoded"] >= 298 and c["drops"] == 0, c


def test_phone_track_set_and_manifest(tmp_path):
    import json, wave
    m = S.make_phone_tracks(str(tmp_path), continuity_min=0.2)
    assert set(m) == {"bab_continuity_30min.wav", "bab_startstop_2min.wav", "bab_quiet_passage.wav"}
    with wave.open(str(tmp_path / "bab_startstop_2min.wav")) as w:
        assert w.getframerate() == 44100 and w.getnchannels() == 2 and abs(w.getnframes() / 44100 - 120) < 0.01
    assert json.load(open(tmp_path / "MANIFEST.json")) == m
