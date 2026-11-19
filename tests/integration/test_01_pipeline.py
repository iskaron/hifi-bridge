"""INTEGRATION (real LMS 9.1.2 + real Squeezelite) with SIMULATED Bluetooth input.
Not evidence of real Bluetooth, phones, speakers or acoustic timing."""
import time

import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, save, wait_for

pytestmark = pytest.mark.integration


def probes(lab, rt, sim, cap):
    t_play = sim.probes["t_play_event"]
    first_out, last_out = A.edges(cap)
    cmd = rt.lms.command_times[0] if rt.lms.command_times else (None, None)
    ev = {n: t for t, n, d in rt.stream.events if n in ("client_connected", "first_bytes_sent")}
    clients = [(t, d) for t, n, d in rt.stream.events if n == "client_connected"]
    return {
        "t_play_event": 0.0,
        "t_first_bt_pcm": sim.probes.get("t_first_pcm", 0) - t_play,
        "t_lms_command_sent": (cmd[0] - t_play) if cmd[0] else None,
        "t_lms_command_returned": (cmd[1] - t_play) if cmd[1] else None,
        "t_stream_client_connected": [(round(t - t_play, 3), d) for t, d in clients],
        "t_first_lms_output": (first_out - t_play) if first_out else None,
    }


@pytest.mark.parametrize("fmt", ["wav", "flac"])
def test_end_to_end_bit_exact_and_startup_probes(lab, fmt):
    rt, sim = make_bridge(fmt=fmt)
    try:
        assert sim.connect()
        time.sleep(0.5)
        assert lab.mode() == "stop"                          # connection alone does not start LMS
        sim.play()
        assert wait_for(lambda: rt.controller.ownership == "owned", 5)
        assert wait_for(lambda: lab.mode() == "play", 5)
        assert lab.url() == rt.controller.session.url        # LMS plays OUR session URL
        time.sleep(10)
        t_end = time.time()
        sim.disconnect()
        assert wait_for(lambda: lab.mode() == "stop", 5)     # disconnect released our stream
        time.sleep(0.5)
        cap = lab.capture(until=t_end + 0.2)
        res = A.integrity(cap)
        p = probes(lab, rt, sim, cap)
        print("PROBES", p, "INTEGRITY", {k: res.get(k) for k in ("blocks", "matched", "mismatched", "jumps")})
        assert res["blocks"] > 80, res
        assert res["mismatched"] <= 1, res                    # only the tail block at stop may differ
        assert all(d == 0 for _, d in res["jumps"]), res      # no lost or repeated frames
        assert p["t_first_lms_output"] is not None
        save(f"pipeline_startup_{fmt}", {"probes": p, "integrity": {k: res.get(k) for k in ("blocks", "matched", "mismatched", "jumps")},
                                         "feeder": rt.status()["feeder"], "label": "INTEGRATION real LMS + SIMULATED BT"})
    finally:
        rt.stop()
