"""UNIT tests (fake clock, fake LMS) for playback state + ownership rules.
Label: UNIT/SIMULATED - these prove the decision logic, not real LMS behaviour
(that is covered by tests/integration against a real LMS)."""
from fakes import PLAYER, Rig

RADIO = "http://radio.example/stream"


def test_connect_alone_never_starts_lms():
    r = Rig(); r.ctl.on_connected("phone"); r.run(5)
    assert r.lms.commands == [] and r.ctl.status()["bluetooth_connection"] == "connected"


def test_play_on_idle_target_starts_immediately_without_filter():
    r = Rig(); r.connect_and_play()
    assert len(r.cmds("play")) == 1 and r.ctl.ownership == "owned"       # no 2 s wait on idle target


def test_standby_player_is_woken():
    r = Rig(); r.lms.players[PLAYER]["power"] = False; r.connect_and_play()
    assert [c[0] for c in r.lms.commands] == ["power_on", "play"]


def test_notification_filter_when_radio_playing():
    r = Rig(); r.lms.user_plays(PLAYER, RADIO)
    r.ctl.on_connected("phone"); r.ctl.on_media(True); r.run(1.0); r.ctl.on_media(False); r.run(5)
    assert r.lms.commands == [] and r.lms.players[PLAYER]["url"] == RADIO     # beep never touched radio


def test_sustained_media_takes_over_radio_after_2s():
    r = Rig(); r.lms.user_plays(PLAYER, RADIO); r.connect_and_play()
    r.run(1.5); assert not r.cmds("play")
    r.run(1.0); assert len(r.cmds("play")) == 1 and r.lms.players[PLAYER]["url"] == r.url


def test_override_disabled_never_takes_radio():
    r = Rig(override_lms=False); r.lms.user_plays(PLAYER, RADIO); r.connect_and_play(); r.run(10)
    assert r.lms.commands == [] and r.ctl.status()["blocked_reason"] == "override_disabled"


def test_no_auto_resume_of_old_radio_after_release():
    r = Rig(); r.lms.user_plays(PLAYER, RADIO); r.connect_and_play(); r.run(3)
    r.ctl.on_disconnected("phone")
    assert r.lms.players[PLAYER]["mode"] == "stop"                            # radio NOT resurrected
    assert all(c[0] != "play" or c[2] != RADIO for c in r.lms.commands)


def test_pause_drop_restart_silences_immediately_and_grace_expiry_releases_only_ours():
    r = Rig(); r.connect_and_play(); u = r.url
    r.run(2); r.ctl.on_media(False)
    assert ("release", PLAYER, u) in r.lms.commands and r.lms.players[PLAYER]["mode"] == "stop"
    n = len(r.lms.commands); r.run(31)
    assert len(r.lms.commands) == n and r.ctl.ownership == "none"          # nothing more at expiry


def test_pause_keepalive_holds_stream_for_30s_then_releases():
    r = Rig(pause_strategy="keepalive_silence"); r.connect_and_play(); u = r.url
    r.run(2); r.ctl.on_media(False); r.run(29)
    assert r.lms.players[PLAYER]["url"] == u and not r.cmds("release")
    r.run(2); assert r.cmds("release") == [("release", PLAYER, u)] and r.lms.players[PLAYER]["mode"] == "stop"


def test_resume_within_grace_keepalive_same_session_no_commands():
    r = Rig(pause_strategy="keepalive_silence"); r.connect_and_play(); sid = r.ctl.session.sid
    r.run(2); r.ctl.on_media(False); r.run(10); r.ctl.on_media(True); r.run(2)
    assert r.ctl.session.sid == sid and len(r.lms.commands) == 1


def test_resume_within_grace_drop_restart_uses_fresh_session_id():
    r = Rig(); r.connect_and_play(); sid1 = r.ctl.session.sid
    r.run(2); r.ctl.on_media(False); r.run(10); r.ctl.on_media(True)
    assert r.ctl.session.sid != sid1 and r.ctl.ownership == "owned" and len(r.cmds("play")) == 2


def test_resume_after_grace_is_a_fresh_play():
    r = Rig(); r.connect_and_play(); r.run(2); r.ctl.on_media(False); r.run(31); r.ctl.on_media(True)
    assert r.ctl.ownership == "owned" and len(r.cmds("play")) == 2


def test_disconnect_releases_immediately_no_grace():
    r = Rig(); r.connect_and_play(); u = r.url; r.run(2); r.ctl.on_disconnected("phone")
    assert ("release", PLAYER, u) in r.lms.commands and r.ctl.session is None


