"""INTEGRATION (real LMS + Squeezelite, SIMULATED Bluetooth): clock-drift compensation, CPU, memory bound.
CPU numbers are for the x86 dev host inside Docker - NOT representative of a Raspberry Pi (relative only)."""
import os
import time

import pytest

from btkit import analyze as A
from btkit.lab import Lab, make_bridge, save, wait_for
from btkit.sim_a2dp import SimulatedA2dpSource

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def flac_cpu_s():
    """CPU seconds (user+sys) of live `flac` children of this process, from /proc (os.times only counts reaped children)."""
    tot, tck = 0.0, os.sysconf("SC_CLK_TCK")
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            st = open(f"/proc/{pid}/stat").read()
            comm = st[st.index("(") + 1: st.rindex(")")]
            f = st[st.rindex(")") + 2:].split()
            if comm == "flac" and int(f[1]) == os.getpid():
                tot += (int(f[11]) + int(f[12])) / tck
        except (OSError, ValueError):
            pass
    return tot


def rss_kb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1])


@pytest.mark.parametrize("ppm", [-3000, 3000])
def test_clock_drift_between_phone_and_bridge(lab, ppm):
    """Exaggerated phone-vs-bridge clock offset (+/-3000 ppm; real phones are expected at tens of ppm - UNVERIFIED) so
    both correction paths are exercised within 100 s.  (At +/-300 ppm / 120 s the 36 ms skew stayed inside the feeder
    tolerance: zero corrections, zero underruns - see measurements/drift_*300ppm.json.)  Fast source -> frames dropped,
    slow source -> silence inserted; stream stays live, bounded, no player underrun."""
    rt, sim = make_bridge(sim=SimulatedA2dpSource(drift_ppm=ppm))
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6)
        off = Lab.log_size("player1"); time.sleep(100); t_end = time.time()
        f = rt.status()["feeder"]; sim.disconnect(); time.sleep(1)
        res = A.integrity(lab.capture(until=t_end))
        under = lab.underruns("player1", off)
        deltas = [d for _, d in res["jumps"] if d]
        out = {"drift_ppm": ppm, "duration_s": 100, "feeder_dropped_frames": f["dropped_frames"], "feeder_silence_frames": f["silence_frames"],
               "player_underruns": under, "discontinuities_in_player_output": len(deltas), "discontinuity_frames": deltas[:20],
               "expected_total_frames_correction": round(abs(ppm) * 1e-6 * 100 * 44100),
               "label": "INTEGRATION real LMS + SIMULATED BT (simulated clock offset)"}
        print("\n", out); save(f"drift_{ppm}ppm", out)
        assert under == 0
        corrected = f["dropped_frames"] if ppm > 0 else f["silence_frames"]
        assert corrected > 0                                                      # the path under test really ran
        assert corrected < 2 * out["expected_total_frames_correction"] + 3000
    finally:
        rt.stop()


@pytest.mark.parametrize("fmt", ["wav", "flac"])
def test_cpu_and_memory_bounded_over_60s(lab, fmt):
    rt, sim = make_bridge(fmt=fmt)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6); time.sleep(10)
        rss0, sim0 = rss_kb(), sim.cpu_s; t0 = time.time(); c0 = os.times(); fl0 = flac_cpu_s()
        time.sleep(60)
        c1 = os.times(); wall = time.time() - t0
        own = (c1.user + c1.system) - (c0.user + c0.system) - (sim.cpu_s - sim0)
        child = flac_cpu_s() - fl0
        out = {"format": fmt, "wall_s": round(wall, 1), "bridge_process_cpu_pct_of_one_core": round(100 * own / wall, 2),
               "flac_encoder_child_cpu_pct_of_one_core": round(100 * child / wall, 2), "rss_start_kb": rss0, "rss_end_kb": rss_kb(),
               "ring_capacity_bytes": rt.ring.cap * 4, "bandwidth_kbit_s": 1411 if fmt == "wav" else "~700-900 (content dependent)",
               "caveat": "x86 dev host in Docker, simulator CPU subtracted; NOT a Raspberry Pi measurement",
               "label": "INTEGRATION; relative numbers only"}
        print("\n", out); save(f"cpu_mem_{fmt}", out)
        assert out["rss_end_kb"] - rss0 < 20000                                  # no unbounded growth
    finally:
        rt.stop()
