"""UNIT (fake LMS, simulated phone, real stream server and, where it matters, the real ``lame``): the MP3 option end to end inside the
bridge - settings, WebUI, session restart, the missing-encoder path, ICY switches and what "now playing" shows (DECISIONS #147).
Real Lyrion behaviour is covered by tests/integration/test_13_mp3_icy_lms.py."""
import shutil
import urllib.parse

import pytest
from test_service import configure, svc, wait  # noqa: F401  (svc is a fixture)
from test_webui import PW, finish_wizard, ui  # noqa: F401  (ui is a fixture)

from btbridge.app import RuntimeConfig
from btbridge.config.schema import SETTINGS, SettingError, validate
from btbridge.stream import encoders as E
from btbridge.stream.encoders import STREAM_FORMATS

HAS_LAME = shutil.which("lame") is not None


def plays(lms):
    return [c for c in lms.commands if c[0] == "play"]


def test_defaults_are_unchanged_wav_with_every_icy_switch_off_and_mp3_is_a_valid_choice():
    assert SETTINGS["stream_format"].default == "wav" and SETTINGS["icy_metadata"].default is False and SETTINGS["icy_mp3"].default is False
    rc = RuntimeConfig()
    assert rc.stream_format == "wav" and rc.icy_metadata is False and rc.icy_mp3 is False
    assert STREAM_FORMATS == ("wav", "flac", "mp3") and validate({"stream_format": "mp3"}) == {"stream_format": "mp3"}
    with pytest.raises(SettingError):
        validate({"stream_format": "ogg"})


def test_the_playback_page_offers_mp3_marked_experimental_and_the_separate_icy_switch(ui):
    finish_wizard(ui)
    page = ui.c.get("/settings/playback").body
    assert '<option value="mp3"' in page and "experimental" in page and 'name="icy_mp3"' in page and 'name="icy_metadata"' in page
    assert 'value="wav" selected' in page.replace("<option value=\"wav\" selected>", 'value="wav" selected')      # WAV is still the selected default
    ui.c.post("/settings/appearance", {"language": "de", "theme": "auto"})
    assert "experimentell" in ui.c.get("/settings/playback").body


def test_selecting_mp3_is_refused_with_a_clear_message_when_lame_is_missing_and_nothing_changes(ui, monkeypatch):
    finish_wizard(ui)
    monkeypatch.setattr(E, "mp3_encoder_path", lambda: None)
    ui.c.post("/settings/playback", {"pause_strategy": "drop_restart", "stream_format": "mp3"})
    assert "lame" in urllib.parse.unquote(ui.c.cookies["bab_flash"]) and ui.svc.store.get("stream_format") == "wav"
    assert ui.svc.runtime.stream.fmt == "wav"


@pytest.mark.skipif(not HAS_LAME, reason="needs lame")
def test_mp3_and_its_icy_switch_are_saved_applied_and_independent_of_the_wav_icy_switch(ui):
    finish_wizard(ui)
    ui.c.post("/settings/playback", {"pause_strategy": "drop_restart", "stream_format": "mp3", "icy_mp3": "on"})
    st = ui.svc.runtime.stream
    assert ui.svc.store.get("stream_format") == "mp3" and st.fmt == "mp3" and st.icy_mp3 is True and st.icy is False
    assert ui.svc.store.get("icy_metadata") is False and ui.svc.runtime.rcfg.icy_mp3 is True
    ui.c.post("/settings/playback", {"pause_strategy": "drop_restart", "stream_format": "wav"})                # instant rollback to WAV
    assert st.fmt == "wav" and st.icy is False and st.icy_mp3 is False and ui.svc.store.get("stream_format") == "wav"


@pytest.mark.skipif(not HAS_LAME, reason="needs lame")
def test_switching_the_format_replaces_only_our_running_session_and_the_new_url_is_mp3(svc):
    lms = configure(svc)
    svc.sim.connect(); svc.sim.play()
    assert wait(lambda: plays(lms))
    assert plays(lms)[0][2].endswith(".wav")
    svc.store.update({"stream_format": "mp3"})
    assert wait(lambda: len(plays(lms)) == 2, t=6) and plays(lms)[1][2].endswith(".mp3")
    assert all(c[0] in ("play", "release", "power_on", "volume_delta") for c in lms.commands)        # no stop/pause/clear/sync commands
    svc.store.update({"stream_format": "wav"})
    assert wait(lambda: len(plays(lms)) == 3, t=6) and plays(lms)[2][2].endswith(".wav")


def test_mp3_without_an_encoder_plays_nothing_says_why_and_recovers_when_the_encoder_returns(svc, monkeypatch):
    lms = configure(svc)
    svc.store.update({"stream_format": "mp3"}) if HAS_LAME else svc.store.update({"stream_format": "mp3"})
    monkeypatch.setattr("btbridge.stream.server.mp3_encoder_path", lambda: None)
    svc.sim.connect(); svc.sim.play()
    assert wait(lambda: svc.status()["bridge"]["blocked_reason"] == "stream_encoder_unavailable", t=6)
    assert "stream_encoder_unavailable" in svc.status()["degraded"] and not plays(lms)               # no WAV substitute, no half start
    monkeypatch.setattr("btbridge.stream.server.mp3_encoder_path", lambda: "/usr/bin/lame")
    assert wait(lambda: plays(lms), t=8) and plays(lms)[0][2].endswith(".mp3")
    assert "stream_encoder_unavailable" not in svc.status()["degraded"]


def test_metadata_reaches_the_stream_title_as_artist_dash_title_and_clears_when_the_phone_reports_nothing_or_leaves(svc):
    configure(svc)
    svc.sim.connect()
    svc.sim.set_metadata("Blue in Green", "Miles Davis", "Kind of Blue")
    assert svc.runtime.stream.title == "Miles Davis - Blue in Green" and svc.status()["now_playing"] == "Miles Davis - Blue in Green"
    svc.sim.set_metadata("Next Song", "", "")
    assert svc.runtime.stream.title == "Next Song"                                                     # title only: no dangling dash
    svc.sim.set_metadata("", "", "")
    assert svc.runtime.stream.title == "" and svc.status()["now_playing"] is None                      # unavailable: generic display, not stale
    svc.sim.set_metadata("Again", "Artist", "")
    svc.sim.disconnect()
    assert svc.runtime.stream.title == "" and svc.status()["now_playing"] is None                      # nothing leaks to the next phone
