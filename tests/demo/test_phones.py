"""Virtual phone model: what a phone does over the air, and how it reacts to what the bridge does. The radio side is faked here; the
real virtual bluetoothd + product adapter are covered by test_vbluez.py and the stack test."""
import time

import pytest

from hifidemo import music
from hifidemo.phones import Library, Phone, PhoneStore

MAC = "02:A1:00:00:00:01"


class FakeAir:
    def __init__(self):
        self.calls, self.visible, self.results, self.events, self.seq = [], [], {}, [], 0

    def get(self, path, **q):
        if path == "/air/scan":
            return {"visible": self.visible}
        if path == "/air/device":
            since = int(q.get("since", 0))
            return {"device": None, "events": [e for e in self.events if e["seq"] > since]}
        raise AssertionError(path)

    def post(self, action, **body):
        self.calls.append((action, body))
        return {"result": self.results.get(action, "ok" if action not in ("pair", "connect") else
                                           {"pair": "paired", "connect": "connected"}[action])}

    def push_event(self, kind):
        self.seq += 1
        self.events.append({"seq": self.seq, "kind": kind, "detail": ""})

    def actions(self):
        return [a for a, _ in self.calls]


class FakeLink:
    def __init__(self):
        self.sent, self.closed = bytearray(), False

    def send(self, data):
        self.sent += data

    def close(self):
        self.closed = True


@pytest.fixture
def phone(tmp_path):
    air, links = FakeAir(), []

    def link_factory(mac):
        links.append(FakeLink())
        return links[-1]
    lib = Library(str(tmp_path / "uploads"))
    p = Phone("a", "Annas Smartphone", MAC, air, link_factory, PhoneStore(str(tmp_path / "phones.json")), lib, suspend_delay=0.2)
    yield p, air, links
    p.shutdown()


def paired_and_connected(p, air):
    air.visible = [{"name": "Hi-Fi Bridge", "address": "02:00:00:B7:1D:6E"}]
    p.scan(); assert p.pair()["ok"]
    assert p.connect()["ok"]


def test_pairing_needs_a_visible_bridge(phone):
    p, air, _ = phone
    p.scan()
    assert p.pair() == {"ok": False, "message": "not_visible"}
    air.visible = [{"name": "Hi-Fi Bridge", "address": "02:00:00:B7:1D:6E"}]
    assert p.scan() == air.visible
    assert p.pair()["ok"] and p.snapshot()["paired"] and p.snapshot()["bridge_name"] == "Hi-Fi Bridge"
    air.results["pair"] = "rejected"
    assert p.pair() == {"ok": False, "message": "rejected"}


def test_connect_reports_refusal_by_the_bridge(phone):
    p, air, _ = phone
    air.visible = [{"name": "Hi-Fi Bridge", "address": "x"}]
    p.scan(); p.pair()
    air.results["connect"] = "refused"
    assert p.connect() == {"ok": False, "message": "refused"}
    assert not p.snapshot()["connected"]
    air.results["connect"] = "not_paired"
    assert p.connect() == {"ok": False, "message": "pair_again"} and not p.snapshot()["paired"]


def test_play_pause_resume_follow_the_a2dp_avrcp_sequence(phone):
    p, air, links = phone
    paired_and_connected(p, air)
    air.calls.clear()
    assert p.play()["ok"]
    seq = air.actions()
    assert seq[:4] == ["track", "transport", "transport", "status"]
    assert [b["state"] for a, b in air.calls if a == "transport"] == ["pending", "active"]
    time.sleep(0.25)
    assert len(links[-1].sent) > 0.15 * music.RATE * 4             # real-time audio flows while playing
    air.calls.clear()
    p.pause()
    assert air.calls[0] == ("status", {"mac": MAC, "status": "paused"})
    assert ("transport", {"mac": MAC, "state": "idle"}) not in air.calls     # A2DP suspends a little later, like a real phone
    time.sleep(0.35)
    assert ("transport", {"mac": MAC, "state": "idle"}) in air.calls
    sent = len(links[-1].sent); time.sleep(0.1)
    assert len(links[-1].sent) == sent                              # no audio while paused
    pos = p.snapshot()["position_s"]
    p.play(); time.sleep(0.1)
    assert p.snapshot()["position_s"] >= pos                        # resume continues the track


def test_track_change_sends_new_metadata_and_keeps_streaming(phone):
    p, air, links = phone
    paired_and_connected(p, air)
    p.play()
    first = p.snapshot()["track"]["title"]
    air.calls.clear()
    p.next_track()
    assert p.snapshot()["track"]["title"] != first and p.snapshot()["status"] == "playing"
    assert air.calls[0][0] == "track" and air.calls[0][1]["title"] == p.snapshot()["track"]["title"]
    p.prev_track()
    assert p.snapshot()["track"]["title"] == first


def test_volume_is_absolute_avrcp_volume(phone):
    p, air, _ = phone
    paired_and_connected(p, air)
    p.set_volume(64)
    assert ("volume", {"mac": MAC, "volume": 64}) in air.calls
    assert p.snapshot()["volume"] == 64
    p.set_volume(500)
    assert p.snapshot()["volume"] == 127


def test_notification_opens_the_transport_without_changing_the_player_status(phone):
    p, air, links = phone
    paired_and_connected(p, air)
    air.calls.clear()
    p.notify()
    time.sleep(1.4)
    assert [b["state"] for a, b in air.calls if a == "transport"] == ["active", "idle"]
    assert not [c for c in air.calls if c[0] == "status"]
    assert len(links[-1].sent) >= int(0.6 * music.RATE) * 4


def test_link_loss_and_bridge_events_update_the_phone(phone):
    p, air, _ = phone
    paired_and_connected(p, air)
    p.play()
    p.drop()
    s = p.snapshot()
    assert not s["connected"] and s["status"] == "paused" and s["message"] == "link_lost"
    assert ("drop", {"mac": MAC}) in air.calls
    p.connect()
    air.push_event("disconnected_by_bridge")
    p.sync()
    assert not p.snapshot()["connected"] and p.snapshot()["message"] == "disconnected_by_bridge"


def test_state_survives_a_console_restart(tmp_path):
    air, store = FakeAir(), PhoneStore(str(tmp_path / "phones.json"))
    lib = Library(str(tmp_path / "up"))
    p = Phone("a", "Annas Smartphone", MAC, air, lambda m: FakeLink(), store, lib)
    air.visible = [{"name": "Hi-Fi Bridge", "address": "x"}]
    p.scan(); p.pair(); p.set_volume(33); p.shutdown()
    q = Phone("a", "Annas Smartphone", MAC, air, lambda m: FakeLink(), PhoneStore(str(tmp_path / "phones.json")), lib)
    s = q.snapshot()
    assert s["paired"] and s["volume"] == 33 and not s["connected"]
    q.shutdown()


def test_uploaded_files_join_the_library(tmp_path):
    lib = Library(str(tmp_path / "up"))
    n = len(lib.items())
    item = lib.add(b"\x01\x00\x01\x00" * 44100, "Mein Lied.wav")
    assert len(lib.items()) == n + 1 and item["source"] == "upload" and item["title"] == "Mein Lied"
    assert len(lib.pcm(item["id"])) == 44100 * 4
    assert Library(str(tmp_path / "up")).items()[-1]["title"] == "Mein Lied"   # persisted
