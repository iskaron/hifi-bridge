"""Playback state machine + Lyrion source-ownership logic.

Design (see docs/PLAYBACK_STATES.md, docs/FINDINGS.md):

* Every decision is a method call (``on_*`` / ``tick``) made under one **state lock**; I/O goes through injected ``lms`` and
  ``stream`` objects and an injected clock, so all rules are unit-testable with fakes (``tests/unit``).
* **The state lock is never held across an LMS call** (review H2: a slow or dead LMS used to block status, health and the
  watchdog for the whole outage). A decision that needs LMS is split into: snapshot under the lock -> LMS call without it ->
  **revalidation** under the lock. A start (takeover or diagnostic) is committed as an owned session only if, after the
  ``play`` command returned, the world is still the one it was decided for (same LMS server, same target, same phone, still
  wanted, no other session). Otherwise the bridge removes exactly the item it just started (URL-addressed release).
  Observations taken while the session changed are discarded; the next poll decides. At most one start is in flight.
* **Ownership invariant.** The bridge only ever (a) starts its *own* session URL (explicit takeover) and (b) removes *its
  own* session URL (``release``).  When ownership is lost (user switched LMS to something else), no LMS command is ever
  sent for that session again: late timers / disconnects / toggles only close the local stream.  There is no generic
  stop/pause/clear.
* Session identity: each takeover mints a fresh random session id embedded in the stream URL; stale sessions are rejected
  at the HTTP layer (410) and recognised in LMS observations by URL.
* Races: LMS offers no compare-and-swap.  Remaining window = (observe -> ``play``) during a takeover, which is an
  intentional override anyway; releases are URL-addressed and therefore race-free against foreign sources and against
  newer sessions of the bridge itself (different URL).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ..lms.client import LmsUnreachable, Observation
from .bounded import BoundedList
from .volume import VolumePolicy

class StreamAddressUnavailable(Exception):
    """``url_for`` has no address that LMS could reach (network not up yet): no start is sent (review H3)."""


@dataclass
class Config:
    target_player: str = ""
    audio_allowed: bool = True
    override_lms: bool = True              # BT may take over a playing LMS source (Buildroot menuconfig default on)
    notification_filter_s: float = 2.0     # only when a foreign source would be interrupted
    pause_grace_s: float = 30.0
    pause_strategy: str = "drop_restart"   # drop_restart | keepalive_silence  (see docs/FINDINGS.md)
    poll_interval_s: float = 1.0
    restart_backoff_s: float = 2.0
    wake_standby: bool = True
    settle_s: float = 1.5                  # ignore 'not ours' observations right after our own play command


@dataclass
class Session:
    sid: str
    url: str
    started: float
    device: str | None = None
    released_at_lms: bool = False          # drop_restart pause: LMS item already removed, session logically kept


@dataclass
class _Start:
    """A start (phone takeover or owner diagnostic) in flight: decided under the lock, sent without it, committed after
    revalidation."""
    lms: object
    target: str
    device: str | None
    diagnostic: bool
    prev_sid: str | None = None            # session a phone start may replace (an owner diagnostic)


class IoTracker:
    """Which thread is inside an LMS call since when. Health uses it: a loop waiting in a call that is bounded by the client's
    timeouts is busy, not hung (review H2); a call that never returns is still detected (``hang_s``)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._since: dict[int, float] = {}
        self._last: dict[int, float] = {}

    def call(self, fn: Callable, *args):
        me = threading.get_ident()
        with self._lock:
            self._since[me] = self._last[me] = time.monotonic()
        try:
            return fn(*args)
        finally:
            with self._lock:
                self._since.pop(me, None)
                self._last[me] = time.monotonic()

    def alive(self, ident: int | None, heartbeat: float, stale_s: float, hang_s: float, now: float | None = None) -> bool:
        """True if the loop thread ``ident`` beat recently, or made LMS-call progress recently, or is inside one LMS call that
        started less than ``hang_s`` ago."""
        now = time.monotonic() if now is None else now
        with self._lock:
            since, last = self._since.get(ident), self._last.get(ident, 0.0)
        if now - max(heartbeat, last) < stale_s:
            return True
        return since is not None and now - since < hang_s


