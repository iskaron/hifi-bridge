"""Fakes for UNIT tests (clearly mocks: nothing here is evidence of real LMS behaviour)."""
from btbridge.core.clock import FakeClock
from btbridge.core.controller import Config, Controller
from btbridge.lms.client import LmsUnreachable, Observation


class FakeLms:
    """Models only what the lab measured on real LMS 9.1.2: ``play`` replaces the playlist;
    ``release`` (= playlist deleteitem <url>) stops only if that URL is current."""

    def __init__(self):
        self.players = {}
        self.down = False
        self.commands = []

    def add(self, pid, power=True, connected=True):
        self.players[pid] = dict(mode="stop", url=None, power=power, connected=connected, volume=40)

    def _chk(self):
        if self.down:
            raise LmsUnreachable("down")

    def observe(self, pid):
        self._chk()
        p = self.players.get(pid)
        if p is None:
            return Observation(player_known=False, player_connected=False)
        return Observation(player_connected=p["connected"], mode=p["mode"], power=p["power"], url=p["url"], volume=p["volume"])

    def play(self, pid, url, title="x"):
        self._chk(); self.commands.append(("play", pid, url))
        p = self.players[pid]; p.update(mode="play", url=url, power=True)

    def release(self, pid, url):
        self._chk(); self.commands.append(("release", pid, url))
        p = self.players[pid]
        if p["url"] == url:
            p.update(mode="stop", url=None)

    def power_on(self, pid):
        self._chk(); self.commands.append(("power_on", pid)); self.players[pid]["power"] = True

    def volume_delta(self, pid, d):
        self._chk(); self.commands.append(("volume_delta", pid, d)); self.players[pid]["volume"] += d

    # --- test helpers (the 'user' acting on LMS) ---
    def user_plays(self, pid, url="http://radio/stream"):
        p = self.players[pid]; p.update(mode="play", url=url)

    def user_stops(self, pid):
        self.players[pid].update(mode="stop", url=None)

    def restart_server(self):
        for p in self.players.values():
            p.update(mode="stop", url=None)


class FakeStream:
    def __init__(self):
        self.open, self.log, self.title = set(), [], ""

    def open_session(self, sid):
        self.open.add(sid); self.log.append(("open", sid))

    def close_session(self, sid):
        self.open.discard(sid); self.log.append(("close", sid))

    def set_title(self, t):
        self.title = t


PLAYER = "02:00:00:00:00:01"


class Rig:
    def __init__(self, **cfg):
        self.clock = FakeClock()
        self.lms = FakeLms(); self.lms.add(PLAYER)
        self.stream = FakeStream()
        self.cfg = Config(target_player=PLAYER, **cfg)
        self.ctl = Controller(self.cfg, self.lms, self.stream, self.clock, lambda sid: f"http://bridge:8090/s/TOKEN/{sid}.wav")

    def run(self, seconds, step=0.25):
        t = 0.0
        while t < seconds:
            self.clock.advance(step); self.ctl.tick(); t += step

    @property
    def url(self):
        return self.ctl.session.url if self.ctl.session else None

    def cmds(self, kind=None):
        return [c for c in self.lms.commands if kind is None or c[0] == kind]

    def connect_and_play(self, dev="phone"):
        assert self.ctl.on_connected(dev)
        self.ctl.on_media(True)