def test_manual_source_change_then_all_late_events_are_silent():
    """Acceptance: switch from bridge to radio, then BT timeout/disconnect/pause/toggle - never touch radio."""
    r = Rig(pause_strategy="keepalive_silence"); r.connect_and_play(); r.run(2)
    n_before = len(r.lms.commands)
    r.lms.user_plays(PLAYER, RADIO); r.run(3)                                  # poll notices
    assert r.ctl.ownership == "lost" and r.ctl.session is None
    r.ctl.on_media(False); r.run(40)                                           # pause + grace timer window
    r.ctl.set_audio_allowed(False); r.ctl.set_audio_allowed(True)
    r.ctl.on_disconnected("phone"); r.run(5)
    assert r.lms.commands[n_before:] == [] and r.lms.players[PLAYER]["url"] == RADIO


def test_race_grace_timer_fires_before_poll_notices_foreign_source():
    """Radio starts, and the ownership poll has not run yet when the release path executes:
    release is URL-addressed so the radio must survive."""
    r = Rig(pause_strategy="keepalive_silence", poll_interval_s=1000); r.connect_and_play(); r.run(2)
    r.ctl.on_media(False); r.lms.user_plays(PLAYER, RADIO); r.run(31)           # timer expires, poll never ran
    assert r.lms.players[PLAYER]["url"] == RADIO and r.lms.players[PLAYER]["mode"] == "play"


def test_race_disconnect_before_poll_notices_foreign_source():
    r = Rig(poll_interval_s=1000); r.connect_and_play(); r.run(2)
    r.lms.user_plays(PLAYER, RADIO); r.ctl.on_disconnected("phone")
    assert r.lms.players[PLAYER]["url"] == RADIO


def test_fresh_play_retakes_after_manual_change_but_continuous_play_does_not_fight():
    r = Rig(); r.connect_and_play(); r.run(2)
    r.lms.user_plays(PLAYER, RADIO); r.run(3); assert r.ctl.ownership == "lost"
    r.run(60); assert r.lms.players[PLAYER]["url"] == RADIO                    # phone still playing: no fight
    r.ctl.on_media(False); r.ctl.on_media(True); r.run(2.5)                    # fresh Play + filter
    assert r.ctl.ownership == "owned" and r.lms.players[PLAYER]["url"] == r.url


def test_user_stop_at_lms_is_not_resurrected():
    r = Rig(); r.connect_and_play(); r.run(2); r.lms.user_stops(PLAYER); r.run(5)
    assert r.ctl.ownership == "lost" and len(r.cmds("play")) == 1


def test_lms_outage_bt_stays_connected_then_resumes_live_on_return():
    r = Rig(); r.lms.down = True; r.connect_and_play()
    assert r.ctl.status()["lyrion_reachability"] == "unreachable" and r.ctl.device == "phone"
    r.run(10); r.lms.down = False; r.run(4)
    assert r.ctl.ownership == "owned" and len(r.cmds("play")) == 1


def test_lms_restart_midstream_recovers_with_new_session():
    r = Rig(); r.connect_and_play(); sid1 = r.ctl.session.sid; r.run(3)
    r.lms.down = True; r.run(5); r.lms.restart_server(); r.lms.down = False; r.run(5)
    assert r.ctl.ownership == "owned" and r.ctl.session.sid != sid1 and r.lms.players[PLAYER]["url"] == r.url


def test_lms_restart_to_foreign_source_is_not_fought():
    r = Rig(); r.connect_and_play(); r.run(3)
    r.lms.down = True; r.run(5); r.lms.user_plays(PLAYER, RADIO); r.lms.down = False; r.run(5)
    assert r.ctl.ownership == "lost" and r.lms.players[PLAYER]["url"] == RADIO


def test_release_during_outage_is_retried_by_url_when_lms_returns():
    r = Rig(); r.connect_and_play(); u = r.url; r.run(3)
    r.lms.down = True; r.ctl.on_disconnected("phone"); r.lms.down = False
    r.run(3)
    assert ("release", PLAYER, u) in r.lms.commands and r.lms.players[PLAYER]["mode"] == "stop"


def test_selected_player_unreachable_never_moves_to_another_room():
    r = Rig(); r.lms.add("02:00:00:00:00:02"); r.lms.players[PLAYER]["connected"] = False
    r.connect_and_play(); r.run(10)
    assert r.lms.commands == [] and r.ctl.status()["lyrion_reachability"] == "unreachable"


