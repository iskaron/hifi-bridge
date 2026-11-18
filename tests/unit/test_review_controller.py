"""UNIT (fake clock, FAKE LMS that blocks inside calls, real threads): review H2 - LMS I/O runs outside the controller's state
lock, and every start is revalidated after its LMS call, so a slow LMS neither blocks status/health nor breaks the ownership
rules (session identity, never stop unrelated playback). Label: UNIT/SIMULATED."""
import threading
import time

import pytest
from fakes import PLAYER, Rig

from btbridge.lms.client import LmsUnreachable

RADIO = "http://radio.example/stream"
P2 = "02:00:00:00:00:02"


class Gate:
    """Makes chosen FakeLms methods block until released, recording that the call has started."""

    def __init__(self, lms, method, snapshot=False):
        self.entered, self.release_ev = threading.Event(), threading.Event()
        self.lms, self.method, self.snapshot = lms, method, snapshot
        self.orig = getattr(lms, method)
        self.armed = True
        setattr(lms, method, self._call)

    def _call(self, *a):
        if not self.armed:
            return self.orig(*a)
        self.armed = False
        before = self.orig(*a) if self.snapshot else None      # an observation taken now, answered late ("late event")
        self.entered.set()
        assert self.release_ev.wait(10)
        return before if self.snapshot else self.orig(*a)

    def open(self):
        self.release_ev.set()


def in_thread(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True)
    t.start()
    return t


def timed(fn, *a):
    t0 = time.monotonic()
    out = fn(*a)
    return out, time.monotonic() - t0


def test_status_and_events_do_not_wait_for_a_slow_lms_call():
    r = Rig(); r.connect_and_play(); r.run(2)
    g = Gate(r.lms, "observe")
    r.clock.advance(1); t = in_thread(r.ctl.tick)
    assert g.entered.wait(2)
    st, dt = timed(r.ctl.status)
    assert dt < 0.2 and st["target_ownership"] == "owned"
    g.open(); t.join(5); assert not t.is_alive()


def test_disconnect_while_play_is_in_flight_removes_exactly_the_new_item():
    r = Rig(); assert r.ctl.on_connected("phone")
    g = Gate(r.lms, "play")
    t = in_thread(r.ctl.on_media, True)
    assert g.entered.wait(2)
    _, dt = timed(r.ctl.on_disconnected, "phone")
    assert dt < 0.2                                          # was: blocked behind the LMS call holding the lock
    g.open(); t.join(5)
    url = r.cmds("play")[0][2]
    assert r.cmds("release") == [("release", PLAYER, url)]  # the unwanted item - and only it - is removed
    assert r.ctl.session is None and r.ctl.ownership == "none" and r.lms.players[PLAYER]["mode"] == "stop"
    assert any(n == "start_superseded" for _, n, _ in r.ctl.events)


def test_disconnect_during_a_slow_start_never_touches_a_radio_started_meanwhile():
    r = Rig(); assert r.ctl.on_connected("phone")
    g = Gate(r.lms, "play")
    t = in_thread(r.ctl.on_media, True)
    assert g.entered.wait(2)
    r.ctl.on_disconnected("phone")
    g.open(); t.join(5)
    r.lms.user_plays(PLAYER, RADIO); r.run(5)
    assert r.lms.players[PLAYER]["url"] == RADIO and r.lms.players[PLAYER]["mode"] == "play"


def test_target_change_while_play_is_in_flight_moves_to_the_new_target():
    r = Rig(); r.lms.add(P2); assert r.ctl.on_connected("phone")
    g = Gate(r.lms, "play")
    t = in_thread(r.ctl.on_media, True)
    assert g.entered.wait(2)
    _, dt = timed(r.ctl.set_target, P2)
    assert dt < 0.2
    g.open(); t.join(5)
    old_url = r.cmds("play")[0][2]
    assert ("release", PLAYER, old_url) in r.lms.commands and r.lms.players[PLAYER]["mode"] == "stop"
    assert r.ctl.ownership == "owned" and r.ctl.cfg.target_player == P2 and r.lms.players[P2]["url"] == r.url != old_url


