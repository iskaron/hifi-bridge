"""Demo console HTTP API: routing, rebinding/CSRF guards, uploads, the radio stream and the monitor relay (with fakes behind it)."""
import io
import json
import socket
import urllib.error
import urllib.request
import wave

import numpy as np
import pytest

from hifidemo import console as CON
from hifidemo import monitor as MON
from hifidemo.phones import Library, Phone, PhoneStore

from test_phones import FakeAir, FakeLink


class FakeLms:
    def __init__(self):
        self.calls = []

    def players(self):
        return [{"id": "02:00:00:00:01:01", "name": "Wohnzimmer", "connected": True, "mode": "stop", "title": "", "volume": 50,
                 "sync_master": None, "sync_slaves": []}]

    def play_radio(self, pid, url): self.calls.append(("radio", pid, url))
    def stop(self, pid): self.calls.append(("stop", pid))
    def sync(self, ids): self.calls.append(("sync", ids))
    def unsync(self, ids): self.calls.append(("unsync", ids))


@pytest.fixture
def con(tmp_path):
    air = FakeAir()
    lib = Library(str(tmp_path / "up"))
    store = PhoneStore(str(tmp_path / "phones.json"))
    phones = {"a": Phone("a", "Annas Smartphone", "02:A1:00:00:00:01", air, lambda m: FakeLink(), store, lib)}
    mon = MON.Broadcaster()
    msrv = MON.serve(mon, 0, host="127.0.0.1", info={"player": "Wohnzimmer"})
    lms = FakeLms()
    c = CON.Console(phones, lib, lms, rooms={"Wohnzimmer": ("127.0.0.1", msrv.server_address[1])},
                    radio_url="http://console:8100/radio.wav", links={"bridge_http": "http://127.0.0.1:8080"})
    srv = CON.serve(c, 0, host="127.0.0.1")
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, c, air, lms, mon
    srv.shutdown(); msrv.shutdown(); c.shutdown()


def call(base, method, path, body=None, headers=None, raw=None):
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else raw
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_pages_static_and_product_stylesheet(con):
    base, *_ = con
    for page in ("/", "/phones", "/monitor"):
        s, h, b = call(base, "GET", page)
        assert s == 200 and h["Content-Type"].startswith("text/html") and b"SIMULATED" in b.upper()
        assert "default-src 'self'" in h["Content-Security-Policy"]
    s, h, b = call(base, "GET", "/static/app.css")             # the product's own stylesheet: one corporate identity
    assert s == 200 and b"Hi-Fi & elegant" in b
    assert call(base, "GET", "/static/../../etc/passwd")[0] == 404


def test_state_lists_phones_players_and_links(con):
    base, *_ = con
    s, _, b = call(base, "GET", "/api/state")
    st = json.loads(b)
    assert s == 200 and st["phones"][0]["name"] == "Annas Smartphone" and st["phones"][0]["simulated"] is True
    assert st["players"][0]["name"] == "Wohnzimmer" and st["links"]["bridge_http"] == "http://127.0.0.1:8080"
    assert st["rooms"] == ["Wohnzimmer"]


def test_phone_actions_are_routed(con):
    base, c, air, *_ = con
    air.visible = [{"name": "Hi-Fi Bridge", "address": "x"}]
    assert json.loads(call(base, "POST", "/api/phones/a/scan", {})[2])["visible"] == air.visible
    assert json.loads(call(base, "POST", "/api/phones/a/pair", {})[2])["ok"]
    assert json.loads(call(base, "POST", "/api/phones/a/connect", {})[2])["ok"]
    assert json.loads(call(base, "POST", "/api/phones/a/volume", {"value": 40})[2])["ok"]
    assert ("volume", {"mac": "02:A1:00:00:00:01", "volume": 40}) in air.calls
    assert call(base, "POST", "/api/phones/a/explode", {})[0] == 404
    assert call(base, "POST", "/api/phones/zz/play", {})[0] == 404


def test_guards_against_rebinding_and_cross_site_requests(con):
    base, *_ = con
    assert call(base, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 421
    assert call(base, "POST", "/api/phones/a/scan", raw=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    assert call(base, "POST", "/api/phones/a/scan", {}, headers={"Origin": "http://evil.example"})[0] == 403


def test_upload_decodes_into_the_library(con):
    base, c, *_ = con
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes((np.ones(44100 * 2, dtype="<i2") * 1000).tobytes())
    s, _, b = call(base, "PUT", "/api/library?name=Mein%20Lied.wav", raw=buf.getvalue(), headers={"X-Hifi-Demo": "1"})
    assert s == 200 and json.loads(b)["item"]["title"] == "Mein Lied"
    assert call(base, "PUT", "/api/library?name=x.wav", raw=b"x")[0] == 415        # no custom header -> refused (CSRF)
    s, _, b = call(base, "PUT", "/api/library?name=x.txt", raw=b"x", headers={"X-Hifi-Demo": "1"})
    assert s == 400 and b"unsupported" in b


def test_lms_helpers(con):
    base, c, air, lms, _ = con
    call(base, "POST", "/api/lms/radio", {"player": "02:00:00:00:01:01"})
    call(base, "POST", "/api/lms/sync", {"players": ["p1", "p2"]})
    call(base, "POST", "/api/lms/unsync", {"players": ["p2"]})
    call(base, "POST", "/api/lms/stop", {"player": "p1"})
    assert lms.calls == [("radio", "02:00:00:00:01:01", "http://console:8100/radio.wav"), ("sync", ["p1", "p2"]),
                         ("unsync", ["p2"]), ("stop", "p1")]


def test_radio_stream_is_an_endless_wav_from_the_live_position(con):
    base, *_ = con
    s = socket.create_connection((base.split("//")[1].split(":")[0], int(base.rsplit(":", 1)[1])), timeout=5)
    s.sendall(b"GET /radio.wav HTTP/1.1\r\nHost: console:8100\r\n\r\n")
    data = b""
    while len(data) < 44 + 44100 * 4:
        data += s.recv(65536)
    head, body = data.split(b"\r\n\r\n", 1)
    assert b"200" in head.split(b"\r\n")[0] and b"audio/x-wav" in head
    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE"
    s.close()


def test_monitor_relay_passes_player_frames_through(con):
    base, c, air, lms, mon = con
    host, port = base.split("//")[1].split(":")
    s = socket.create_connection((host, int(port)), timeout=5)
    s.sendall(b"GET /stream/Wohnzimmer HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    f = s.makefile("rb")
    status = f.readline()
    assert b"200" in status
    while f.readline() not in (b"\r\n", b""):
        pass
    import time
    for _ in range(100):
        if mon.listeners:
            break
        time.sleep(0.02)
    mon.publish(1234.5, b"\x01\x00\x02\x00")
    p = MON.FrameParser()
    got = []
    while not got:
        got = p.feed(f.read1(4096))
    assert got[0] == (1234.5, b"\x01\x00\x02\x00")
    s.close()
    assert call(base, "GET", "/stream/Keller")[0] == 404