class Controller:
    def __init__(self, cfg: Config, lms, stream, clock, url_for, log=None, volume: VolumePolicy | None = None,
                 io: IoTracker | None = None):
        self.cfg, self.lms, self.stream, self.clock, self.url_for = cfg, lms, stream, clock, url_for
        self.log = log or (lambda *a: None)
        self.volume = volume or VolumePolicy()
        self.io = io or IoTracker()
        self.lock = threading.RLock()                  # state lock: short critical sections only, never across LMS I/O
        # state dimensions (docs/PLAYBACK_STATES.md)
        self.device: str | None = None
        self.media_playing = False
        self.reachability = "unknown"                  # reachable | unreachable | unknown
        self.ownership = "none"                        # none | owned | lost
        self.blocked_reason: str | None = None
        # internals
        self.session: Session | None = None
        self.play_edge_at: float | None = None         # when current continuous media activity began
        self.paused_at: float | None = None
        self.lost_at: float | None = None
        self.next_poll = 0.0
        self.next_start_attempt = 0.0
        self.saw_outage = False                        # LMS/player outage seen since session start
        self.stale_urls: list[tuple[str, str]] = []    # (player, url) releases deferred by an outage
        self.events: list[tuple] = BoundedList(2000)  # (clock, name, detail) audit trail (bounded: review M7)
        self._starting: _Start | None = None           # the start in flight (at most one)
        self._recheck = False                          # something asked for a start while one was in flight
        self.io_failures = 0                           # LMS calls that failed as unreachable (tick time budget)

    # ------------------------------------------------------------------ helpers
    def _ev(self, name, detail=""):
        self.events.append((self.clock.now(), name, detail))
        self.log(name, detail)

    def status(self) -> dict:
        with self.lock:                                # short: no LMS call ever runs under this lock
            s = self.session
            return {
                "bluetooth_connection": "connected" if self.device else "disconnected",
                "bt_media_activity": ("playing" if self.media_playing else ("paused" if self.paused_at is not None else "idle")),
                "audio_permission": "allowed" if self.cfg.audio_allowed else "blocked",
                "lyrion_reachability": self.reachability,
                "target_ownership": self.ownership,
                "stream_transport": ("closed" if not s else ("parked" if s.released_at_lms else "streaming")),
                "blocked_reason": self.blocked_reason,
                "session": s.sid if s else None,
            }

    def _wanted(self) -> bool:
        return bool(self.device) and self.media_playing and self.cfg.audio_allowed

    def _call(self, fn, *args):
        """One LMS call. Must run WITHOUT the state lock."""
        try:
            return self.io.call(fn, *args)
        except LmsUnreachable:
            self.io_failures += 1
            raise

    def _observe_io(self, lms, target: str) -> Observation | None:
        """Observe the target (no lock held); records reachability if the server is still the current one."""
        try:
            obs = self._call(lms.observe, target)
        except LmsUnreachable:
            with self.lock:
                if lms is self.lms:
                    self._set_reachability("unreachable")
            return None
        with self.lock:
            if lms is self.lms and target == self.cfg.target_player:
                self._set_reachability("reachable" if (obs.player_connected and obs.player_known) else "unreachable")
        return obs if obs.player_connected else None

    def _set_reachability(self, r: str):
        if r != self.reachability:
            self._ev("reachability", r)
            self.reachability = r
        if r == "unreachable" and self.session:
            self.saw_outage = True

    def _is_ours(self, obs: Observation) -> bool:
        return bool(self.session) and obs.url == self.session.url

    @staticmethod
    def _run(actions: list[Callable[[], None]]) -> None:
        for a in actions:                              # LMS I/O collected under the lock, executed after it was released
            a()

    # ------------------------------------------------------------------ source events
    def on_connected(self, device: str) -> bool:
        """Returns False when the connection must be refused: exactly one active source,
        and a second phone never silently evicts the first (docs/DECISIONS.md #3)."""
        with self.lock:
            if self.device and self.device != device:
                self._ev("source_rejected", "another source is active")
                return False
            self.device = device
            self.volume.reset()
            self._ev("bt_connected")          # connection alone never starts LMS playback
            return True

    def on_disconnected(self, device: str) -> None:
        with self.lock:
            if device != self.device:
                return
            self.device, self.media_playing, self.play_edge_at, self.paused_at = None, False, None, None
            self.blocked_reason = None
            self._ev("bt_disconnected")
            acts = self._release_locked("disconnect")        # immediate, no grace
        self._run(acts)

    def on_media(self, playing: bool) -> None:
        """Fused 'phone is playing media' signal (see core.fusion)."""
        acts: list = []
        with self.lock:
            if not self.device:
                return
            now = self.clock.now()
            if playing and not self.media_playing:
                self.media_playing, self.play_edge_at = True, now
                self._ev("media_play")
                was_paused = self.paused_at is not None
                self.paused_at = None
                if self.session and was_paused:
                    s = self.session
                    self._ev("resume_within_grace")
                    if s.released_at_lms:
                        # drop_restart: fresh session (new id) from live audio; re-evaluate target like a fresh Play
                        self._finish_session_local()
                        acts.append(self._takeover)
                    # keepalive_silence: the stream never stopped, nothing to do
                else:
                    acts.append(self._takeover)
            elif not playing and self.media_playing:
                self.media_playing, self.play_edge_at = False, None
                self._ev("media_pause")
                self.blocked_reason = None
                if self.session and self.ownership == "owned":
                    self.paused_at = now
                    if self.cfg.pause_strategy == "drop_restart":
                        acts += self._park_locked()
                else:
                    self.paused_at = None
        self._run(acts)

    def on_volume(self, value: int) -> None:
        with self.lock:
            s = self.session
            if not (s and self.ownership == "owned" and not s.released_at_lms):
                self.volume.on_phone_volume(value, None)   # still track the reference
                return
            lms, target, sid, url = self.lms, self.cfg.target_player, s.sid, s.url
        try:
            obs = self._call(lms.observe, target)
            with self.lock:
                s = self.session
                still = (s is not None and s.sid == sid and self.ownership == "owned" and not s.released_at_lms
                         and lms is self.lms and target == self.cfg.target_player and obs.url == url)
                delta = self.volume.on_phone_volume(value, obs.volume if still else None)
            if still and delta:
                self._call(lms.volume_delta, target, delta)
        except LmsUnreachable:
            with self.lock:
                if lms is self.lms:
                    self._set_reachability("unreachable")

    def on_metadata(self, title: str) -> None:
        self.stream.set_title(title)

    # ------------------------------------------------------------------ owner controls
    def set_audio_allowed(self, allowed: bool) -> None:
        acts: list = []
        with self.lock:
            self.cfg.audio_allowed = allowed
            self._ev("audio_allowed", allowed)
            if not allowed:
                acts = self._release_locked("audio_disabled")
                self.blocked_reason = "audio_disabled"
            else:
                self.blocked_reason = None
                if self._wanted():
                    acts.append(self._takeover)
        self._run(acts)

    def set_target(self, player: str) -> None:
        acts: list = []
        with self.lock:
            if player == self.cfg.target_player:
                return
            self._ev("target_change", player)
            acts = self._release_locked("target_change")    # releases at the OLD target (captured before cfg changes)
            self.cfg.target_player = player
            self.lost_at = None
            if self._wanted():
                acts.append(self._takeover)
        self._run(acts)

    def change_server(self, lms, target_player: str | None = None) -> None:
        """Switch to another LMS server (optionally with a new target): release OUR stream on the old server (URL-addressed,
        as always), then continue on the new one. An LMS call still in flight against the old server is discarded."""
        acts: list = []
        with self.lock:
            changing_target = target_player is not None and target_player != self.cfg.target_player
            acts = self._release_locked("target_change" if changing_target else "server_change")
            if target_player is not None:
                self.cfg.target_player = target_player
            self.lms = lms
            self.reachability, self.lost_at, self.stale_urls = "unknown", None, []
            self.ownership = "none"
            if self._wanted():
                acts.append(self._takeover)
        self._run(acts)

    def restart_session(self, reason: str, apply: Callable[[], bool]) -> None:
        """Apply a change (``apply`` returns whether anything changed, e.g. the stream format) and, if the bridge owned a
        session, replace it by a fresh one so the change takes effect."""
        acts: list = []
        with self.lock:
            owned = self.session is not None and self.ownership == "owned"
            if not apply():
                return
            if owned:
                acts = self._release_locked(reason)
                if self._wanted():
                    acts.append(self._takeover)
        self._run(acts)

    def release(self, reason: str) -> None:
        """Release our own session (e.g. at shutdown)."""
        with self.lock:
            acts = self._release_locked(reason)
        self._run(acts)

    # ------------------------------------------------------------------ owner diagnostic
    def begin_diagnostic(self) -> tuple[bool, str, str | None]:
        """Owner 'play test sound' (no phone needed). REFUSES - without touching LMS - if the target is
        playing anything, a bridge session exists, or LMS/target is unreachable. Returns (ok, reason, sid)."""
        with self.lock:
            if self.session or self._starting:
                return False, "bridge_session_active", None
            st = self._starting = _Start(self.lms, self.cfg.target_player, None, True)
        try:
            obs = self._observe_io(st.lms, st.target)
            with self.lock:
                if not self._valid(st):
                    return False, "bridge_session_active", None
                if obs is None:
                    return False, "lyrion_unreachable", None
                if obs.playing_something:
                    self._ev("diagnostic_refused", "target_busy")
                    return False, "target_busy", None
            sid = self._launch(st, obs)
            return (True, "started", sid) if sid else (False, "start_failed", None)
        finally:
            if self._start_done(st):
                self._takeover()

    def end_diagnostic(self, sid: str) -> None:
        with self.lock:
            acts = []
            if self.session and self.session.sid == sid:     # only our own diagnostic session
                acts = self._release_locked("diagnostic_done")
        self._run(acts)

    # ------------------------------------------------------------------ takeover
    def _takeover(self) -> None:
        """Evaluate a start for the phone (called WITHOUT the state lock)."""
        for _ in range(3):                                   # a start requested while one was in flight is re-evaluated
            with self.lock:
                if not self._wanted():
                    return
                if self._starting is not None:
                    self._recheck = True
                    return
                st = self._starting = _Start(self.lms, self.cfg.target_player, self.device, False,
                                             self.session.sid if self.session else None)
            try:
                self._takeover_once(st)
            finally:
                again = self._start_done(st)
            if not again:
                return

    def _start_done(self, st: _Start) -> bool:
        with self.lock:
            if self._starting is st:
                self._starting = None
            again, self._recheck = self._recheck, False
            # a phone start asked for during an owner diagnostic replaces the diagnostic; otherwise only start if nothing runs
            return again and self._wanted() and (self.session is None or st.diagnostic)

    def _valid(self, st: _Start) -> bool:
        """Is a start still the one it was decided for? (revalidation after every LMS call, under the lock)"""
        if self._starting is not st or self.lms is not st.lms or self.cfg.target_player != st.target:
            return False
        if st.diagnostic:
            return self.session is None
        cur = self.session.sid if self.session else None
        return self._wanted() and self.device == st.device and cur == st.prev_sid

    def _takeover_once(self, st: _Start) -> None:
        obs = self._observe_io(st.lms, st.target)
        with self.lock:
            if not self._valid(st):
                return
            if obs is None:
                self.blocked_reason = "lyrion_unreachable"
                return                                    # tick() retries; Bluetooth stays connected
            busy = obs.playing_something and not self._is_ours(obs)
            if busy:
                if not self.cfg.override_lms:
                    self.blocked_reason = "override_disabled"
                    self._ev("takeover_blocked", "override_disabled")
                    return
                elapsed = self.clock.now() - (self.play_edge_at or self.clock.now())
                if elapsed < self.cfg.notification_filter_s:
                    self.blocked_reason = "notification_filter"   # pending: tick() re-evaluates
                    return
                self.blocked_reason = None
                self._ev("takeover_override", f"after {elapsed:.2f}s")
            else:
                self.blocked_reason = None                # idle target: prompt start, no filter
        self._launch(st, obs)

    def _launch(self, st: _Start, obs: Observation) -> str | None:
        """Send the start (no lock held) and commit it after revalidation. Returns the session id, or None."""
        sid = secrets.token_urlsafe(6)
        try:
            url = self.url_for(sid)                       # looks up the local address per session: not under the lock
        except StreamAddressUnavailable:
            url = None
        with self.lock:
            if not self._valid(st):
                return None
            now = self.clock.now()
            if url is None:                               # never hand LMS a URL it cannot reach; tick() retries
                self.next_start_attempt = now + self.cfg.restart_backoff_s
                if self.blocked_reason != "stream_address_unavailable":
                    self._ev("stream_address_unavailable")
                self.blocked_reason = "stream_address_unavailable"
                return None
            self.next_start_attempt = now + self.cfg.restart_backoff_s
            self.stream.open_session(sid)
            wake = not obs.power and self.cfg.wake_standby
        try:
            if wake:
                self._call(st.lms.power_on, st.target)
            self._call(st.lms.play, st.target, url, "Bluetooth")
        except LmsUnreachable:
            with self.lock:
                self.stream.close_session(sid)
                if st.lms is self.lms:
                    self.stale_urls.append((st.target, url))   # the play may still have reached LMS: remove it when back
                    self._set_reachability("unreachable")
                    self.blocked_reason = "lyrion_unreachable"
            return None
        acts: list = []
        with self.lock:
            if self._valid(st):
                old = self.session
                if old is not None and old.sid != sid:
                    self.stream.close_session(old.sid)    # an owner diagnostic replaced by the phone (play replaced the item)
                self.session, self.ownership, self.lost_at, self.paused_at = Session(sid, url, now, st.device), "owned", None, None
                self.saw_outage = False
                self.blocked_reason = None
                self._ev("session_start", sid)
                return sid
            # the world changed while the play command was in flight (disconnect, pause, target/server change, audio off):
            # this item is unwanted -> remove exactly it (URL-addressed: harmless if anything else plays now)
            self.stream.close_session(sid)
            self._ev("start_superseded", sid)
            acts.append(self._release_io(st.lms, st.target, url))
        self._run(acts)
        return None

    def _park_locked(self) -> list:
        """drop_restart pause: remove *our* item now (fast silence), keep logical ownership for the grace period."""
        s = self.session
        if not s or s.released_at_lms:
            return []
        s.released_at_lms = True
        self.stream.close_session(s.sid)
        return [self._release_io(self.lms, self.cfg.target_player, s.url, then=("session_parked", s.sid))]

    # ------------------------------------------------------------------ release
    def _release_io(self, lms, player: str, url: str, then: tuple | None = None) -> Callable[[], None]:
        """An action that removes our URL at LMS (outside the lock); an outage defers it to the next reachable poll.
        ``then`` = event recorded after the attempt (the crash-recovery journal follows these events)."""
        def act():
            try:
                self._call(lms.release, player, url)      # URL-addressed: harmless if a foreign source is current
            except LmsUnreachable:
                with self.lock:
                    if lms is self.lms:
                        self.stale_urls.append((player, url))   # retry when LMS returns
                        self._set_reachability("unreachable")
            if then:
                with self.lock:
                    self._ev(*then)
        return act

    def _finish_session_local(self) -> None:
        if self.session:
            self.stream.close_session(self.session.sid)
        self.session = None
        self.ownership = "none"

    def _release_locked(self, reason: str) -> list:
        s = self.session
        if not s:
            self.paused_at = None
            return []
        detail = f"{s.sid}:{reason}"
        acts: list = []
        if self.ownership == "owned" and not s.released_at_lms:
            acts.append(self._release_io(self.lms, self.cfg.target_player, s.url, then=("session_release", detail)))
        else:
            self._ev("session_release", detail)
        self._finish_session_local()
        self.paused_at = None
        return acts

    # ------------------------------------------------------------------ periodic
    def tick(self) -> None:
        """Periodic rules. Time budget: once an LMS call of this tick failed as unreachable, the tick makes no further LMS
        calls (the next poll retries), so an outage costs at most one timeout per tick."""
        failures = self.io_failures
        acts: list = []
        with self.lock:
            now = self.clock.now()
            # 1. grace expiry: release only OUR stream (never anything else)
            if self.paused_at is not None and now - self.paused_at >= self.cfg.pause_grace_s:
                self._ev("grace_expired")
                acts = self._release_locked("grace_expired")
            # 2. notification filter pending
            filter_pending = self.blocked_reason == "notification_filter" and self._wanted()
        self._run(acts)
        if filter_pending and self.io_failures == failures:
            self._takeover()
        # 3. LMS polling: ownership validation + recovery
        with self.lock:
            poll = now >= self.next_poll and self.io_failures == failures
            if poll:
                self.next_poll = now + self.cfg.poll_interval_s
        if poll:
            self._poll()
        # 4. wanted but nothing running (e.g. LMS was unreachable at Play time)
        with self.lock:
            retry = (self._wanted() and not self.session and self._starting is None and self.ownership != "lost"
                     and self.blocked_reason in (None, "lyrion_unreachable", "stream_address_unavailable")
                     and now >= self.next_start_attempt
                     and self.io_failures == failures)
            if retry:
                self.next_start_attempt = now + self.cfg.restart_backoff_s
        if retry:
            self._takeover()

    def _poll(self) -> None:
        with self.lock:
            lms, target = self.lms, self.cfg.target_player
            sid = self.session.sid if self.session else None
        obs = self._observe_io(lms, target)
        if obs is None:
            return
        # deferred releases of stale URLs (release attempted while LMS was down)
        with self.lock:
            if lms is not self.lms:
                return
            todo = [it for it in self.stale_urls if obs.url == it[1]]
            self.stale_urls = [it for it in self.stale_urls if obs.url == it[1]]
        for item in todo:
            try:
                self._call(lms.release, *item)
            except LmsUnreachable:
                return
            with self.lock:
                if item in self.stale_urls:
                    self.stale_urls.remove(item)
        want_start = False
        with self.lock:
            now = self.clock.now()
            s = self.session
            if lms is not self.lms or target != self.cfg.target_player or self._starting is not None:
                return                                     # changed during the observation: the next poll decides
            if not s or s.sid != sid or self.ownership != "owned" or s.released_at_lms:
                return
            if now - s.started < self.cfg.settle_s:
                return
            if self._is_ours(obs):
                if obs.mode == "stop":                     # our stream died under us (not a user choice)
                    self._ev("stream_died")
                    self._finish_session_local()
                    want_start = self._wanted() and now >= self.next_start_attempt   # else tick() retries after the backoff
            # LMS shows something else
            elif obs.playing_something:
                self._lose_ownership("foreign_source")
            elif self.saw_outage:
                self._ev("restart_after_outage")            # LMS/player restarted: playlist gone, resume live audio
                self._finish_session_local()
                want_start = self._wanted() and now >= self.next_start_attempt
            else:
                self._lose_ownership("stopped_or_cleared")  # user stopped/cleared at LMS: don't resurrect
        if want_start:
            self._takeover()

    def _lose_ownership(self, why: str) -> None:
        self._ev("ownership_lost", why)
        if self.session:
            self.stream.close_session(self.session.sid)   # local only; NO LMS command
        self.session = None
        self.ownership = "lost"
        self.lost_at = self.clock.now()
        self.paused_at = None