def test_audio_disabled_while_play_is_in_flight_leaves_nothing_playing():
    r = Rig(); assert r.ctl.on_connected("phone")
    g = Gate(r.lms, "play")
    t = in_thread(r.ctl.on_media, True)
    assert g.entered.wait(2)
    r.ctl.set_audio_allowed(False)
    g.open(); t.join(5)
    assert r.ctl.session is None and r.lms.players[PLAYER]["mode"] == "stop" and r.ctl.device == "phone"


def test_late_observation_from_before_a_pause_and_resume_does_not_end_the_new_session():
    """Poll observes (old session playing), answer arrives late; meanwhile the phone paused (item removed) and resumed (new
    session). The stale answer must not count as 'stream died' or 'ownership lost'."""
    r = Rig(); r.connect_and_play(); r.run(2); sid1 = r.ctl.session.sid
    r.clock.advance(1)
    g = Gate(r.lms, "observe", snapshot=True)
    t = in_thread(r.ctl.tick)
    assert g.entered.wait(2)
    r.ctl.on_media(False); r.ctl.on_media(True)            # park + fresh session with a new id
    sid2 = r.ctl.session.sid
    g.open(); t.join(5)
    assert sid2 != sid1 and r.ctl.session and r.ctl.session.sid == sid2 and r.ctl.ownership == "owned"
    assert not [n for _, n, _ in r.ctl.events if n in ("ownership_lost", "stream_died")]
    assert r.lms.players[PLAYER]["url"] == r.url


def test_late_observation_after_a_server_change_is_discarded():
    r = Rig(); r.connect_and_play(); r.run(2)
    old = r.lms
    g = Gate(old, "observe")
    r.clock.advance(1); t = in_thread(r.ctl.tick)
    assert g.entered.wait(2)
    from fakes import FakeLms
    new = FakeLms(); new.add(PLAYER); new.down = True
    r.ctl.change_server(new)
    g.open(); t.join(5)
    assert r.ctl.reachability == "unreachable" or r.ctl.reachability == "unknown"   # never 'reachable' from the OLD server


def test_tick_makes_no_further_lms_calls_after_an_unreachable_one():
    calls = []
    r = Rig(); r.lms.down = True; r.connect_and_play()
    orig = r.lms.observe
    r.lms.observe = lambda pid: (calls.append(pid), orig(pid))[1]
    for _ in range(8):
        n = len(calls); r.clock.advance(0.5); r.ctl.tick()
        assert len(calls) - n <= 1                           # one timeout per tick at most, not poll + retry


class SlowDeadLms:
    """Every call blocks DELAY s, then fails (a dead LMS behind a slow resolver)."""
    DELAY = 10.0

    def __init__(self, url):
        pass

    def _slow(self, *a, **k):
        time.sleep(self.DELAY)
        raise LmsUnreachable("timed out")

    observe = play = release = power_on = volume_delta = server_version = targets = _slow


class Notifier:
    watchdog_s = 2.0

    def __init__(self):
        self.pings, self.status = [], []

    def notify(self, **f):
        if f.get("watchdog"):
            self.pings.append(time.monotonic())
        return True


