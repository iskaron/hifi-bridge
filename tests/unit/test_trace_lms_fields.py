"""UNIT (scripted JSON-RPC peer on loopback; fake LMS for the controller; fake clock for the trace): the G1-S diagnostics that tell
from the bridge's side WHY Lyrion stopped reading - Lyrion's own ``waitingToPlay`` (group controller BUFFERING, e.g. rebuffering
after a member's output underrun: Slim::Player::StreamingController::_Rebuffer), whether the last poll of the target failed or found
the target player disconnected, and the connection state / Wi-Fi signal of every member of the sync group. All read-only and only
when the opt-in trace is on (docs/STREAM_TRACE.md). Real Lyrion behaviour: scripts/exp_group_member_datastall.sh (INTEGRATION)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakes import PLAYER, Rig

from btbridge.lms.client import LmsClient, Observation
from btbridge.stream.trace import StreamTrace


@pytest.fixture
def lms():
    answers, seen = {}, []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            player, cmd = body["params"]
            seen.append((player, cmd))
            result = answers.get((player, cmd[0]), answers.get(cmd[0], {}))
            data = json.dumps({"id": 1, "result": result}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield type("L", (), dict(url=f"http://127.0.0.1:{srv.server_address[1]}", answers=answers, seen=seen))
    srv.shutdown(); srv.server_close()


def test_observe_reports_lyrions_waiting_to_play_flag(lms):
    lms.answers["status"] = {"player_connected": 1, "mode": "play", "time": 31.7, "waitingToPlay": 1}
    assert LmsClient(lms.url).observe(PLAYER).waiting is True
    lms.answers["status"] = {"player_connected": 1, "mode": "play", "time": 31.7}
    assert LmsClient(lms.url).observe(PLAYER).waiting is False


def test_group_status_lists_every_sync_member_without_ids_or_addresses(lms):
    a, b, c = "02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:03"
    lms.answers[(a, "status")] = {"player_name": "Küche", "player_connected": 1, "signalstrength": 78, "player_ip": "10.0.0.5:1",
                                  "sync_master": a, "sync_slaves": f"{b},{c}"}
    lms.answers[(b, "status")] = {"player_name": "Arbeitszimmer", "player_connected": 0, "signalstrength": 0}
    lms.answers[(c, "status")] = {"player_name": "esparagus-01", "player_connected": 1, "signalstrength": 55}
    got = LmsClient(lms.url).group_status(a)
    assert got == [{"name": "Küche", "connected": True, "signal": 78, "master": True},
                   {"name": "Arbeitszimmer", "connected": False, "signal": 0, "master": False},
                   {"name": "esparagus-01", "connected": True, "signal": 55, "master": False}]
    assert [p for p, cmd in lms.seen] == [a, b, c] and all(cmd[0] == "status" for _, cmd in lms.seen)   # read-only, one per member


def test_group_status_marks_the_master_when_the_target_is_a_slave(lms):
    """Tests 2/3 of the G1-S round 2 had the target (Arbeitszimmer) first in the list; which member is the sync MASTER decides whose
    elapsed time Lyrion reports and which member's buffering gives up first (docs/STREAM_TRACE.md)."""
    a, b = "02:00:00:00:00:01", "02:00:00:00:00:02"
    lms.answers[(b, "status")] = {"player_name": "Slave", "player_connected": 1, "sync_master": a, "sync_slaves": b}
    lms.answers[(a, "status")] = {"player_name": "Master", "player_connected": 1, "sync_master": a, "sync_slaves": b}
    got = LmsClient(lms.url).group_status(b)
    assert [(m["name"], m["master"]) for m in got] == [("Slave", False), ("Master", True)]


def test_group_status_of_an_unsynced_or_unknown_player(lms):
    lms.answers[(PLAYER, "status")] = {"player_name": "Solo", "player_connected": 1}
    assert LmsClient(lms.url).group_status(PLAYER) == [{"name": "Solo", "connected": True, "signal": 0, "master": False}]
    assert LmsClient(lms.url).group_status("02:00:00:00:00:99") == []


def test_the_controller_records_why_the_last_poll_of_the_target_did_not_give_an_observation():
    r = Rig(); r.connect_and_play(); r.run(2)
    assert r.ctl.last_poll[1] == "ok"
    r.lms.players[PLAYER]["connected"] = False; r.run(2)                 # the target player dropped its Slimproto connection
    assert r.ctl.last_poll[1] == "target-disconnected"
    r.lms.players[PLAYER]["connected"] = True; r.lms.down = True; r.run(2)
    assert r.ctl.last_poll[1] == "lms-unreachable"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _trace(obs=None, poll=None):
    lines, clk = [], Clock()
    tr = StreamTrace(lines.append, clock=clk)
    tr.lms_observation = (lambda: obs) if obs else None
    tr.lms_poll = (lambda: poll) if poll else None
    tr.ingress(b"\x01\x00" * 4410)
    return tr, clk, lines


def test_the_summary_flags_lyrion_waiting_and_a_failed_poll():
    tr, clk, _ = _trace(obs=(99.5, Observation(mode="play", time=31.7, waiting=True), True), poll=(99.0, "target-disconnected"))
    line = tr.snapshot()
    assert "lms: play time=31.7" in line and " WAITING" in line and "poll=target-disconnected 1.0s ago" in line
    tr, _, _ = _trace(obs=(99.5, Observation(mode="play", time=5.0), True), poll=(99.5, "ok"))
    line = tr.snapshot()
    assert "WAITING" not in line and "poll=" not in line


def test_the_summary_shows_a_failed_poll_even_without_any_observation():
    tr, _, _ = _trace(poll=(97.0, "lms-unreachable"))
    assert "| lms: poll=lms-unreachable 3.0s ago" in tr.snapshot()


def test_the_member_poll_runs_in_its_own_thread_keeps_the_last_result_on_failure_and_shows_its_age():
    tr, clk, _ = _trace()
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise OSError("lms down")
        return [{"name": "Küche", "connected": True, "signal": 78, "master": True}, {"name": "Arbeits zimmer|x", "connected": False, "signal": 0}]
    stop = threading.Event()
    tr.start_member_poll(stop, fetch, period_s=0.02)
    deadline = time.time() + 5
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    line = tr.snapshot()
    assert "| members: Küche:ok sig=78(master), Arbeits_zimmer_x:DISC" in line                # names sanitised, no separators injected
    assert "old)" not in line
    clk.t += 10                                                                       # the poll has failed for 10 s
    tr.ingress(b"\x01\x00" * 10)
    assert "(10s old)" in tr.snapshot()


def test_without_the_trace_the_bridge_never_polls_group_members(monkeypatch):
    from btbridge import app
    started = []
    monkeypatch.setattr(app.StreamTrace, "start_member_poll", lambda *a, **k: started.append(a))
    monkeypatch.delenv("HIFI_STREAM_TRACE", raising=False)
    assert app.enabled_from_env() is False and started == []
