"""INTEGRATION (real LMS + Squeezelite, SIMULATED Bluetooth): startup latency and delivery-hiccup tolerance
of the default buffering policy.  (An `icy-br` hint and silence pre-roll were tried and gave NO startup
gain - see measurements/NEGATIVE_startup_policy_icybr_preroll.json and docs/FINDINGS.md.)"""
import statistics
import time

import pytest

from btkit import analyze as A
from btkit.lab import Lab, make_bridge, save, wait_for

pytestmark = pytest.mark.integration


def startup_runs(lab, fmt, runs=5):
    vals = []
    rt, sim = make_bridge(fmt=fmt)
    try:
        sim.connect()
        for _ in range(runs):
            lab.t_reset = time.time()
            sim.play(); assert wait_for(lambda: lab.mode() == "play", 6); time.sleep(4)
            first, _ = A.edges(lab.capture(since=lab.t_reset))
            vals.append(first - sim.probes["t_play_event"])
            sim.pause(); time.sleep(1.5)
    finally:
        rt.stop()
    return vals


def hiccup_underruns(lab, hold_s):
    """Phone keeps playing while the bridge's HTTP writers stall for hold_s (test hook); count player underruns."""
    rt, sim = make_bridge(pause_strategy="keepalive_silence")
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6); time.sleep(5)
        off = Lab.log_size("player1"); t0 = time.time()
        rt.stream.debug_hold_writes(hold_s); time.sleep(hold_s + 6)
        return lab.underruns("player1", off)
    finally:
        rt.stop(); lab.reset(); time.sleep(1.5)


@pytest.mark.slow
def test_startup_latency_wav_vs_flac(lab):
    wav = startup_runs(lab, "wav"); lab.reset(); time.sleep(1); flac = startup_runs(lab, "flac")
    print(f"\nWAV  play->first player output: {['%.2f' % v for v in wav]} median {statistics.median(wav):.2f}s"
          f"\nFLAC play->first player output: {['%.2f' % v for v in flac]} median {statistics.median(flac):.2f}s")
    save("startup_wav_vs_flac", {"wav_s": wav, "flac_s": flac, "wav_median_s": statistics.median(wav), "flac_median_s": statistics.median(flac),
                                 "explanation": "Squeezelite waits for LMS's start threshold (255 KB of stream data) which fills at real-time rate (~1.45 s of 44.1k/16/2 PCM)",
                                 "label": "INTEGRATION real LMS + SIMULATED BT; NOT acoustic"})
    assert statistics.median(wav) < 2.5 and statistics.median(flac) < 3.0


@pytest.mark.slow
def test_hiccup_tolerance_of_default_buffering(lab):
    res = {}
    for hold in (0.9, 1.5, 2.5):
        lab.reset(); time.sleep(1)
        res[hold] = hiccup_underruns(lab, hold)
        print(f"\nstream writers stalled {hold}s -> player underruns: {res[hold]}")
    save("hiccup_tolerance_default_buffering", {"underruns_by_stall_s": res,
         "note": "an underrun makes LMS pause the player and re-buffer (~5.5 s audible gap, see stall_baseline_vs_keepalive)",
         "label": "INTEGRATION real LMS + SIMULATED BT"})
    assert res[0.9] == 0                                       # sub-second delivery hiccups are absorbed by the player buffer
