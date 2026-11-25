"""UNIT (real sockets, real StreamServer/LiveFeeder/PcmRing, SIMULATED PipeWire source, no LMS): the live HTTP stream's semantics
for the G1 hardware finding "audio silent 10-15 s, back exactly with a new 'GET ... 206'" (docs/STREAM_TRACE.md).

What these tests pin down (they are regression tests of the bridge's side; they do NOT reproduce the Pi outage - see the report):
  * 200 / 206 headers: endless WAV header only without a Range >= 44; Content-Range for every Range request; never a Content-Length;
  * an LMS retry request (``Range: bytes=44-4294967338`` or any large offset) is answered with FRESH live audio, never stale audio;
  * repeated 206 requests (previous connection closed or reset) each get contiguous fresh PCM; slots and threads are released;
  * a connection that is held keeps delivering at 1x for 2+ minutes (bit-exact, contiguous, ONE connection) and through a source gap
    (silence-filled) - no new request is needed for continuity;
  * simultaneous readers get identical bytes; a slow reader is skipped forward without disturbing the others;
  * the opt-in trace is off by default, never shows the token/URL/peer address, and reports bytes, levels, blocked writes and why
    a connection ended.
"""
import re
import socket
import struct
import threading
import time

import numpy as np
import pytest

from btbridge.app import BridgeRuntime, RuntimeConfig
from btbridge.core.controller import Config
from btbridge.core.ring import PcmRing
from btbridge.stream import server as S
from btbridge.stream.feeder import LiveFeeder
from btbridge.stream.server import StreamServer
from btbridge.stream.trace import Level, StreamTrace, enabled_from_env, safe_range

TOKEN = "unit-continuity-token-XYZ"
RATE = 44100
FB = 4


def counter_pcm(start: int, frames: int) -> bytes:
    """Frame i carries the 32-bit counter i (left = low 16 bits, right = high 16 bits) - contiguity is checkable bit-exactly."""
    i = np.arange(start, start + frames, dtype=np.uint32)
    lr = np.empty((frames, 2), dtype="<u2")
    lr[:, 0], lr[:, 1] = i & 0xFFFF, i >> 16
    return lr.tobytes()


def frames_of(body: bytes) -> np.ndarray:
    a = np.frombuffer(body[: len(body) // FB * FB], dtype="<u2").reshape(-1, 2).astype(np.int64)
    return a[:, 0] + (a[:, 1] << 16)


def contiguous(vals: np.ndarray) -> bool:
    return len(vals) < 2 or bool(np.all(np.diff(vals) == 1))


class Source(threading.Thread):
    """Simulated PipeWire capture: 20 ms counter chunks at real time into ``sink`` (like PipeWireCapture's on_pcm), pausable."""
    def __init__(self, sink, chunk=882):
        super().__init__(daemon=True)
        self.sink, self.chunk, self.i = sink, chunk, 0
        self.stop, self.paused = threading.Event(), threading.Event()

    def run(self):
        t = time.monotonic()
        while not self.stop.is_set():
            if not self.paused.is_set():
                self.sink(counter_pcm(self.i, self.chunk))
                self.i += self.chunk
            t += self.chunk / RATE
            time.sleep(max(0.0, t - time.monotonic()))


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(S, "REQUEST_TIMEOUT_S", 1.0)
    monkeypatch.setattr(S, "WRITE_TIMEOUT_S", 2.0)
    orig = S._Server.get_request

    def small_send_buffer(self):                              # like a slow peer on a Pi: writes block soon, not after MBs of
        sock, addr = orig(self)                               # loopback buffering
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        return sock, addr
    monkeypatch.setattr(S._Server, "get_request", small_send_buffer)
    lines = []
    ring = PcmRing(RATE * 2)                                   # 2 s ring: a slow reader is skipped quickly
    f = LiveFeeder(ring, tolerance_ms=400, max_ahead_ms=1000)     # a loaded CI host's scheduling jitter must not insert silence
    s = StreamServer(ring, f, TOKEN, "wav", lead_in_ms=300)
    tr = StreamTrace(lines.append, period_s=0.25)
    tr.feeder, tr.ring = f, ring
    s.trace = tr
    port = s.start("127.0.0.1", 0)
    s.open_session("sessA")
    stop = threading.Event()
    threading.Thread(target=f.run, args=(stop,), daemon=True).start()
    tr.start(stop)
    def on_pcm(pcm):                                          # what BridgeRuntime._on_pcm does with the trace on
        tr.ingress(pcm)
        f.push(pcm)
    src = Source(on_pcm)
    src.start()
    time.sleep(0.5)
    yield s, port, f, src, lines
    src.stop.set(); stop.set(); s.stop()


def request(port, rng=None, path=None, rcvbuf=None):
    c = socket.socket()
    if rcvbuf:
        c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    c.connect(("127.0.0.1", port))
    c.settimeout(5)
    hdr = f"GET {path or f'/s/{TOKEN}/sessA.wav'} HTTP/1.0\r\nIcy-MetaData: 1\r\nConnection: close\r\n"
    if rng:
        hdr += f"Range: {rng}\r\n"
    c.sendall((hdr + "\r\n").encode())
    return c


def read_headers(c):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = c.recv(4096)
        assert d, buf
        buf += d
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    code = int(lines[0].split()[1])
    hdrs = {k.lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:])}
    return code, hdrs, rest


