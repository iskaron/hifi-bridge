"""INTEGRATION (real Lyrion + Squeezelite, SIMULATED Bluetooth/AVRCP): live MP3 320 kbit/s CBR from the bridge, and the artist/title
that reaches Lyrion's now-playing model through ICY (DECISIONS #147).

Asserted against what **Lyrion reports** (``status``: ``current_title``, the playlist item's title/artist/bitrate), not against what
the bridge sends. What this does NOT show: Squeezebox Classic, Squeezelite-ESP32 and Nest displays (hardware/other integrations;
UNVERIFIED), a real phone's AVRCP behaviour, or any benefit for stalls (a bitrate A/B on the real Pi decides that)."""
import shutil
import time

import numpy as np
import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, save, wait_for
from btkit.lms_rpc import PLAYERS

pytestmark = [pytest.mark.integration, pytest.mark.skipif(shutil.which("lame") is None, reason="needs lame (lab image)")]
P = PLAYERS
RATE = 44100
TAGS = "tags:uatlrbBcdgKkNxyY"


def item(lab, name="player1"):
    r = lab.lms.req(P[name], "status", "-", 1, TAGS)
    return r, (r.get("playlist_loop") or [{}])[0]


def make_group(lab, members):
    for m in members[1:]:
        lab.lms.req(P[members[0]], "sync", P[m])
    assert wait_for(lambda: len((lab.lms.req("", "syncgroups", "?").get("syncgroups_loop") or [{}])[0].get("sync_members", "").split(",")) == len(members), 5)


def peak_hz(x: np.ndarray) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec[:3000]) * RATE / len(x))             # the tones are below 1 kHz; the noise floor is broadband


def assert_tones(cap):
    """The decoded MP3 still carries the generator's tones: 440 Hz left, 660 Hz right (lossy, so no sample comparison)."""
    pcm = cap.pcm.astype(float)
    assert len(pcm) > RATE * 5, "the player decoded and played the MP3"
    seg = pcm[RATE * 3:RATE * 3 + 32768]
    assert abs(peak_hz(seg[:, 0]) - 440) < 6 and abs(peak_hz(seg[:, 1]) - 660) < 6


def test_mp3_320_plays_in_real_lyrion_and_squeezelite_and_lyrion_sees_a_320k_cbr_stream(lab):
    rt, sim = make_bridge(fmt="mp3")
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.mode() == "play", 15), lab.status()
        start = time.time(); b0 = rt.stream.stats["bytes_sent"]
        time.sleep(12)
        rate_kbps = (rt.stream.stats["bytes_sent"] - b0) * 8 / (time.time() - start) / 1000
        st, it = item(lab)
        t_end = time.time(); sim.disconnect(); time.sleep(1)
        cap = lab.capture(until=t_end)
        assert it["url"].endswith(".mp3") and "320" in str(it.get("bitrate")) and "CBR" in str(it.get("bitrate")), it     # Lyrion's own view
        assert not it.get("duration") or float(it["duration"]) == 0                                # a live stream: no invented length
        assert 290 < rate_kbps < 345, rate_kbps                                                    # bytes on the wire: ~320 kbit/s, not 1411
        assert A.audible_fraction(cap, t_end - 7, t_end - 0.3) > 0.97               # after the start-up (MP3 starts later than WAV: see the report)
        assert_tones(cap)
        assert rt.stream.stats["encoder_died"] == 0 and rt.stream.stats["encoder_refused"] == 0
        save("mp3_320_lms", {"lms_bitrate": it.get("bitrate"), "wire_kbit_per_s": round(rate_kbps, 1), "connections": rt.stream.stats["connections"],
                             "audible_fraction": round(A.audible_fraction(cap, t_end - 7, t_end - 0.3), 3),
                             "first_audio_after_play_s": round((A.edges(cap)[0] or 0) - sim.probes["t_play_event"], 2),
                             "label": "INTEGRATION real LMS + Squeezelite decode + SIMULATED BT; virtual DAC, NOT acoustic, NOT Pi 3 CPU"})
    finally:
        rt.stop()


def test_without_the_icy_switch_lyrion_keeps_the_generic_title_even_when_the_phone_reports_a_track(lab):
    rt, sim = make_bridge(fmt="mp3", icy=True, icy_mp3=False)               # the WAV switch alone must not enable MP3 metadata
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.mode() == "play", 15)
        sim.set_metadata("Blue in Green", "Miles Davis", "Kind of Blue")
        time.sleep(6)
        st, it = item(lab)
        assert st["current_title"] == "Hi-Fi Bridge" and it.get("title") == "Hi-Fi Bridge" and not it.get("artist"), (st["current_title"], it)
        assert rt.stream.title == "Miles Davis - Blue in Green"             # the bridge knows it (WebUI), the stream just does not carry it
    finally:
        sim.disconnect(); rt.stop()


