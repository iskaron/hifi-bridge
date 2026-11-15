"""UNIT tests: ring buffer, feeder, fusion, volume, pairing, stream-server security."""
import http.client
import threading

import pytest

from btbridge.core.clock import FakeClock
from btbridge.core.fusion import MediaFusion
from btbridge.core.pairing import PairingWindow
from btbridge.core.ring import PcmRing
from btbridge.core.volume import VolumePolicy
from btbridge.stream.feeder import LiveFeeder, FRAME_BYTES
from btbridge.stream.server import StreamServer, redact

FB = FRAME_BYTES


# ---- ring --------------------------------------------------------------------
def test_ring_is_bounded_and_skips_stale_reader():
    r = PcmRing(1000)
    for i in range(10):
        r.write(bytes([i]) * (400 * FB))
    assert len(r.buf) == 1000 * FB and r.head == 4000
    pos, data, skipped = r.read(0, 5000, timeout=0)
    assert skipped == 3000 and len(data) == 1000 * FB and data[-FB:] == bytes([9]) * FB   # stale audio dropped, newest kept


def test_ring_wraparound_content_exact():
    r = PcmRing(10); out = b""
    pos = 0
    for i in range(7):
        r.write(bytes([i]) * (4 * FB)); pos, d, s = r.read(pos, 100, 0); out += d
    assert out == b"".join(bytes([i]) * (4 * FB) for i in range(7))


def test_ring_partial_frames_are_kept():
    r = PcmRing(10); r.write(b"\x01\x02\x03"); assert r.head == 0
    r.write(b"\x04\x05"); assert r.head == 1 and r.read(0, 5, 0)[1] == b"\x01\x02\x03\x04"


# ---- feeder ------------------------------------------------------------------
def mk(clock):
    ring = PcmRing(44100 * 5); f = LiveFeeder(ring, clock=clock.now); return ring, f


def test_feeder_inserts_silence_to_keep_timeline_live_only_when_session_active():
    c = FakeClock(); ring, f = mk(c); f.push(b"\x01\x00\x01\x00" * 882)
    c.advance(1.0); f.tick(); assert ring.head == 882                           # no session: no fill
    f.set_fill(True); c.advance(1.0); f.tick()
    assert f.silence_frames >= 44100 * 0.9 and f.silence_frames <= 44100 * 1.1  # filled ~real-time


def test_feeder_drops_frames_when_source_runs_ahead_bounded():
    c = FakeClock(); ring, f = mk(c)
    for _ in range(100):                                                       # 2 s of audio delivered instantly
        f.push(b"\x01\x00\x01\x00" * 882)
    assert f.dropped_frames > 0 and ring.head <= 44100 * 0.2                    # only ~max_ahead retained


def test_feeder_lead_in_never_crosses_a_gap_or_returns_stale_audio():
    c = FakeClock(); ring, f = mk(c)
    for _ in range(10):
        f.push(b"\x01\x00\x01\x00" * 882); c.advance(0.02)
    c.advance(10.0)                                                            # long pause: segment ended
    import time; ring.last_write_mono = time.monotonic() - 10
    assert f.lead_in_start(1000) == ring.head                                  # nothing stale


def test_feeder_lead_in_is_bounded_by_lead_ms():
    c = FakeClock(); ring, f = mk(c)
    for _ in range(100):
        f.push(b"\x01\x00\x01\x00" * 882); c.advance(0.02)
    assert ring.head - f.lead_in_start(500) <= 22050 + 1


# ---- fusion ------------------------------------------------------------------
def test_fusion_avrcp_playing_with_transport():
    c = FakeClock(); m = MediaFusion(c); m.on_transport("active"); m.on_avrcp("playing"); assert m.playing()
    m.on_avrcp("paused"); assert not m.playing()


def test_fusion_notification_transport_only_with_avrcp_available_is_not_media():
    c = FakeClock(); m = MediaFusion(c); m.on_avrcp("paused"); m.on_transport("active"); m.on_pcm()
    assert not m.playing()


def test_fusion_without_avrcp_uses_transport_and_pcm_and_quiet_passages_stay_playing():
    c = FakeClock(); m = MediaFusion(c); m.on_transport("active"); m.on_pcm(); assert m.playing()
    c.advance(1.0); m.on_pcm(); assert m.playing()                              # digital silence frames still arrive
    c.advance(3.0); assert not m.playing()                                      # frames stopped -> paused


