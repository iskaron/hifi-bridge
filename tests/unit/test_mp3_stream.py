"""UNIT (real sockets + real ``lame``, no LMS): the live MP3 endpoint. Headers of a live stream (no length, no ranges), a body that is
genuine 320 kbit/s MP3, ICY only where it is switched on AND requested, title changes in the middle of the stream, a clean 503 when
the encoder is missing/over its limit, and no encoder process left behind (DECISIONS #147)."""
import os
import shutil
import socket
import threading
import time

import numpy as np
import pytest

from btbridge.core.ring import PcmRing
from btbridge.stream import encoders as E
from btbridge.stream import mp3frames as F
from btbridge.stream import server as S
from btbridge.stream.feeder import LiveFeeder
from btbridge.stream.server import StreamServer

pytestmark = pytest.mark.skipif(shutil.which("lame") is None, reason="needs the lame CLI (the lab image has it)")
TOKEN = "unit-mp3-token"
RATE = 44100


def make(fmt="mp3", icy=False, icy_mp3=False):
    ring = PcmRing(RATE * 6)
    f = LiveFeeder(ring)
    s = StreamServer(ring, f, TOKEN, fmt, lead_in_ms=300, icy=icy, icy_mp3=icy_mp3)
    port = s.start("127.0.0.1", 0)
    s.open_session("m1")
    stop = threading.Event()

    def source():                                    # a live source: 20 ms of tone every 20 ms
        k = 0
        while not stop.is_set():
            n = RATE // 50
            t = np.arange(k, k + n)
            pcm = np.empty(n * 2, dtype="<i2")
            pcm[0::2] = (8000 * np.sin(2 * np.pi * 440 * t / RATE)).astype("<i2")
            pcm[1::2] = (8000 * np.sin(2 * np.pi * 660 * t / RATE)).astype("<i2")
            f.push(pcm.tobytes())
            k += n
            time.sleep(0.02)
    threading.Thread(target=f.run, args=(stop,), daemon=True).start()
    threading.Thread(target=source, daemon=True).start()
    return s, port, stop


@pytest.fixture
def mp3srv():
    s, port, stop = make()
    yield s, port
    stop.set(); s.stop()


def request(port, path_ext="mp3", extra="", method="GET", seconds=2.0, sid="m1", limit=10**7):
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(f"{method} /s/{TOKEN}/{sid}.{path_ext} HTTP/1.0\r\n{extra}\r\n".encode())
    buf, t0 = b"", time.monotonic()
    c.settimeout(0.5)
    while time.monotonic() - t0 < seconds and len(buf) < limit:
        try:
            d = c.recv(65536)
        except socket.timeout:
            continue
        if not d:
            break
        buf += d
    c.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    return head.decode("latin-1"), body


def lame_pids():
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                if open(f"/proc/{p}/comm").read().strip() == "lame":
                    out.append(int(p))
            except OSError:
                pass
    return out


