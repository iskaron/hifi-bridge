"""EXPERIMENT (INTEGRATION lab, real LMS + 3 Squeezelite sync group, SIMULATED Bluetooth, trace ON): what the bridge's audio-path
trace and the players' outputs show when ONE member of the sync group stops taking data for a while (its Squeezelite is frozen with
SIGSTOP by the host-side wrapper ``scripts/exp_group_member_stall.sh`` - a stand-in for a player with a Wi-Fi hiccup).

It does not prove what happened on the Pi (G1-S); it shows the lab signature of a player-side stall so the Pi trace can be compared.
Prints a per-second timeline: audible share per player, bridge bytes sent per second, stream requests, connection ends.
Run:  scripts/exp_group_member_stall.sh [stop_at_s] [stop_for_s] [total_s]
"""
from __future__ import annotations

import json
import re
import sys
import time

from btkit import analyze as A
from btkit.lab import Lab, make_bridge, save, wait_for
from btkit.lms_rpc import PLAYERS


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    t0_wall = float(argv[0])                       # the wrapper's common time base (shared host kernel clock)
    stop_at, stop_for, total = (float(x) for x in argv[1:4])
    names = ["player1", "player2", "player3"]
    lab = Lab(); lab.reset()
    for m in names[1:]:
        lab.lms.req(PLAYERS[names[0]], "sync", PLAYERS[m])
    rt, sim = make_bridge(player="player1", trace=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: all(lab.mode(n) == "play" for n in names), 10)
        t_play = time.time()
        print(f"EXP playing at +{t_play - t0_wall:.1f}s (stop at +{stop_at}s for {stop_for}s)", flush=True)
        while time.time() < t0_wall + total:
            time.sleep(0.5)
        t_end = time.time()
        modes = {n: lab.mode(n) for n in names}
        sim.pause(); time.sleep(1)
    finally:
        rt.stop()
        for m in names[1:]:
            lab.lms.req(PLAYERS[m], "sync", "-")
    lines = [m for m in rt.logs if "[trace]" in m or '" 20' in m]
    caps = {n: lab.capture(n, since=t_play, until=t_end) for n in names}
    timeline = []
    for k in range(int(t_play - t0_wall), int(t_end - t0_wall)):
        a, b = t0_wall + k, t0_wall + k + 1
        timeline.append({"s": k, **{n: round(A.audible_fraction(caps[n], a, b), 2) for n in names}})
    events = [m for m in lines if re.search(r" r\d+ (start|end) ", m)]
    out = {"stop_at_s": stop_at, "stop_for_s": stop_for, "modes_at_end": modes, "events": events, "timeline": timeline,
           "trace_sample": [m for m in lines if " in: " in m][int(stop_at):int(stop_at + stop_for + 12)],
           "label": "EXPERIMENT INTEGRATION real LMS 9.1.2 sync group + SIMULATED BT; player3 frozen by SIGSTOP; NOT hardware"}
    save(f"exp_group_member_stall_{int(stop_for)}s", out)
    print(json.dumps({"events": events, "modes_at_end": modes}, indent=1))
    for row in timeline:
        print(f"+{row['s']:4d}s  " + "  ".join(f"{n}={row[n]:.2f}" for n in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