@pytest.mark.slow
def test_watchdog_keeps_pinging_while_every_lms_call_takes_10s(tmp_path):
    from btbridge.service import ApplianceService
    from btbridge.supervision import Watchdog
    from btbridge.system import SystemMonitor
    from btkit.sim_a2dp import SimulatedA2dpSource
    sim = SimulatedA2dpSource()
    d = str(tmp_path / "data")
    svc = ApplianceService(d, source=sim, stream_port=0, advertise_host="bridge", lms_factory=SlowDeadLms, echo=None,
                           auth_iterations=1000, monitor=SystemMonitor(thermal=None, data_dir=d))
    svc.store.update({"lms_host": "10.0.0.5", "target_player": PLAYER})
    svc.start()
    try:
        rt = svc.runtime
        rt._on_connect("11:22:33:44:55:66"); rt._on_transport("active"); rt._on_avrcp("playing")
        n = Notifier()
        wd = Watchdog(svc.health, n, interval_s=1.0)
        latencies = []
        orig = svc.health

        def health():
            h, dt = timed(orig)
            latencies.append(dt)
            return h
        wd.health = health
        wd.start()
        time.sleep(25)
        wd._stop.set()
        assert wd.withheld == 0 and wd.pings >= 20
        assert max(latencies) < 0.5
        assert svc.health()["status"] == "degraded"          # LMS outage = degraded, never 'failed' (no restart)
        _, dt = timed(svc.status)
        assert dt < 0.5
    finally:
        svc.stop()


def test_a_call_that_never_returns_is_still_a_hang():
    from btbridge.core.controller import IoTracker
    io = IoTracker()
    ev = threading.Event()
    idents = []
    t = threading.Thread(target=lambda: (idents.append(threading.get_ident()), io.call(ev.wait, 5)), daemon=True)
    t.start(); time.sleep(0.1)
    now = time.monotonic()
    assert io.alive(idents[0], heartbeat=now - 100, stale_s=5, hang_s=60, now=now)         # inside a bounded call: busy
    assert not io.alive(idents[0], heartbeat=now - 100, stale_s=5, hang_s=60, now=now + 61)   # beyond any timeout: hung
    assert not io.alive(12345, heartbeat=now - 100, stale_s=5, hang_s=60, now=now)          # no beat, no call: hung
    ev.set(); t.join()


# ---------------------------------------------------------------- H2: LMS name resolution is bounded and cached
class _Lms:
    """Minimal real HTTP JSON-RPC endpoint (records the Host header)."""
    def __init__(self):
        import http.server
        import json as _json
        hosts = self.hosts = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                hosts.append(self.headers.get("Host"))
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = _json.dumps({"result": {"version": "9.1.2"}}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def test_a_hanging_name_lookup_fails_fast_and_is_not_repeated_per_call():
    from btbridge.lms.client import LmsClient
    calls = []

    def slow_resolve(*a, **k):
        calls.append(a[0]); time.sleep(10)
        raise OSError("no answer")
    c = LmsClient("http://lms.home.arpa:9000", resolve=slow_resolve, resolve_timeout=0.5)
    for _ in range(3):
        t0 = time.monotonic()
        with pytest.raises(LmsUnreachable):
            c.observe(PLAYER)
        assert time.monotonic() - t0 < 1.5
    assert len(calls) == 1                                   # one lookup in flight, not one blocked thread per call


def test_resolved_address_is_cached_and_the_host_header_keeps_the_name():
    import socket
    from btbridge.lms.client import LmsClient
    srv = _Lms(); calls = []

    def resolve(host, port, *a, **k):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    c = LmsClient(f"http://lms.home.arpa:{srv.port}", resolve=resolve)
    assert c.server_version() == "9.1.2" and c.server_version() == "9.1.2"
    assert calls == ["lms.home.arpa"] and srv.hosts == [f"lms.home.arpa:{srv.port}"] * 2
    srv.httpd.shutdown(); srv.httpd.server_close()
    with pytest.raises(LmsUnreachable):
        c.server_version()                                   # a connection failure drops the cached address ...
    with pytest.raises(LmsUnreachable):
        c.server_version()
    assert calls == ["lms.home.arpa", "lms.home.arpa"]       # ... so the name is looked up again (DHCP change)


def test_ip_literals_are_never_resolved():
    from btbridge.lms.client import LmsClient
    srv = _Lms()
    c = LmsClient(f"http://127.0.0.1:{srv.port}", resolve=lambda *a, **k: pytest.fail("resolved an IP literal"))
    assert c.server_version() == "9.1.2"
    srv.httpd.shutdown(); srv.httpd.server_close()
