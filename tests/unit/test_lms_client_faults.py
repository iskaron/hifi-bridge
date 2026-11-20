"""UNIT (a scripted JSON-RPC peer on a loopback socket; the REAL Lyrion is tests/integration): the LMS client when the answers or the name
service are not what a healthy home network gives it - an unknown player, a server that lists non-players, a name that resolves only to
IPv6, a lookup that fails or never answers. Each case must become LmsUnreachable or an empty answer, never an exception from deep inside
the control loop. Coverage package 7b-3."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from btbridge.lms.client import LmsClient, LmsUnreachable, _Resolver


@pytest.fixture
def lms():
    answers, seen = {}, []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            player, cmd = body["params"]
            seen.append((player, cmd, self.headers.get("Host")))
            result = answers.get(cmd[0], {})
            data = json.dumps({"id": 1, "result": result}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield type("L", (), dict(port=srv.server_address[1], answers=answers, seen=seen))
    srv.shutdown(); srv.server_close()


def test_an_unknown_player_is_reported_as_unknown_not_as_an_error(lms):
    lms.answers["status"] = {}                                               # LMS answers an empty result for ids it does not know
    obs = LmsClient(f"http://127.0.0.1:{lms.port}").observe("02:00:00:00:00:99")
    assert obs.player_known is False and obs.player_connected is False and obs.mode == "stop" and not obs.playing_something


def test_players_that_are_not_audio_players_and_empty_sync_groups_are_left_out(lms):
    lms.answers["players"] = {"players_loop": [
        {"playerid": "aa", "name": "Kitchen", "modelname": "SqueezeLite", "connected": 1, "power": 1},
        {"playerid": "bb", "name": "Alarm clock service", "isplayer": 0},                       # something LMS lists that cannot play
    ]}
    lms.answers["syncgroups"] = {"syncgroups_loop": [{"sync_members": "", "sync_member_names": ""},
                                                     {"sync_members": "aa,cc", "sync_member_names": "Kitchen,Study"}]}
    c = LmsClient(f"http://127.0.0.1:{lms.port}")
    assert [p["id"] for p in c.players()] == ["aa"]
    assert c.sync_groups() == [{"members": ["aa", "cc"], "names": ["Kitchen", "Study"]}]


def test_waking_a_player_is_recorded_in_the_audit_trail_and_sent(lms):
    c = LmsClient(f"http://127.0.0.1:{lms.port}")
    c.power_on("02:00:00:00:00:01")
    assert any(cmd[:2] == ["power", 1] for _, cmd, _ in lms.seen) and c.commands_sent and "power_on" in str(c.commands_sent[-1])


def test_a_https_server_keeps_its_name_for_the_certificate_check_and_gets_no_resolver():
    assert LmsClient("https://lyrion.example:9000")._resolver is None
    assert LmsClient("http://lyrion.example:9000")._resolver is not None
    assert LmsClient("http://192.0.2.7:9000")._resolver is None                 # an IP literal needs no lookup


def test_a_name_that_resolves_only_to_ipv6_is_still_used():
    def only_v6(host, port, family, kind):
        return [(socket.AF_INET6, kind, 6, "", ("2001:db8::7", port, 0, 0))]
    assert _Resolver("lyrion.example", 9000, resolve=only_v6).address() == "2001:db8::7"


def test_a_lookup_that_fails_or_finds_nothing_is_unreachable_with_the_reason():
    def refuse(host, port, family, kind):
        raise socket.gaierror(-2, "Name or service not known")
    with pytest.raises(LmsUnreachable, match="name lookup for lyrion.example failed: .*Name or service not known"):
        _Resolver("lyrion.example", 9000, resolve=refuse).address()
    with pytest.raises(LmsUnreachable, match="no address"):
        _Resolver("lyrion.example", 9000, resolve=lambda *a: []).address()


def test_a_connection_failure_makes_the_client_look_the_name_up_again():
    calls = []

    def resolve(host, port, family, kind):
        calls.append(host)
        return [(socket.AF_INET, kind, 6, "", ("127.0.0.1", 1))]                  # nothing listens on port 1
    c = LmsClient("http://lyrion.example:1", resolve=resolve)
    for _ in range(2):
        with pytest.raises(LmsUnreachable):
            c.server_version()
    assert calls == ["lyrion.example", "lyrion.example"]                            # not cached after a failure: DHCP may have moved it
