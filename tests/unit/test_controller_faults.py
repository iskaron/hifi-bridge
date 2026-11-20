"""UNIT (fake clock, fake LMS): the controller's less usual paths - Lyrion going away in the middle of a volume change, an owner action that
changes nothing, the owner's test sound while something is already playing, our stream dying under us, a long outage. The invariants:
the bridge never touches what it does not own, never releases twice, and always ends in a state the next poll can recover from.
Label: UNIT/SIMULATED (decision logic only; real LMS behaviour is tests/integration). Coverage package 7b-3."""
import pytest
from fakes import PLAYER, Rig

from btbridge.lms.client import LmsUnreachable


def test_lyrion_vanishing_during_a_volume_change_marks_it_unreachable_and_changes_nothing_else():
    r = Rig(); r.connect_and_play(); r.run(1)
    before = list(r.lms.commands)
    r.lms.down = True
    r.ctl.on_volume(80)                                                    # the phone's slider moves while Lyrion is down
    assert r.ctl.reachability == "unreachable" and r.lms.commands == before


def test_a_volume_change_that_arrives_after_the_server_was_switched_is_ignored():
    r = Rig(); r.connect_and_play(); r.run(1)
    other = type(r.lms)(); other.add(PLAYER)
    real_observe = r.lms.observe

    def switch_then_fail(pid):
        r.ctl.change_server(other)                                         # the owner switches server while we are asking the old one
        raise LmsUnreachable("old server went away")
    r.lms.observe = switch_then_fail
    r.ctl.on_volume(75)
    r.lms.observe = real_observe
    assert r.ctl.reachability != "unreachable"                             # the failure belongs to the OLD server: not held against the new


def test_choosing_the_target_that_is_already_chosen_does_nothing():
    r = Rig(); r.connect_and_play(); r.run(1)
    before, url = list(r.lms.commands), r.url
    r.ctl.set_target(PLAYER)
    assert r.lms.commands == before and r.url == url and r.ctl.ownership == "owned"


def test_a_setting_change_that_changes_nothing_leaves_the_running_session_alone():
    r = Rig(); r.connect_and_play(); r.run(1)
    before, url = list(r.lms.commands), r.url
    r.ctl.restart_session("format_change", lambda: False)                  # e.g. "FLAC" chosen while FLAC is already in use
    assert r.lms.commands == before and r.url == url
    r.ctl.restart_session("format_change", lambda: True)                   # a real change replaces the session
    assert r.url != url and ("release", PLAYER, url) in r.lms.commands


def test_the_test_sound_is_refused_while_the_bridge_already_plays_and_does_not_touch_lyrion():
    r = Rig(); r.connect_and_play(); r.run(1)
    before = list(r.lms.commands)
    ok, reason, sid = r.ctl.begin_diagnostic()
    assert (ok, reason, sid) == (False, "bridge_session_active", None) and r.lms.commands == before


def test_the_test_sound_is_refused_when_lyrion_cannot_be_asked_and_releases_the_claim():
    r = Rig(); r.lms.down = True
    ok, reason, _ = r.ctl.begin_diagnostic()
    assert not ok and reason in ("lyrion_unreachable", "target_unavailable")
    r.lms.down = False
    ok, reason, sid = r.ctl.begin_diagnostic()                              # the failed attempt left nothing blocking
    assert ok and sid


def test_our_stream_dying_under_us_ends_the_session_locally_and_a_new_one_starts_after_the_backoff():
    r = Rig(); r.connect_and_play(); r.run(1)
    first = r.url
    r.lms.players[PLAYER].update(mode="stop")                              # Squeezelite crashed; the URL is still the current item
    r.run(6)                                                                # past the settle time and one poll
    events = [e for e in r.ctl.events if "stream_died" in str(e)]
    assert events, r.ctl.events[-6:]
    r.run(30)                                                               # backoff elapsed: the phone is still playing -> start again
    assert len(r.cmds("play")) >= 2 and r.cmds("play")[-1][2] != first and r.ctl.ownership == "owned"


def test_a_long_outage_remembers_each_of_our_urls_once_and_cleans_up_when_lyrion_is_back():
    r = Rig(); r.connect_and_play(); r.run(1)
    url = r.url
    r.lms.down = True
    r.ctl.on_disconnected("phone")                                          # release fails: the URL is remembered for later
    r.run(2)
    assert r.ctl.stale_urls == [(PLAYER, url)]
    r.ctl._defer_release(PLAYER, url)                                       # asking again never duplicates it
    assert r.ctl.stale_urls == [(PLAYER, url)]
    r.run(3)                                                                # still down: the retry gives up quietly
    assert r.ctl.stale_urls == [(PLAYER, url)]
    r.lms.down = False
    r.run(10)
    assert ("release", PLAYER, url) in r.lms.commands and r.ctl.stale_urls == []


@pytest.mark.parametrize("allowed", [False, True])
def test_blocking_audio_while_nothing_plays_is_harmless_and_unblocking_does_not_start_without_a_phone(allowed):
    r = Rig()
    r.ctl.set_audio_allowed(allowed)
    r.run(3)
    assert r.lms.commands == []