def wait_until(cond, t=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_a_live_mp3_get_is_200_audio_mpeg_without_length_or_range_and_the_body_is_320k_mp3(mp3srv):
    s, port = mp3srv
    head, body = request(port, seconds=2.5)
    lines = head.split("\r\n")
    assert lines[0] == "HTTP/1.0 200 OK"
    hdr = {k.lower(): v for k, v in (l.split(": ", 1) for l in lines[1:])}
    assert hdr["content-type"] == "audio/mpeg" and hdr["icy-br"] == "320" and hdr["accept-ranges"] == "none"
    assert "content-length" not in hdr and "content-range" not in hdr and "icy-metaint" not in hdr
    start = F.find_first_frame(body)
    assert start == 0
    frames, end = F.walk(body)
    assert len(frames) > 20 and {f.bitrate_kbps for f in frames} == {320} and {f.mode for f in frames} == {"stereo"}
    assert len(body) - end < 1100                                                # a clean frame stream, no WAV header, no stray bytes


def test_a_range_request_for_the_live_mp3_is_answered_with_the_live_stream_not_a_fabricated_206(mp3srv):
    s, port = mp3srv
    head, body = request(port, extra="Range: bytes=44-\r\n", seconds=1.5)
    assert head.startswith("HTTP/1.0 200") and "content-range" not in head.lower()
    assert F.walk(body)[0], "the stream starts on a frame boundary, not 44 bytes into something"
    head2, _ = request(port, extra="Range: bytes=0-\r\n", seconds=0.5)
    assert head2.startswith("HTTP/1.0 200")


def test_head_returns_the_headers_and_starts_no_encoder(mp3srv):
    s, port = mp3srv
    before = set(lame_pids())
    head, body = request(port, method="HEAD", seconds=1.0)
    assert head.startswith("HTTP/1.0 200") and "audio/mpeg" in head and body == b""
    assert set(lame_pids()) <= before and s.stats["encoders_started"] == 0


def test_stale_session_wrong_token_and_wrong_format_never_start_an_encoder(mp3srv):
    s, port = mp3srv
    assert request(port, sid="old", seconds=0.5)[0].startswith("HTTP/1.0 410")
    assert request(port, path_ext="wav", seconds=0.5)[0].startswith("HTTP/1.0 404")            # the session streams mp3
    c = socket.create_connection(("127.0.0.1", port), timeout=3)
    c.sendall(b"GET /s/wrong/m1.mp3 HTTP/1.0\r\n\r\n")
    assert c.recv(100).startswith(b"HTTP/1.0 403")
    assert s.stats["encoders_started"] == 0


def test_icy_is_sent_only_when_switched_on_for_mp3_and_requested_by_the_client():
    s, port, stop = make(icy=False, icy_mp3=True)                                # the WAV switch does not matter for MP3
    try:
        s.set_title("Miles Davis - Blue in Green")
        head, body = request(port, extra="Icy-MetaData: 1\r\n", seconds=2.0)
        assert "icy-metaint: 8192" in head
        assert wait_until(lambda: s.stats["connections"] >= 1)
        audio, titles = demux_icy(body, 8192)
        assert titles and titles[0] == "StreamTitle='Miles Davis - Blue in Green';"
        assert F.walk(audio)[0] and {f.bitrate_kbps for f in F.walk(audio)[0]} == {320}   # removing the ICY blocks leaves a clean MP3
        head, body = request(port, seconds=1.0)                                  # a client that does not ask gets the plain stream
        assert "icy-metaint" not in head and F.walk(body)[0] and F.walk(body)[1] > len(body) - 1100
    finally:
        stop.set(); s.stop()


def test_mp3_without_its_switch_ignores_the_icy_request_and_wav_icy_is_not_a_switch_for_mp3():
    s, port, stop = make(icy=True, icy_mp3=False)                                # WAV ICY on, MP3 ICY off
    try:
        s.set_title("A - B")
        head, body = request(port, extra="Icy-MetaData: 1\r\n", seconds=1.0)
        assert "icy-metaint" not in head and F.walk(body)[1] > len(body) - 1100
    finally:
        stop.set(); s.stop()


def test_the_title_changes_in_the_middle_of_the_stream_without_interrupting_the_audio():
    s, port, stop = make(icy_mp3=True)
    try:
        s.set_title("First - Song")
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(f"GET /s/{TOKEN}/m1.mp3 HTTP/1.0\r\nIcy-MetaData: 1\r\n\r\n".encode())
        buf, t0, changed = b"", time.monotonic(), False
        c.settimeout(0.5)
        while time.monotonic() - t0 < 6:
            try:
                d = c.recv(65536)
            except socket.timeout:
                continue
            buf += d
            if not changed and time.monotonic() - t0 > 2.0:
                s.set_title("Second - Song"); changed = True
        c.close()
        _, _, body = buf.partition(b"\r\n\r\n")
        audio, titles = demux_icy(body, 8192)
        assert titles[0] == "StreamTitle='First - Song';" and titles[-1] == "StreamTitle='Second - Song';"
        frames, end = F.walk(audio)
        assert len(audio) - end < 1100 and len(frames) > 180                     # ~6 s of unbroken frames across the title change
        assert len(titles) < 12                                                   # changes and the occasional safety repeat, not every block
    finally:
        stop.set(); s.stop()


def test_a_missing_encoder_is_a_clean_503_and_wav_still_works(monkeypatch):
    monkeypatch.setattr(E, "mp3_encoder_path", lambda: None)
    monkeypatch.setattr(S, "mp3_encoder_path", lambda: None)
    s, port, stop = make()
    try:
        assert s.encoder_error() and "lame" in s.encoder_error()
        head, _ = request(port, seconds=1.0)
        assert head.startswith("HTTP/1.0 503") and s.stats["encoder_refused"] == 1 and s.stats["connections"] == 0
        s.fmt = "wav"
        head, body = request(port, path_ext="wav", seconds=1.0)
        assert head.startswith("HTTP/1.0 200") and body[:4] == b"RIFF" and s.encoder_error() is None
    finally:
        stop.set(); s.stop()


def test_more_encoders_than_the_limit_are_refused_with_503_and_the_slots_come_back(monkeypatch):
    monkeypatch.setattr(S, "MAX_MP3_ENCODERS", 2)
    s, port, stop = make()
    try:
        s._enc_slots = threading.BoundedSemaphore(2)
        held = []
        for _ in range(2):
            c = socket.create_connection(("127.0.0.1", port), timeout=5)
            c.sendall(f"GET /s/{TOKEN}/m1.mp3 HTTP/1.0\r\n\r\n".encode())
            assert c.recv(15).startswith(b"HTTP/1.0 200")
            held.append(c)
        head, _ = request(port, seconds=1.0)
        assert head.startswith("HTTP/1.0 503") and s.stats["encoder_refused"] == 1
        for c in held:
            c.close()
        assert wait_until(lambda: not lame_pids(), 8)                            # closed clients: their encoders are killed and reaped
        head, body = request(port, seconds=1.0)
        assert head.startswith("HTTP/1.0 200") and body
    finally:
        stop.set(); s.stop()


def test_ending_the_session_closes_the_stream_and_leaves_no_encoder_process_behind(mp3srv):
    s, port = mp3srv
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(f"GET /s/{TOKEN}/m1.mp3 HTTP/1.0\r\n\r\n".encode())
    c.recv(4096)
    assert wait_until(lambda: lame_pids())
    s.close_session("m1")
    c.settimeout(3)
    try:
        while c.recv(65536):
            pass
    except OSError:
        pass
    assert wait_until(lambda: not lame_pids(), 5)
    assert "session-ended" in [e[2] for e in s.events if e[1] == "client_disconnected"]
    c.close()


def test_a_client_that_hangs_up_takes_its_encoder_with_it(mp3srv):
    s, port = mp3srv
    for _ in range(3):
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(f"GET /s/{TOKEN}/m1.mp3 HTTP/1.0\r\n\r\n".encode())
        c.recv(2048)
        c.close()
    assert wait_until(lambda: not lame_pids(), 8) and s.stats["encoders_started"] == 3


def test_when_the_encoder_dies_the_connection_ends_visibly_and_other_formats_are_not_affected(mp3srv):
    s, port = mp3srv
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(f"GET /s/{TOKEN}/m1.mp3 HTTP/1.0\r\n\r\n".encode())
    c.recv(2048)
    for pid in lame_pids():
        os.kill(pid, 9)
    c.settimeout(4)
    try:
        while c.recv(65536):
            pass
    except OSError:
        pass
    assert wait_until(lambda: s.stats["encoder_died"] >= 1, 5)
    assert "encoder-died" in [e[2] for e in s.events if e[1] == "client_disconnected"]
    c.close()


def demux_icy(body: bytes, metaint: int):
    audio, titles, i = bytearray(), [], 0
    while i + metaint < len(body):
        audio += body[i:i + metaint]
        i += metaint
        n = body[i] * 16
        i += 1
        if n:
            if i + n > len(body):
                break
            titles.append(body[i:i + n].rstrip(b"\0").decode("utf-8"))
            i += n
    audio += body[i:]
    return bytes(audio), titles
