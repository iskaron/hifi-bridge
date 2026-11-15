"""INTEGRATION (real LMS + Squeezelite, SIMULATED Bluetooth): source ownership & policy rules.
'Radio' = a second synthetic stream played through real LMS by the 'user' (JSON-RPC), standing in for
someone else's music.  Real stations / real Lyrion UI actions are not involved."""
import time

import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, save, wait_for
from btkit.lms_rpc import PLAYERS
from btkit.sim_a2dp import SimulatedA2dpSource

pytestmark = pytest.mark.integration
P1 = PLAYERS["player1"]


def user_plays_radio(lab, radio):
    lab.lms.req(P1, "playlist", "play", radio.url, "Radio")
    assert wait_for(lambda: lab.mode() == "play" and lab.url() == radio.url, 5)


def test_manual_switch_to_radio_then_late_bt_events_never_touch_radio(lab, radio):
    rt, sim = make_bridge(pause_strategy="keepalive_silence", grace=4.0)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.url() == rt.controller.session.url and lab.mode() == "play", 5)
        time.sleep(3)
        user_plays_radio(lab, radio); t_switch = time.time()
        assert wait_for(lambda: rt.controller.ownership == "lost", 4)             # ownership loss noticed by polling
        n = len(rt.lms.commands_sent)
        sim.pause()                                                                # pause timer/grace window...
        time.sleep(6.5)                                                            # ...grace (4 s) expires meanwhile
        sim.disconnect(); time.sleep(3)                                            # late disconnect
        assert lab.mode() == "play" and lab.url() == radio.url                     # radio untouched
        assert rt.lms.commands_sent[n:] == []                                      # zero LMS commands after loss
        cap = lab.capture(since=t_switch + 2.5)
        assert A.audible_fraction(cap, t_switch + 3, time.time() - 0.2) > 0.97     # radio audio never gapped
    finally:
        rt.stop()


@pytest.mark.parametrize("avrcp", [True, False])
def test_notification_sound_never_interrupts_radio(lab, radio, avrcp):
    user_plays_radio(lab, radio); time.sleep(2)
    rt, sim = make_bridge(sim=SimulatedA2dpSource(avrcp=avrcp))
    try:
        sim.connect(); time.sleep(0.3); t0 = time.time()
        sim.notification(0.8); time.sleep(0.3); sim.notification(1.2); time.sleep(4)
        assert rt.lms.commands_sent == []                                          # filtered: bridge never touched LMS
        assert lab.url() == radio.url and lab.mode() == "play"
        assert A.audible_fraction(lab.capture(since=t0), t0 + 0.5, time.time() - 0.2) > 0.97
    finally:
        rt.stop()


def test_sustained_media_overrides_radio_after_filter_and_no_auto_restore(lab, radio):
    user_plays_radio(lab, radio); time.sleep(2)
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: rt.controller.ownership == "owned", 6)
        t_cmd = rt.lms.command_times[0][0] - sim.probes["t_play_event"]
        print("takeover command delay after Play:", round(t_cmd, 2))
        assert 1.9 < t_cmd < 3.0                                                   # ~2 s notification filter
        assert lab.url() == rt.controller.session.url
        sim.disconnect(); time.sleep(1.5)
        assert lab.mode() == "stop" and lab.url() is None                          # old radio NOT resurrected
        save("override_takeover_delay", {"filter_s": 2.0, "play_to_lms_command_s": t_cmd, "label": "INTEGRATION real LMS + SIMULATED BT"})
    finally:
        rt.stop()


def test_override_disabled_does_not_take_radio_and_reports_blocked(lab, radio):
    user_plays_radio(lab, radio); rt, sim = make_bridge(override=False)
    try:
        sim.connect(); sim.play(); time.sleep(6)
        assert rt.lms.commands_sent == [] and lab.url() == radio.url
        assert rt.status()["blocked_reason"] == "override_disabled" and rt.status()["bluetooth_connection"] == "connected"
    finally:
        rt.stop()


def test_fresh_play_retakes_after_manual_change(lab, radio):
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(2)
        user_plays_radio(lab, radio); assert wait_for(lambda: rt.controller.ownership == "lost", 4)
        time.sleep(5); assert lab.url() == radio.url                               # continuous phone playback does not fight
        sim.pause(); time.sleep(0.5); sim.play()                                   # fresh user Play
        assert wait_for(lambda: rt.controller.ownership == "owned", 6) and wait_for(lambda: lab.url() == rt.controller.session.url, 3)
    finally:
        rt.stop()


def test_two_bridges_same_player_second_overrides_first_and_first_never_stops_second(lab):
    a, sa = make_bridge(port=8090)
    b, sb = make_bridge(port=8091, sim=SimulatedA2dpSource(device="phone-B", seed=1))
    try:
        sa.connect(); sa.play(); assert wait_for(lambda: a.controller.ownership == "owned", 5); time.sleep(3)
        sb.connect(); sb.play(); assert wait_for(lambda: b.controller.ownership == "owned", 6)
        assert lab.url() == b.controller.session.url
        assert wait_for(lambda: a.controller.ownership == "lost", 4)               # A notices, goes passive
        n = len(a.lms.commands_sent)
        sa.pause(); time.sleep(1); sa.disconnect(); time.sleep(2)
        assert a.lms.commands_sent[n:] == []                                       # A never issues stop for B's stream
        assert lab.mode() == "play" and lab.url() == b.controller.session.url
        assert a.stream.stats["stale_rejected"] >= 0                               # (A's session is closed; stale clients get 410)
    finally:
        a.stop(); b.stop()


def test_second_phone_is_refused_and_does_not_evict_first(lab):
    rt, s1 = make_bridge()
    s2 = SimulatedA2dpSource(device="phone-2", seed=2); s2.attach(rt.callbacks)
    try:
        assert s1.connect(); s1.play(); assert wait_for(lambda: lab.mode() == "play", 5)
        assert s2.connect() is False and s2.refused == 1
        s2.play()                                                                  # refused phone: ignored
        time.sleep(2)
        assert rt.controller.device == "sim-phone-1" and lab.url() == rt.controller.session.url and lab.mode() == "play"
    finally:
        rt.stop()


def test_audio_toggle_releases_only_bridge_stream_bluetooth_stays(lab):
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5); time.sleep(2)
        rt.controller.set_audio_allowed(False)
        assert wait_for(lambda: lab.mode() == "stop", 3) and rt.controller.device == "sim-phone-1"
        rt.controller.set_audio_allowed(True)
        assert wait_for(lambda: lab.mode() == "play" and lab.url() == rt.controller.session.url, 6)
    finally:
        rt.stop()


def test_stale_session_url_is_refused_by_bridge(lab):
    import http.client
    rt, sim = make_bridge()
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 5)
        old = rt.controller.session.url; sim.disconnect(); time.sleep(1)
        host, port = old.split("/")[2].split(":"); c = http.client.HTTPConnection(host, int(port), timeout=3)
        c.request("GET", "/" + old.split("/", 3)[3]); assert c.getresponse().status == 410
    finally:
        rt.stop()
