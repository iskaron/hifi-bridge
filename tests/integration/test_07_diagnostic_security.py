"""INTEGRATION: owner diagnostic refusal, token hygiene.  Real LMS + Squeezelite, SIMULATED Bluetooth."""
import json
import time

import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, wait_for
from btkit.lms_rpc import PLAYERS

pytestmark = pytest.mark.integration
P1 = PLAYERS["player1"]


def test_diagnostic_refuses_when_target_busy_without_interrupting(lab, radio):
    lab.lms.req(P1, "playlist", "play", radio.url, "Radio"); assert wait_for(lambda: lab.url() == radio.url, 5); time.sleep(2)
    rt, sim = make_bridge()
    try:
        t0 = time.time()
        assert rt.diagnostic_test() == "refused:target_busy"
        time.sleep(3)
        assert rt.lms.commands_sent == [] and lab.url() == radio.url
        assert A.audible_fraction(lab.capture(since=t0), t0 + 0.5, time.time() - 0.2) > 0.97
    finally:
        rt.stop()


def test_diagnostic_plays_tone_on_idle_target_then_releases(lab):
    rt, sim = make_bridge()
    try:
        t0 = time.time(); assert rt.diagnostic_test(3.0) == "started"
        assert wait_for(lambda: lab.mode() == "play", 5)
        assert wait_for(lambda: lab.mode() == "stop", 12)
        first, last = A.edges(lab.capture(since=t0))
        assert first is not None and 2.7 < last - first < 3.6            # the whole 3 s tone reached the virtual DAC (not cut short)
    finally:
        rt.stop()


def test_token_never_appears_in_logs_status_or_timeline(lab):
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(3)
        rt.controller.set_audio_allowed(False); rt.controller.set_audio_allowed(True); time.sleep(2); sim.disconnect(); time.sleep(1)
        blob = "\n".join(rt.logs) + json.dumps(rt.status()) + json.dumps(rt.timeline, default=str) + json.dumps([e[1:] for e in rt.stream.events], default=str)
        assert rt.token not in blob and len(rt.logs) > 3
    finally:
        rt.stop()


def test_token_rotation_keeps_current_stream_and_new_sessions_use_new_token(lab):
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(2)
        old = rt.token; rt.stream.rotate_token("rotated-token-xyz"); rt.token = "rotated-token-xyz"
        time.sleep(3); assert lab.mode() == "play"                               # running stream unaffected
        sim.pause(); time.sleep(1); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6)
        assert "rotated-token-xyz" in rt.controller.session.url and old not in rt.controller.session.url
    finally:
        rt.stop()


def test_lms_discovery_by_udp_broadcast_finds_real_lms(lab):
    from btbridge.lms.discovery import discover
    found = discover(timeout=2.5)
    print("DISCOVERED", found)
    assert any(f.get("VERS", "").startswith("9.") and f.get("JSON") == "9000" for f in found), found