def read_for(c, seconds, initial=b""):
    out = bytearray(initial)
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        c.settimeout(left)
        try:
            d = c.recv(65536)
        except socket.timeout:
            break
        if not d:
            break
        out += d
    c.settimeout(5)
    return bytes(out)


# ---------------------------------------------------------------------------------------------------------------- headers
def test_a_plain_request_gets_200_and_the_endless_wav_header(live):
    s, port, *_ = live
    c = request(port)
    code, h, rest = read_headers(c)
    body = read_for(c, 0.5, rest)
    c.close()
    assert code == 200 and h["content-type"] == "audio/x-wav"
    assert "content-length" not in h and "content-range" not in h        # endless: never a length
    assert body[:4] == b"RIFF" and body[8:16] == b"WAVEfmt " and body[36:40] == b"data"
    assert struct.unpack("<I", body[40:44])[0] == 0xFFFFFFFF
    rate, = struct.unpack("<I", body[24:28]); ch, = struct.unpack("<H", body[22:24]); bits, = struct.unpack("<H", body[34:36])
    assert (rate, ch, bits) == (44100, 2, 16)
    assert contiguous(frames_of(body[44:]))


@pytest.mark.parametrize("rng", ["bytes=44-", "bytes=44-4294967338", "bytes=1234567-", "bytes=99999999-4294967294",
                                 "bytes=4294967294-", "bytes=4294967295-", "bytes=4294967336-", "bytes=4294967336-4294967294"])
def test_every_lms_range_request_gets_206_without_header_and_fresh_live_pcm(live, rng):
    """'bytes=44-' = what LMS 9.1.2 sends after parsing the header (lab-observed); 'bytes=44-<end>' = the form LMS builds when it
    knows an audio_size (Protocols/HTTP.pm requestString); a large offset = a persistent-HTTP / seek reconnect. All get the live
    edge, never old audio.

    ``bytes=4294967336-`` (= 2^32 + 40) is what the G1-S round-2 Pi traces 2/3 showed: Lyrion's elapsed time was clamped to the
    virtual WAV duration (4294967295 B / 176400 B/s = 24347.9 s), so ``getSeekData`` asked for offset(duration) + 44. It is a
    CONSEQUENCE of Lyrion's clamped time, not a bridge computation; the bridge must keep serving it (a 416 would end Lyrion's
    recovery) and keeps echoing the requested start in Content-Range, because Lyrion derives its ``startOffset`` from exactly that
    (Protocols/HTTP.pm ~l.757-795). A start beyond the virtual end therefore yields a formally reversed Content-Range; pinned here."""
    s, port, f, src, _ = live
    head_at_request = s.ring.head
    c = request(port, rng)
    code, h, rest = read_headers(c)
    body = read_for(c, 0.6, rest)
    c.close()
    first = int(rng.split("=")[1].split("-")[0])
    assert code == 206
    assert h["content-range"] == f"bytes {first}-4294967294/4294967295"
    assert "content-length" not in h
    assert body[:4] != b"RIFF" and len(body) > RATE
    vals = frames_of(body)
    assert contiguous(vals)
    # fresh: the first frame is at most the lead-in (300 ms) older than the live edge at request time (counter == ring position)
    assert int(vals[0]) >= head_at_request - int(0.31 * RATE), (int(vals[0]), head_at_request)


