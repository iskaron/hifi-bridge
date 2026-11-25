"""INTEGRATION (REAL LMS 9.1.2 + 3 Squeezelite in a sync group, SIMULATED Bluetooth, trace ON): a held stream connection keeps
the group playing for 2.5 minutes - LMS needs NO new request for continuity, the bridge never stalls, and the opt-in trace
(docs/STREAM_TRACE.md) shows exactly the stream requests LMS made.

Context: on the real Pi (G1, 2026-11-24) the music went silent for 10-15 s and came back exactly with a new "GET ... 206". This
test is the lab BASELINE for the trace the owner will record on the Pi: in a healthy chain there is no periodic re-request. It does
not reproduce the Pi outage (no Wi-Fi, no real players here). Label: INTEGRATION, virtual DACs, NOT acoustic."""
import re
import time

import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, save, wait_for
from btkit.lms_rpc import PLAYERS

pytestmark = pytest.mark.integration
P = PLAYERS
SECONDS = 150


def make_group(lab, members):
    for m in members[1:]:
        lab.lms.req(P[members[0]], "sync", P[m])
    assert wait_for(lambda: len((lab.lms.req("", "syncgroups", "?").get("syncgroups_loop") or [{}])[0].get("sync_members", "").split(",")) == len(members), 5)


@pytest.mark.slow
def test_a_sync_group_plays_150_s_from_one_held_connection_and_the_trace_shows_it(lab):
    names = ["player1", "player2", "player3"]
    make_group(lab, names)
    rt, sim = make_bridge(player="player1", trace=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: all(lab.mode(n) == "play" for n in names), 10), [lab.mode(n) for n in names]
        t_play = time.time()
        time.sleep(SECONDS)
        t_end = time.time()
        obs = rt.controller.last_observation
        sim.pause(); time.sleep(1)
        lines = [m for m in rt.logs if "[trace]" in m]
        starts = [m for m in lines if re.search(r"\] t=[\d.]+ [\d:]+ r\d+ start GET", m)]
        ends = [m for m in lines if re.search(r"\] t=[\d.]+ [\d:]+ r\d+ end reason=", m)]
        summaries = [m for m in lines if " in: " in m]
        caps = {n: lab.capture(n, until=t_end) for n in names}
        audible = {n: round(A.audible_fraction(caps[n], t_play + 10, t_end - 1), 4) for n in names}
        starts_t = [float(re.search(r"t=([\d.]+)", m).group(1)) for m in starts]
        late = [m for m, t in zip(starts, starts_t) if t - starts_t[0] > 15]          # any request after the start-up phase
        blocked = [m for m in summaries if "STUCK" in m]
        t_of = lambda m: float(re.search(r"t=([\d.]+)", m).group(1))                 # noqa: E731
        waiting = [m for m in summaries if " WAITING" in m and t_of(m) - t_of(summaries[0]) > 10]   # LMS rebuffering after start-up
        members = [m for m in summaries if re.search(r"\| members: (lab-player\d:ok(, )?){3}$", m)]
        result = {"seconds": SECONDS, "audible_fraction_from_10s": audible, "stream_requests": starts, "connection_ends": ends,
                  "late_requests": late, "stuck_summaries": len(blocked), "waiting_summaries": len(waiting),
                  "summaries_with_3_members_ok": len(members), "summary_lines": len(summaries),
                  "ring_skips": rt.stream.stats["ring_skips"], "feeder": rt.status()["feeder"],
                  "lms_observation": None if not obs else {"mode": obs[1].mode, "time": obs[1].time, "duration": obs[1].duration,
                                                           "ours": obs[2]},
                  "example_summary": summaries[len(summaries) // 2] if summaries else None,
                  "label": "INTEGRATION real LMS 9.1.2 sync group (3x Squeezelite) + SIMULATED BT; virtual DACs, NOT acoustic"}
        print("\n" + "\n".join(starts + ends[:5]) + f"\naudible {audible}; lms {result['lms_observation']}")
        save("stream_continuity_sync_group_150s", result)
        assert all(a > 0.99 for a in audible.values()), audible
        assert late == [], late                                                       # no re-request needed for continuity
        assert not blocked and rt.stream.stats["ring_skips"] == 0
        assert not waiting, waiting[:3]                                               # Lyrion never had to rebuffer the group
        assert len(members) >= SECONDS - 15, summaries[-1]                            # every member reported connected, by name
        assert len(summaries) >= SECONDS - 5                                         # ~1 line/s, bounded
        assert all(not re.search(r"/s/|token", m) for m in lines)
    finally:
        rt.stop()