def test_icy_mp3_titles_reach_lyrions_now_playing_and_change_midstream_without_an_interruption(lab):
    rt, sim = make_bridge(fmt="mp3", icy_mp3=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.mode() == "play", 15)
        time.sleep(3)
        assert item(lab)[0]["current_title"] == "Hi-Fi Bridge"                # before any metadata: the generic station name
        marks = {n: lab.log_size(n) for n in ("player1",)}
        t_first = time.time()
        sim.set_metadata("Blue in Green", "Miles Davis", "Kind of Blue")
        assert wait_for(lambda: item(lab)[0].get("current_title") == "Miles Davis - Blue in Green", 8), item(lab)[0].get("current_title")
        lag1 = time.time() - t_first
        st, it = item(lab)
        assert it.get("title") == "Blue in Green" and it.get("artist") == "Miles Davis"      # Lyrion split it: what Jive/Classic/ESP32 UIs read
        time.sleep(3)
        t_second = time.time()
        sim.set_metadata("So What", "Miles Davis", "Kind of Blue")             # track change in the middle of the stream
        assert wait_for(lambda: item(lab)[0].get("current_title") == "Miles Davis - So What", 8)
        lag2 = time.time() - t_second
        time.sleep(4)
        t_end = time.time()
        assert lab.mode() == "play" and lab.url().endswith(".mp3")
        assert rt.controller.session is not None and [c[0] for c in rt.lms.commands_sent].count("play") == 1     # same session: no restart for a title
        cap = lab.capture(until=t_end)
        first_audio = A.edges(cap)[0]
        assert A.audible_fraction(cap, max(first_audio + 0.5, t_first - 1), t_end - 0.3) > 0.99         # no gap around either change
        assert lab.underruns("player1", marks["player1"]) == 0
        save("mp3_icy_titles", {"seconds_to_lyrion_first_title": round(lag1, 2), "seconds_to_lyrion_track_change": round(lag2, 2),
                                "lyrion_item": {k: it.get(k) for k in ("title", "artist", "bitrate", "remote_title")},
                                "label": "INTEGRATION real LMS + SIMULATED AVRCP; Squeezebox Classic / ESP32 / Nest displays UNVERIFIED"})
    finally:
        sim.disconnect(); rt.stop()


def test_no_stale_title_after_the_phone_leaves_or_reports_nothing_and_a_new_session_starts_generic(lab):
    rt, sim = make_bridge(fmt="mp3", icy_mp3=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.mode() == "play", 15)
        sim.set_metadata("Old Song", "Old Artist", "")
        assert wait_for(lambda: item(lab)[0].get("current_title") == "Old Artist - Old Song", 8)
        sim.set_metadata("", "", "")                                           # AVRCP: metadata unavailable
        assert wait_for(lambda: item(lab)[0].get("current_title") in ("Hi-Fi Bridge", "Bluetooth"), 10), item(lab)[0].get("current_title")   # cleared: Lyrion falls back to the play title
        sim.set_metadata("Another", "Someone", "")
        assert wait_for(lambda: item(lab)[0].get("current_title") == "Someone - Another", 8)
        sim.disconnect(); time.sleep(2)
        assert rt.stream.title == "" and lab.url() is None
        sim.connect(); sim.play()                                              # a later phone/session must not inherit "Someone - Another"
        assert wait_for(lambda: lab.mode() == "play", 15)
        time.sleep(3)
        assert item(lab)[0]["current_title"] == "Hi-Fi Bridge", item(lab)[0]["current_title"]
    finally:
        sim.disconnect(); rt.stop()


def test_icy_mp3_in_a_sync_group_shows_the_title_for_every_member_and_plays_in_sync(lab):
    names = ["player1", "player2", "player3"]
    make_group(lab, names)
    rt, sim = make_bridge(fmt="mp3", icy_mp3=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: all(lab.mode(n) == "play" for n in names), 15), [lab.mode(n) for n in names]
        sim.set_metadata("Blue in Green", "Miles Davis", "Kind of Blue")
        assert wait_for(lambda: all(item(lab, n)[0].get("current_title") == "Miles Davis - Blue in Green" for n in names), 10), \
            [item(lab, n)[0].get("current_title") for n in names]
        time.sleep(10)
        t_end = time.time()
        marks = {n: lab.log_size(n) for n in names}
        caps = {n: lab.capture(n, until=t_end) for n in names}
        for n in names:
            assert A.audible_fraction(caps[n], t_end - 6, t_end - 0.3) > 0.97, n
        assert {c[0] for c in rt.lms.commands_sent} <= {"play", "release", "power_on", "volume_delta"}     # nothing but our own session
        assert lab.lms.req("", "syncgroups", "?")["syncgroups_loop"][0]["sync_members"].count(",") == 2        # the group is untouched
    finally:
        sim.disconnect(); rt.stop()


def test_a_foreign_source_is_never_displaced_by_the_mp3_bridge_and_a_manual_change_ends_our_stream_quietly(lab, radio):
    lab.lms.req(P["player1"], "playlist", "play", radio.url, "Radio")
    assert wait_for(lambda: lab.mode() == "play" and lab.url() == radio.url, 6)
    rt, sim = make_bridge(fmt="mp3", icy_mp3=True, override=False)
    try:
        sim.connect(); sim.play(); time.sleep(5)
        assert rt.lms.commands_sent == [] and lab.url() == radio.url           # override off: not one command, the radio plays on
    finally:
        sim.disconnect(); rt.stop()
    rt, sim = make_bridge(fmt="mp3", icy_mp3=True)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: lab.url() == (rt.controller.session.url if rt.controller.session else None) and lab.mode() == "play", 20)
        time.sleep(3)
        lab.lms.req(P["player1"], "playlist", "play", radio.url, "Radio")      # the owner picks something else by hand
        assert wait_for(lambda: rt.controller.ownership == "lost", 6)
        n = len(rt.lms.commands_sent)
        sim.set_metadata("Late", "Title", "")
        time.sleep(4)
        assert lab.url() == radio.url and lab.mode() == "play" and rt.lms.commands_sent[n:] == []
    finally:
        sim.disconnect(); rt.stop()