def test_repeated_206_requests_after_close_and_reset_each_get_fresh_contiguous_audio(live):
    s, port, f, src, lines = live
    starts = []
    for k in range(6):
        c = request(port, "bytes=44-4294967338")
        code, _, rest = read_headers(c)
        body = read_for(c, 0.4, rest)
        assert code == 206
        vals = frames_of(body)
        assert contiguous(vals)
        starts.append(int(vals[0]))
        if k % 2:
            c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # abrupt: RST
        c.close()
    assert starts == sorted(starts) and starts[-1] > starts[0]               # every new request starts later (live edge)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and sum("end reason=" in ln for ln in lines) < 6:
        time.sleep(0.1)
    ends = [ln for ln in lines if " end reason=" in ln]
    assert len(ends) == 6, ends
    assert all(re.search(r"reason=(peer-closed|peer-reset)", ln) for ln in ends), ends
    assert s.httpd._slots.acquire(blocking=False)                           # slots were released
    s.httpd._slots.release()


# ---------------------------------------------------------------------------------------------------------- held connection
def test_a_held_connection_streams_two_minutes_bit_exact_without_any_new_request(monkeypatch):
    """130 s of stream time through ONE connection, accelerated: the source is paced by the reader (a fake feeder clock advances
    by each chunk's duration, so the feeder sees exactly real-time PCM) - deterministic, ~5 s wall time. Every frame arrives,
    contiguous, no silence, no drop, no skip, and exactly one connection."""
    ring = PcmRing(RATE * 6)
    clock = [1000.0]
    f = LiveFeeder(ring, clock=lambda: clock[0])
    s = StreamServer(ring, f, TOKEN, "wav", lead_in_ms=0)
    port = s.start("127.0.0.1", 0)
    s.open_session("sessA")
    total = 130 * RATE
    got = bytearray()
    received = [0]
    pushed = [0]
    try:
        c = request(port, "bytes=44-")
        code, _, rest = read_headers(c)
        assert code == 206
        got += rest

        def push():
            chunk = 882
            while pushed[0] < total:
                while pushed[0] - received[0] // FB > 2 * RATE:                # never more than 2 s ahead of the reader
                    time.sleep(0.001)
                f.push(counter_pcm(pushed[0], chunk))
                pushed[0] += chunk
                clock[0] += chunk / RATE
        t = threading.Thread(target=push, daemon=True)
        time.sleep(0.05)
        t.start()
        c.settimeout(5)
        while len(got) < total * FB:
            d = c.recv(1 << 16)
            assert d, "connection ended"
            got += d
            received[0] = len(got)
        c.close()
        t.join(5)
    finally:
        s.stop()
    vals = frames_of(bytes(got[: total * FB]))
    assert vals[0] == 0 and contiguous(vals) and len(vals) == total
    assert f.silence_frames == 0 and f.dropped_frames == 0 and s.stats["ring_skips"] == 0
    assert s.stats["connections"] == 1


