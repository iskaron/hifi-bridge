"""UNIT: systemd notify protocol (real unix datagram socket), watchdog behaviour, crash-recovery journal (real service, fake LMS)."""
import json
import os
import socket
import time

import pytest
from test_service import P1, Lmses, fake_tree, wait

from btbridge.service import ApplianceService
from btbridge.supervision import SessionJournal, SystemdNotifier, Watchdog
from btkit.sim_a2dp import SimulatedA2dpSource


@pytest.fixture
def notify_socket(tmp_path):
    path = str(tmp_path / "notify.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); s.bind(path); s.settimeout(2)
    yield path, s
    s.close()


def recv_all(s, n=1):
    out = []
    for _ in range(n):
        out.append(s.recv(4096).decode())
    return out


def test_notify_protocol_on_a_real_socket(notify_socket):
    path, s = notify_socket
    n = SystemdNotifier({"NOTIFY_SOCKET": path, "WATCHDOG_USEC": "4000000", "WATCHDOG_PID": str(os.getpid())})
    assert n.enabled and n.watchdog_s == 4.0
    assert n.notify(ready=1, status="running")
    assert recv_all(s) == ["READY=1\nSTATUS=running"]
    assert SystemdNotifier({}).notify(ready=1) is False                      # not under systemd: no-op
    other = str(os.getpid() + 1)                                                 # not this process (pytest may itself be PID 1)
    assert SystemdNotifier({"NOTIFY_SOCKET": path, "WATCHDOG_USEC": "1000", "WATCHDOG_PID": other}).watchdog_s is None


def test_watchdog_pings_only_while_healthy(notify_socket):
    path, s = notify_socket
    state = {"status": "ok"}
    w = Watchdog(lambda: {"status": state["status"], "checks": {"control_loop": state["status"] != "failed"}},
                 SystemdNotifier({"NOTIFY_SOCKET": path}), interval_s=0.1)
    w.start()
    msgs = recv_all(s, 3)
    assert msgs[0].startswith("READY=1") and any(m.startswith("WATCHDOG=1") for m in msgs[1:])
    state["status"] = "failed"; time.sleep(0.35)
    while True:                                                              # drain
        try:
            s.settimeout(0.05); last = s.recv(4096).decode()
        except socket.timeout:
            break
    pings_before = w.pings; time.sleep(0.35)
    assert w.pings == pings_before and w.withheld >= 2 and "failed: control_loop" in last
    state["status"] = "degraded"; time.sleep(0.3)
    assert w.pings > pings_before                                            # degraded (e.g. LMS down) is no reason to restart
    w.stop()


def test_a_crashing_health_check_counts_as_failed(notify_socket):
    path, s = notify_socket
    w = Watchdog(lambda: 1 / 0, SystemdNotifier({"NOTIFY_SOCKET": path}), interval_s=0.05)
    w.start(); time.sleep(0.3); w.stop()
    assert w.pings == 0 and w.withheld >= 2


def test_journal_roundtrip(tmp_path):
    j = SessionJournal(str(tmp_path))
    assert j.pending() is None
    j.record("02:00:00:00:00:01", "http://bridge/s/T/abc.wav")
    assert j.pending()["url"].endswith("abc.wav")
    j.clear(); assert j.pending() is None
    (tmp_path / "session.json").write_text("{broken"); assert j.pending() is None


def make(tmp_path, lmses):
    return ApplianceService(str(tmp_path / "data"), source=SimulatedA2dpSource(), stream_port=0, advertise_host="bridge", lms_factory=lmses,
                            monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)


def test_crashed_process_leaves_a_journal_and_the_next_start_releases_only_that_url(tmp_path):
    lmses = Lmses()
    a = make(tmp_path, lmses); a.store.update({"lms_host": "10.0.0.5", "target_player": P1}); a.start()
    a.source.connect(); a.source.play()
    lms = lmses("http://10.0.0.5:9000")
    assert wait(lambda: lms.commands and lms.commands[-1][0] == "play")
    url = lms.commands[-1][2]
    assert json.load(open(tmp_path / "data" / "session.json"))["url"] == url
    # 'crash': no stop(), no release - just abandon the process state
    a.runtime._stop.set(); a._stop.set()
    b = make(tmp_path, lmses)
    b.start()                                                                     # start() recovers before anything else
    assert ("release", P1, url) in lms.commands and not (tmp_path / "data" / "session.json").exists()
    b.stop()


def test_recovery_is_harmless_when_a_foreign_source_plays_and_waits_for_unreachable_lms(tmp_path):
    lmses = Lmses()
    j = SessionJournal(str(tmp_path / "data")); os.makedirs(tmp_path / "data", exist_ok=True)
    j.record(P1, "http://bridge:8090/s/T/old.wav")
    b = make(tmp_path, lmses); b.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    lms = lmses("http://10.0.0.5:9000"); lms.user_plays(P1, "http://radio/x"); lms.down = True
    assert b.recover_stale_session() == "pending" and j.pending()
    lms.down = False
    assert b.recover_stale_session() == "released"
    assert lms.players[P1]["url"] == "http://radio/x" and lms.players[P1]["mode"] == "play"     # URL-addressed: radio untouched


def test_normal_release_clears_the_journal(tmp_path):
    lmses = Lmses()
    a = make(tmp_path, lmses); a.store.update({"lms_host": "10.0.0.5", "target_player": P1}); a.start()
    a.source.connect(); a.source.play()
    lms = lmses("http://10.0.0.5:9000")
    assert wait(lambda: (tmp_path / "data" / "session.json").exists())
    a.source.pause()                                                              # drop_restart: our item is released at once
    assert wait(lambda: not (tmp_path / "data" / "session.json").exists())
    a.stop()


STOPPER = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from btbridge.supervision import StopSignal
stop = StopSignal()
print("ready", flush=True)
stop.wait(poll=0.05)
print("stopped", flush=True)
'''


def test_stop_signal_survives_back_to_back_signals_in_real_processes():
    """Regression (found by the mDNS lab test): a handler that called threading.Event.set() deadlocked when a second SIGTERM arrived
    while the first handler held the Event's lock (pkill matched both tini and the process). The handler must take no lock."""
    import os
    import signal
    import subprocess
    import sys
    bridge = os.path.join(os.path.dirname(__file__), "..", "..", "bridge")
    for i in range(30):
        p = subprocess.Popen([sys.executable, "-c", STOPPER, bridge], stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "ready"
        for _ in range(2 + i % 4):                       # two to five signals, back to back
            os.kill(p.pid, signal.SIGTERM)
        try:
            assert p.wait(timeout=5) == 0 and p.stdout.read().strip() == "stopped"
        finally:
            if p.poll() is None:
                p.kill()
                raise AssertionError(f"process hung after back-to-back SIGTERMs (iteration {i})")


def test_stop_signal_handler_takes_no_lock():
    import inspect
    from btbridge.supervision import StopSignal
    src = inspect.getsource(StopSignal._handler)
    assert ".set(" not in src and "acquire" not in src and "with " not in src and "print" not in src
