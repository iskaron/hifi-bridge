"""UNIT (real sockets, no LMS): regressions for review findings on the stream server and encoders
(reports/REVIEW_PHASE2B_EXISTING_CODE.md: M2, M7, L4)."""
import socket
import threading
import time

import pytest

from btbridge.core.ring import PcmRing
from btbridge.stream import server as S
from btbridge.stream.feeder import LiveFeeder
from btbridge.stream.server import StreamServer

TOKEN = "tok_SECRET-123"


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(S, "REQUEST_TIMEOUT_S", 0.5)
    monkeypatch.setattr(S, "WRITE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(S, "MAX_CONNECTIONS", 4)
    ring = PcmRing(44100 * 4); f = LiveFeeder(ring); s = StreamServer(ring, f, TOKEN, "wav", lead_in_ms=200)
    port = s.start("127.0.0.1", 0); s.open_session("sess1"); stop = threading.Event()
    threading.Thread(target=f.run, args=(stop,), daemon=True).start()
    yield s, port, f
    stop.set(); s.stop()


def _closed_within(sock, seconds):
    sock.settimeout(seconds)
    try:
        while sock.recv(65536):
            pass
        return True
    except (ConnectionResetError, OSError):
        return True
    except socket.timeout:
        return False


def test_idle_connection_without_a_request_is_closed(srv):
    _, port, _ = srv
    c = socket.create_connection(("127.0.0.1", port))
    assert _closed_within(c, 3)                               # was: a thread per idle peer, forever


def test_stalled_reader_is_dropped(srv):
    s, port, _ = srv
    c = socket.socket()
    c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    c.connect(("127.0.0.1", port))
    c.sendall(f"GET /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())
    t0 = time.monotonic()                                     # never reads: the server's writes stall, then time out
    while time.monotonic() - t0 < 15 and "client_disconnected" not in [e[1] for e in s.events]:
        time.sleep(0.2)
    assert "client_disconnected" in [e[1] for e in s.events]  # was: the writer thread blocked forever
    c.close()


def test_bad_token_is_answered_and_closed_at_once(srv):
    _, port, _ = srv
    c = socket.create_connection(("127.0.0.1", port), timeout=3)
    c.sendall(b"GET /s/wrong/sess1.wav HTTP/1.1\r\nHost: x\r\n\r\n")
    data = b""
    t0 = time.monotonic()
    while True:
        b = c.recv(4096)
        if not b:
            break
        data += b
    assert data.startswith(b"HTTP/1.0 403") and time.monotonic() - t0 < 1.0


def test_bounded_list_keeps_the_newest_items_and_list_semantics():
    from btbridge.core.bounded import BoundedList
    b = BoundedList(100)
    for i in range(100_000):
        b.append(i)
    assert 100 <= len(b) <= 125 and b[-1] == 99_999 and b[-100:] == list(range(99_900, 100_000))
    assert b[:3] == [b[0], b[1], b[2]] and isinstance(b[-50:], list)


def test_long_running_audit_trails_are_bounded(srv):
    """Review M7: controller events, runtime timeline, stream events, LMS command audit, MQTT command log."""
    s, _, _ = srv
    from fakes import Rig
    from btbridge.app import BridgeRuntime, RuntimeConfig
    from btbridge.core.controller import Config
    from btbridge.lms.client import LmsClient
    r = Rig()
    for _ in range(100_000):
        r.ctl._ev("x")
        s._ev("y")
    c = LmsClient("http://127.0.0.1:9")
    for _ in range(100_000):
        c._audit("play", "p", "u")
        c.command_times.append((0.0, 0.0))
    rt = BridgeRuntime(RuntimeConfig(stream_port=0), Config(), log=lambda m: None)
    for _ in range(100_000):
        rt._clog("z")
    for lst in (r.ctl.events, s.events, c.commands_sent, c.command_times, rt.timeline, rt.controller.events):
        assert len(lst) <= 5000, len(lst)


def test_mqtt_command_log_is_bounded():
    from test_mqtt import bridge
    _, b = bridge()                                               # read-only default: every command is logged as ignored
    for _ in range(20_000):
        b._on_message("hifi-bridge/x/cmd/pairing", b"ON")
    assert len(b.commands) <= 500


# ---------------------------------------------------------------- L4: child processes back off and are reaped
def test_a_capture_child_that_keeps_dying_backs_off_and_every_child_is_reaped():
    import subprocess
    from btbridge.adapters.bluez.pcm import PipeWireCapture
    procs = []

    def popen(argv, **kw):
        p = subprocess.Popen(argv, **kw); procs.append(p); return p
    cap = PipeWireCapture(argv_for=lambda addr: ["sh", "-c", "exit 0"], popen=popen, retry_s=0.05)
    cap.start("AA:BB:CC:DD:EE:FF", lambda pcm: None)
    time.sleep(2.0)
    cap.stop()
    assert 2 <= cap.starts <= 12, cap.starts                  # was: a restart every retry_s (about 40 in 2 s)
    assert procs and all(p.returncode is not None for p in procs)          # waited for: no zombies
    assert all(p.stdout is None or p.stdout.closed for p in procs)         # no leaked pipe fds


def test_capture_stop_reaps_a_running_child():
    import subprocess
    from btbridge.adapters.bluez.pcm import PipeWireCapture
    procs = []

    def popen(argv, **kw):
        p = subprocess.Popen(argv, **kw); procs.append(p); return p
    cap = PipeWireCapture(argv_for=lambda addr: ["sh", "-c", "while :; do sleep 0.05; done"], popen=popen, retry_s=0.05, stall_s=30)
    cap.start("AA:BB:CC:DD:EE:FF", lambda pcm: None)
    assert wait_for(lambda: procs)
    cap.stop()
    assert procs[0].returncode is not None and procs[0].stdout.closed


def test_flac_encoder_close_reaps_the_encoder():
    import shutil
    from btbridge.stream.encoders import FlacEncoder
    if not shutil.which("flac"):
        pytest.skip("flac not installed in this image")
    e = FlacEncoder()
    e.encode(b"\0" * 4410 * 4)
    e.close()
    assert e.proc.returncode is not None and e.proc.stdout.closed and e.proc.stdin.closed


def wait_for(cond, t=3.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_connections_beyond_the_cap_are_refused(srv):
    _, port, _ = srv
    hold = [socket.create_connection(("127.0.0.1", port)) for _ in range(4)]
    time.sleep(0.1)
    extra = socket.create_connection(("127.0.0.1", port), timeout=3)
    assert extra.recv(100).startswith(b"HTTP/1.0 503")
    for h in hold:
        h.close()