def test_a_source_gap_does_not_need_a_new_request_the_held_connection_continues_at_1x(live):
    """The phone/PipeWire delivers nothing for 2.5 s: the SAME connection carries silence at real time, then audio again."""
    s, port, f, src, lines = live
    c = request(port, "bytes=44-")
    code, _, rest = read_headers(c)
    a = read_for(c, 1.0, rest)
    src.paused.set()
    b = read_for(c, 2.5)
    src.paused.clear()
    d = read_for(c, 1.5)
    c.close()
    assert code == 206
    rate_b = len(b) / FB / 2.5
    assert 0.8 * RATE < rate_b < 1.3 * RATE                                  # delivery continued at ~1x during the gap
    assert np.count_nonzero(np.frombuffer(b[FB * RATE // 2: -FB * RATE // 2], "<i2")) == 0   # it was silence (fill)
    after = frames_of(d[FB * RATE // 2:])
    assert contiguous(after) and np.count_nonzero(after) > 0                 # real audio again, same connection
    assert s.stats["connections"] == 1 and f.silence_frames > RATE


# --------------------------------------------------------------------------------------------------------- several readers
def test_simultaneous_readers_get_identical_bytes(live):
    s, port, *_ = live
    cs = [request(port, "bytes=44-") for _ in range(3)]
    bodies = []
    for c in cs:
        _, _, rest = read_headers(c)
        bodies.append(rest)
    res = [None] * 3

    def rd(i):
        res[i] = read_for(cs[i], 1.5, bodies[i])
    ts = [threading.Thread(target=rd, args=(i,)) for i in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    [c.close() for c in cs]
    vals = [frames_of(r) for r in res]
    assert all(contiguous(v) for v in vals)
    lo = max(int(v[0]) for v in vals); hi = min(int(v[-1]) for v in vals)
    assert hi - lo > RATE // 2
    segs = [v[(v >= lo) & (v <= hi)] for v in vals]
    assert all(np.array_equal(segs[0], x) for x in segs[1:])


def test_a_slow_reader_is_skipped_forward_and_the_others_are_not_disturbed(live, monkeypatch):
    s, port, f, src, lines = live
    monkeypatch.setattr(S, "WRITE_TIMEOUT_S", 8.0)                          # slower than the ring, but not yet "gone"
    slow = request(port, "bytes=44-", rcvbuf=4096)
    fast = request(port, "bytes=44-")
    read_headers(slow)
    _, _, rest = read_headers(fast)
    out = bytearray(rest)
    end = time.monotonic() + 3.5                                             # slow reader reads nothing for 3.5 s (> 2 s ring)
    while time.monotonic() < end:
        out += fast.recv(65536)
    time.sleep(0.3)
    tail = read_for(slow, 1.0)                                               # it resumes: whatever it gets is live and contiguous
    fast.close(); slow.close()
    assert contiguous(frames_of(bytes(out)))
    assert s.stats["ring_skips"] >= 1
    assert len(tail) > 0


# ------------------------------------------------------------------------------------------------------------------ trace
def test_the_trace_is_off_by_default_and_on_only_with_the_environment_switch(monkeypatch):
    monkeypatch.delenv("HIFI_STREAM_TRACE", raising=False)
    assert enabled_from_env() is False
    rt = BridgeRuntime(RuntimeConfig(stream_port=0), Config(target_player="aa:bb"), lms=object(), log=lambda m: None)
    assert rt.trace is None and rt.stream.trace is None
    monkeypatch.setenv("HIFI_STREAM_TRACE", "1")
    assert enabled_from_env() is True
    rt2 = BridgeRuntime(RuntimeConfig(stream_port=0), Config(target_player="aa:bb"), lms=object(), log=lambda m: None)
    assert rt2.trace is not None and rt2.stream.trace is rt2.trace
    assert BridgeRuntime(RuntimeConfig(stream_port=0, stream_trace=False), Config(target_player="aa:bb"), lms=object(),
                         log=lambda m: None).trace is None


def test_trace_lines_carry_the_needed_fields_and_nothing_private(live):
    s, port, f, src, lines = live
    c = request(port, "bytes=44-4294967338")
    read_headers(c)
    read_for(c, 1.2)
    c.close()
    time.sleep(0.6)
    text = "\n".join(lines)
    assert TOKEN not in text and "127.0.0.1" not in text and "/s/" not in text
    start = [ln for ln in lines if " start GET 206" in ln]
    assert start and "range=bytes=44-4294967338" in start[0] and "content-range=bytes 44-4294967294/4294967295" in start[0]
    assert "sid=sessA" in start[0] and "peer1" in start[0] and "wav-header=no" in start[0]
    summary = [ln for ln in lines if " in: " in ln and " out: r" in ln]
    assert summary, lines
    assert re.search(r"in: \d+ch \d+B peak=-?[\d.]+dBFS nz=1\.00", summary[-1])
    assert re.search(r"r\d+:\d+B peak=-?[\d.]+dBFS nz=\d\.\d\d lag=\d+ms blk=\d+ms", summary[-1])
    assert re.search(r"feeder: \+\d+fr real \+\d+fr silence \+\d+fr dropped fill=on", summary[-1])
    assert any(re.search(r"end reason=peer-(closed|reset) after=[\d.]+s bytes=\d+ sid=sessA peer1", ln) for ln in lines)


def test_trace_shows_a_blocked_write_while_it_is_still_stuck(live, monkeypatch):
    s, port, f, src, lines = live
    c = request(port, "bytes=44-", rcvbuf=4096)                             # never reads: the server's write blocks
    read_headers(c)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not any("STUCK" in ln for ln in lines):
        time.sleep(0.1)
    assert any(re.search(r"blk=\d{4,}ms STUCK", ln) for ln in lines), lines[-5:]
    time.sleep(0.6)
    stuck = [ln for ln in lines if "STUCK" in ln]
    assert int(re.search(r"lag=(\d+)ms blk", stuck[-1]).group(1)) >= 500, stuck[-1]      # the lag grows while the write is stuck
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not any("end reason=write-timeout" in ln for ln in lines):
        time.sleep(0.1)
    assert any(re.search(r"end reason=write-timeout-2s .* write-in-progress=", ln) for ln in lines), lines[-5:]
    c.close()


def test_trace_reports_a_stale_session_request_and_the_session_end_reason(live):
    s, port, f, src, lines = live
    c = request(port, "bytes=44-")
    read_headers(c); read_for(c, 0.3)
    s.close_session("sessA")
    read_for(c, 1.0)
    c.close()
    time.sleep(0.3)
    assert any("end reason=session-ended" in ln for ln in lines)


def test_level_and_range_helpers():
    lv = Level(); lv.add(bytes(400), 4)
    assert lv.text() == "peak=-infdBFS nz=0.00"
    lv2 = Level(); lv2.add(struct.pack("<4h", 0, 16384, -32767, 0) * 10, 1)
    assert lv2.peak == 32767 and abs(lv2.nonzero / lv2.samples - 0.5) < 1e-9 and "peak=0.0dBFS" in lv2.text()
    assert Level().text() == "peak=- nz=-"
    assert safe_range("bytes=44-") == "bytes=44-" and safe_range(None) == "-"
    assert safe_range("bytes=0-1, 5-6") == "other" and safe_range("bytes=44-<script>") == "other"


def test_the_trace_does_not_change_a_single_byte_of_the_stream(monkeypatch):
    """Same source, same timing, trace on vs off: identical body (the trace only reads)."""
    bodies = []
    for on in (False, True):
        ring = PcmRing(RATE * 6); clock = [1.0]
        f = LiveFeeder(ring, clock=lambda: clock[0])
        s = StreamServer(ring, f, TOKEN, "wav", lead_in_ms=0)
        if on:
            s.trace = StreamTrace(lambda m: None)
        port = s.start("127.0.0.1", 0); s.open_session("sessA")
        try:
            c = request(port)
            _, _, rest = read_headers(c)
            time.sleep(0.05)
            for k in range(50):
                f.push(counter_pcm(k * 882, 882)); clock[0] += 0.02
            body = read_for(c, 0.5, rest)
            c.close()
        finally:
            s.stop()
        bodies.append(body)
    assert bodies[0] == bodies[1] and len(bodies[0]) == 44 + 50 * 882 * FB
