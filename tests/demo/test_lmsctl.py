"""Lyrion demo helpers: exact JSON-RPC commands (the real LMS is exercised by the stack test)."""
from hifidemo.lmsctl import RADIO_TITLE, Lms


class Rec:
    def __init__(self, replies=None):
        self.sent, self.replies = [], replies or {}

    def __call__(self, body):
        self.sent.append(body["params"])
        key = body["params"][1][0]
        return {"result": self.replies.get(key, {})}


def test_players_merges_status():
    rec = Rec({"players": {"players_loop": [{"playerid": "02:00:00:00:01:01", "name": "Wohnzimmer", "connected": 1}]},
               "status": {"mode": "play", "playlist_loop": [{"title": "Abendlicht"}], "mixer volume": 50, "sync_slaves": "a,b"}})
    p = Lms(transport=rec).players()
    assert p == [{"id": "02:00:00:00:01:01", "name": "Wohnzimmer", "connected": True, "mode": "play", "title": "Abendlicht",
                  "volume": 50, "sync_master": None, "sync_slaves": ["a", "b"]}]


def test_radio_stop_sync_unsync_commands():
    rec = Rec()
    lms = Lms(transport=rec)
    lms.play_radio("p1", "http://console:8100/radio.wav")
    lms.stop("p1")
    lms.sync(["p1", "p2", "p3"])
    lms.unsync(["p2", "p3"])
    assert rec.sent == [["p1", ["playlist", "play", "http://console:8100/radio.wav", RADIO_TITLE]], ["p1", ["stop"]],
                        ["p1", ["sync", "p2"]], ["p1", ["sync", "p3"]], ["p2", ["sync", "-"]], ["p3", ["sync", "-"]]]
