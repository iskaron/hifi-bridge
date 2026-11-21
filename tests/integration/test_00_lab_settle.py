"""INTEGRATION (real LMS + Squeezelite): the lab harness starts every suite from a defined player state (OPEN_QUESTIONS #86).

Lyrion keeps a fade-in that a play request asked for until a stream really starts. A request that cannot stream (in the lab: a
stale queue item from an earlier session, resumed by Lyrion with `playlist jump <i> 1` when a player connects after a Lyrion
restart) leaves it pending, and the next stream - the first test's - starts with a 1 s fade-in. `stop` / `playlist clear` do not
clear it. Here the pending fade-in is created on purpose (unreachable URL on Lyrion's own loopback, no network access) and the
harness must consume it, so the bridge's next stream starts without a transition."""
import time

from btkit.lab import Lab, make_bridge, wait_for
from btkit.lms_rpc import PLAYERS

P1 = PLAYERS["player1"]
UNREACHABLE = "http://127.0.0.1:1/lab-settle-test.wav"   # Lyrion's own loopback, nothing listens: fails before any stream starts


def test_settle_consumes_a_pending_lyrion_fade_in_so_the_next_stream_starts_without_transition(lab):
    lab.lms.req(P1, "playlist", "play", UNREACHABLE, "stale", 1)          # fadeInSecs = 1, as Lyrion's resume-on-connect does
    assert wait_for(lambda: lab.mode() == "stop", 10)
    lab.lms.req(P1, "playlist", "clear"); lab.lms.req(P1, "stop")        # what the old reset did: does not clear the fade-in

    assert lab.settle_players() == 2                                      # 1st start carried the pending fade-in, 2nd is clean

    lab.reset()
    off = Lab.log_size("player1")
    rt, sim = make_bridge()
    try:
        assert sim.connect(); sim.play()
        assert wait_for(lambda: rt.controller.ownership == "owned", 5)
        assert wait_for(lambda: lab.mode() == "play", 5)
        time.sleep(1)
        assert Lab.transitions_since("player1", off) == [0]              # the bridge's stream: no fade-in
    finally:
        sim.disconnect(); rt.stop()
