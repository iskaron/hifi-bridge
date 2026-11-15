"""INTEGRATION (real LMS + Squeezelite, SIMULATED Bluetooth): Pause/Resume behaviour, grace period,
stream-stall handling.  Timings are virtual-DAC (software) measurements - NOT acoustic."""
import statistics
import time

import pytest

from btkit import analyze as A
from btkit.lab import Lab, make_bridge, save, wait_for

pytestmark = pytest.mark.integration
RUNS = 3


def one_cycle(lab, rt, sim):
    """play 6 s -> pause 4 s -> resume; returns (pause_to_silence, resume_to_audio)."""
    sim.play(); assert wait_for(lambda: lab.mode() == "play", 6)
    time.sleep(6)
    t_pause = time.time(); sim.pause()
    time.sleep(4)
    t_resume = time.time(); sim.play()
    time.sleep(5)
    t_end = time.time()
    cap = lab.capture(since=t_pause - 0.5, until=t_end)
    # last audible output after the pause, first audible after the resume
    before = A.load("/captures/player1", since=t_pause - 0.5, until=t_resume - 0.3)
    _, last = A.edges(before)
    after = A.load("/captures/player1", since=t_resume, until=t_end)
    first, _ = A.edges(after)
    sim.pause(); time.sleep(1)
    return (last - t_pause) if last else None, (first - t_resume) if first else None


@pytest.mark.slow
@pytest.mark.parametrize("strategy", ["drop_restart", "keepalive_silence"])
def test_pause_resume_latency_by_strategy(lab, strategy):
    rt, sim = make_bridge(pause_strategy=strategy)
    out = []
    try:
        sim.connect()
        for i in range(RUNS):
            lab.t_reset = time.time()
            out.append(one_cycle(lab, rt, sim))
            time.sleep(1.5)
        assert all(p is not None and r is not None for p, r in out), out
        p = [x[0] for x in out]; r = [x[1] for x in out]
        print(f"\n{strategy}: pause->silence {['%.2f' % v for v in p]}  resume->audio {['%.2f' % v for v in r]}")
        save(f"pause_resume_{strategy}", {"runs": RUNS, "pause_to_last_audible_output_s": p, "resume_to_first_output_s": r,
                                         "median_pause_s": statistics.median(p), "median_resume_s": statistics.median(r),
                                         "method": "virtual DAC (paced pipe from Squeezelite -o -) arrival time vs simulated phone event time; same host clock",
                                         "label": "INTEGRATION real LMS + SIMULATED BT; NOT acoustic"})
        if strategy == "drop_restart":
            assert statistics.median(p) < 0.5               # silence promptly after Pause
        assert statistics.median(r) < 3.5                    # resume within seconds
    finally:
        rt.stop()


@pytest.mark.slow
def test_grace_30s_keepalive_releases_only_bridge_stream(lab):
    rt, sim = make_bridge(pause_strategy="keepalive_silence", grace=30.0)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(3)
        t_pause = time.time(); sim.pause()
        time.sleep(27); assert lab.mode() == "play" and lab.url() == rt.controller.session.url      # still held at 27 s
        assert wait_for(lambda: lab.mode() == "stop", 6)
        t_rel = time.time() - t_pause
        print("keepalive grace release at", round(t_rel, 1), "s after Pause")
        assert 29.5 < t_rel < 33.5
        save("grace_keepalive", {"configured_s": 30, "lms_released_after_s": t_rel, "label": "INTEGRATION real LMS + SIMULATED BT"})
    finally:
        rt.stop()


@pytest.mark.slow
def test_resume_after_grace_is_fresh_play_not_stale_audio(lab):
    rt, sim = make_bridge(pause_strategy="drop_restart", grace=6.0)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(3)
        sim.pause(); time.sleep(8)
        assert rt.controller.ownership == "none"
        f_before = sim.frame
        t = time.time(); sim.play()
        assert wait_for(lambda: lab.mode() == "play", 5)
        time.sleep(5)
        cap = lab.capture(since=t)
        res = A.integrity(cap)
        # first audio after the resume must be at/after the resume position: nothing stale replayed
        assert res["start_index"] >= f_before - 1000, (res["start_index"], f_before)
    finally:
        rt.stop()


def _underruns_during_stall(lab, fill: bool):
    rt, sim = make_bridge(pause_strategy="keepalive_silence")
    if not fill:
        rt.feeder.set_fill = lambda enabled: None        # BASELINE: no silence keepalive (stream really stalls)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6); time.sleep(6)
        off = Lab.log_size("player1"); t0 = time.time()
        sim.stall(4.0); time.sleep(4.2)                  # source delivers nothing for 4 s (transport 'active')
        t_stall_end = time.time(); time.sleep(10)
        cap = lab.capture(since=t_stall_end)
        first, _ = A.edges(cap)
        return {"underruns": lab.underruns("player1", off), "audio_returned_after_s": (first - t_stall_end) if first else None,
                "audible_fraction_next_10s": A.audible_fraction(cap, t_stall_end, t_stall_end + 10)}
    finally:
        rt.stop()


@pytest.mark.slow
def test_stall_baseline_vs_silence_keepalive(lab):
    base = _underruns_during_stall(lab, fill=False)
    lab.reset(); time.sleep(2)
    keep = _underruns_during_stall(lab, fill=True)
    print("\nBASELINE (no silence fill):", base, "\nSILENCE KEEPALIVE:", keep)
    save("stall_baseline_vs_keepalive", {"stall_s": 4, "baseline_no_fill": base, "silence_keepalive": keep,
                                         "label": "INTEGRATION real LMS + SIMULATED BT"})
    assert keep["underruns"] == 0
    assert base["underruns"] >= 1 and (base["audio_returned_after_s"] or 99) > (keep["audio_returned_after_s"] or 0)
    assert keep["audible_fraction_next_10s"] > base["audible_fraction_next_10s"]