def test_second_source_does_not_evict_first():
    r = Rig(); assert r.ctl.on_connected("phone-A"); r.ctl.on_media(True)
    assert r.ctl.on_connected("phone-B") is False and r.ctl.device == "phone-A"
    r.ctl.on_disconnected("phone-B")                                            # B never owned anything
    assert r.ctl.ownership == "owned"


def test_audio_toggle_releases_only_bridge_stream_and_bt_stays():
    r = Rig(); r.connect_and_play(); u = r.url; r.ctl.set_audio_allowed(False)
    assert ("release", PLAYER, u) in r.lms.commands and r.ctl.device == "phone" and r.ctl.session is None
    r.ctl.set_audio_allowed(True); assert r.ctl.ownership == "owned"            # phone still playing -> restarts


def test_audio_toggle_with_foreign_source_sends_nothing_harmful():
    r = Rig(); r.connect_and_play(); r.run(2); r.lms.user_plays(PLAYER, RADIO); r.run(3)
    n = len(r.lms.commands); r.ctl.set_audio_allowed(False); assert r.lms.commands[n:] == []


def test_target_change_releases_old_then_continues_on_new():
    r = Rig(); r.lms.add("02:00:00:00:00:02"); r.connect_and_play(); u = r.url
    r.ctl.set_target("02:00:00:00:00:02")
    assert ("release", PLAYER, u) in r.lms.commands
    assert r.lms.players["02:00:00:00:00:02"]["url"] == r.url and r.lms.players[PLAYER]["mode"] == "stop"


def test_two_bridges_same_player_second_overrides_first_which_then_stays_silent():
    a, b = Rig(), Rig()
    b.lms = a.lms; b.ctl.lms = a.lms                                            # same LMS/player
    a.connect_and_play(); a.run(2)
    b.connect_and_play(); b.run(2.5)                                            # B takes over after filter
    assert a.lms.players[PLAYER]["url"] == b.url
    n = len(a.lms.commands); a.run(3); assert a.ctl.ownership == "lost"
    a.ctl.on_disconnected("phone"); a.run(35)
    assert [c for c in a.lms.commands[n:] if c[0] != "play"] == []              # A never stops B's stream
    assert a.lms.players[PLAYER]["url"] == b.url


def test_volume_only_while_owning_and_never_jumps():
    r = Rig(); r.ctl.on_connected("phone"); r.ctl.on_volume(100)               # reference only
    r.ctl.on_media(True); r.ctl.on_volume(127)                                  # +27*100/127=21 -> capped 10
    d = r.cmds("volume_delta"); assert d and d[-1][2] == 10
    r.lms.user_plays(PLAYER, RADIO); r.run(3); n = len(r.lms.commands)
    r.ctl.on_volume(10); assert r.lms.commands[n:] == []                        # lost ownership: radio volume untouched


def test_status_dimensions_are_separate():
    r = Rig(); r.ctl.on_connected("phone"); s = r.ctl.status()
    assert s["bluetooth_connection"] == "connected" and s["bt_media_activity"] == "idle" and s["stream_transport"] == "closed"
    r.ctl.on_media(True); s = r.ctl.status()
    assert s["bt_media_activity"] == "playing" and s["target_ownership"] == "owned" and s["stream_transport"] == "streaming"


def test_only_allowed_lms_commands_are_ever_issued():
    r = Rig(); r.connect_and_play(); r.run(3); r.ctl.on_media(False); r.run(40); r.ctl.on_media(True); r.run(3); r.ctl.on_disconnected("phone")
    assert {c[0] for c in r.lms.commands} <= {"play", "release", "power_on", "volume_delta"}


def test_diagnostic_refused_when_target_plays_anything_and_sends_nothing():
    r = Rig(); r.lms.user_plays(PLAYER, RADIO)
    ok, reason, sid = r.ctl.begin_diagnostic()
    assert (ok, reason, sid) == (False, "target_busy", None) and r.lms.commands == []


def test_diagnostic_on_idle_target_starts_and_releases_only_its_own_session():
    r = Rig(); ok, _, sid = r.ctl.begin_diagnostic(); assert ok and r.lms.players[PLAYER]["url"] == r.url
    r.lms.user_plays(PLAYER, RADIO); r.run(3)                                  # user switches during the test
    n = len(r.lms.commands); r.ctl.end_diagnostic(sid)
    assert r.lms.commands[n:] == [] and r.lms.players[PLAYER]["url"] == RADIO   # test end never stops the radio