def test_fusion_transport_idle_is_never_playing():
    c = FakeClock(); m = MediaFusion(c); m.on_avrcp("playing"); m.on_transport("idle"); assert not m.playing()


# ---- volume --------------------------------------------------------------------
def test_volume_first_value_is_reference_only():
    v = VolumePolicy(); assert v.on_phone_volume(127, 30) is None


def test_volume_big_jump_is_stepped_and_ceiling_respected():
    v = VolumePolicy(max_step=10, ceiling=50); v.on_phone_volume(0, 30)
    assert v.on_phone_volume(127, 30) == 10                      # bounded step
    v.reset(); v.on_phone_volume(0, 45); assert v.on_phone_volume(127, 45) == 5     # ceiling trims the step
    v.reset(); v.on_phone_volume(0, 50); assert v.on_phone_volume(127, 50) is None  # at ceiling: no increase


def test_volume_capped_jump_excess_is_not_replayed_on_later_small_decrease():
    v = VolumePolicy(max_step=10, ceiling=100); v.on_phone_volume(60, 50)
    assert v.on_phone_volume(127, 50) == 10                       # big jump: one bounded step
    assert v.on_phone_volume(120, 60) < 0                         # small phone decrease must lower LMS volume, never raise it


def test_volume_decrease_not_limited_by_ceiling():
    v = VolumePolicy(ceiling=50); v.on_phone_volume(100, 90); assert v.on_phone_volume(80, 90) < 0


# ---- pairing ---------------------------------------------------------------------
def test_pairing_window_expires_and_notifies_adapter():
    c = FakeClock(); ev = []; p = PairingWindow(300, c, on_change=ev.append)
    p.open(); assert p.is_open() and ev == [True]
    c.advance(299); p.tick(); assert p.is_open() and 0 < p.remaining() <= 1
    c.advance(2); p.tick(); assert not p.is_open() and ev == [True, False]


def test_pairing_can_be_closed_early_and_reopened():
    c = FakeClock(); p = PairingWindow(300, c); p.open(); p.close(); assert not p.is_open(); p.open(); assert p.is_open()


# ---- stream server (real sockets, no LMS) -------------------------------------------
@pytest.fixture
def srv():
    ring = PcmRing(44100 * 4); f = LiveFeeder(ring); s = StreamServer(ring, f, "tok_SECRET-123", "wav", lead_in_ms=200)
    port = s.start("127.0.0.1", 0); s.open_session("sess1"); stop = threading.Event()
    threading.Thread(target=f.run, args=(stop,), daemon=True).start()
    yield s, port, f
    stop.set(); s.stop()


def get(port, path, headers=None, read=0):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3); c.request("GET", path, headers=headers or {})
    r = c.getresponse(); body = r.read(read) if read else b""; c.close(); return r.status, r, body


def test_server_rejects_bad_token_and_stale_session(srv):
    s, port, _ = srv
    assert get(port, "/s/wrongtoken/sess1.wav")[0] == 403
    assert get(port, "/s/tok_SECRET-123/oldsess.wav")[0] == 410
    assert s.stats["bad_token"] == 1 and s.stats["stale_rejected"] == 1


def test_server_serves_wav_header_then_audio_and_range_skips_header(srv):
    s, port, f = srv
    f.push(b"\x07\x00\x07\x00" * 4410)
    st, r, body = get(port, "/s/tok_SECRET-123/sess1.wav", read=44)
    assert st == 200 and body[:4] == b"RIFF" and r.getheader("Content-Type") == "audio/x-wav"
    st, r, body = get(port, "/s/tok_SECRET-123/sess1.wav", {"Range": "bytes=44-"}, read=8)
    assert st == 206 and body[:2] != b"RI"                                       # header skipped


def test_server_closes_connection_when_session_ends(srv):
    s, port, f = srv
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3); c.request("GET", "/s/tok_SECRET-123/sess1.wav")
    r = c.getresponse(); r.read(44); s.close_session("sess1")
    import time; t = time.time()
    try:
        while r.read(4096):
            assert time.time() - t < 3
    except Exception:
        pass
    assert time.time() - t < 3                                                  # stream ended promptly


def test_token_rotation_invalidates_new_requests_only(srv):
    s, port, _ = srv; s.rotate_token("new-token")
    assert get(port, "/s/tok_SECRET-123/sess1.wav")[0] == 403 and get(port, "/s/new-token/sess1.wav", read=1)[0] == 200


def test_tokens_are_redacted_in_logs():
    assert "SECRET" not in redact("GET /s/tok_SECRET-123/abc.wav HTTP/1.1 200")
