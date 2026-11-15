"""Playback state machine + Lyrion source-ownership logic.

Design (see docs/PLAYBACK_STATES.md, docs/FINDINGS.md):

* Every decision is a method call (``on_*`` / ``tick``) executed under one lock on a
  single logical timeline; I/O goes through injected ``lms`` and ``stream`` objects
  and an injected clock, so all rules are unit-testable with fakes (``tests/unit``).
* **Ownership invariant.** The bridge only ever (a) starts its *own* session URL
  (explicit takeover) and (b) removes *its own* session URL (``release``).  When
  ownership is lost (user switched LMS to something else), no LMS command is ever
  sent for that session again: late timers / disconnects / toggles only close the
  local stream.  There is no generic stop/pause/clear.
* Session identity: each takeover mints a fresh random session id embedded in the
  stream URL; stale sessions are rejected at the HTTP layer (410) and recognised in
  LMS observations by URL.
* Races: LMS offers no compare-and-swap.  Remaining window = (observe -> ``play``)
  during a takeover, which is an intentional override anyway; releases are
  URL-addressed and therefore race-free against foreign sources.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field

from ..lms.client import LmsUnreachable, Observation
from .volume import VolumePolicy


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


class Controller:
    def __init__(self, cfg: Config, lms, stream, clock, url_for, log=None, volume: VolumePolicy | None = None):
        self.cfg, self.lms, self.stream, self.clock, self.url_for = cfg, lms, stream, clock, url_for
        self.log = log or (lambda *a: None)
        self.volume = volume or VolumePolicy()
        self.lock = threading.RLock()
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
        self.events: list[tuple] = []                  # (clock, name, detail) audit trail

    # ------------------------------------------------------------------ helpers
    def _ev(self, name, detail=""):
        self.events.append((self.clock.now(), name, detail))
        self.log(name, detail)

    def status(self) -> dict:
        with self.lock:
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

    def _observe(self) -> Observation | None:
        try:
            obs = self.lms.observe(self.cfg.target_player)
        except LmsUnreachable:
            self._set_reachability("unreachable")
            return None
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
            self._release("disconnect")        # immediate, no grace

    def on_media(self, playing: bool) -> None:
        """Fused 'phone is playing media' signal (see core.fusion)."""
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
                    self._resume_within_grace()
                else:
                    self._maybe_takeover(fresh=True)
            elif not playing and self.media_playing:
                self.media_playing, self.play_edge_at = False, None
                self._ev("media_pause")
                self.blocked_reason = None
                if self.session and self.ownership == "owned":
                    self.paused_at = now
                    if self.cfg.pause_strategy == "drop_restart":
                        self._park_session()
                else:
                    self.paused_at = None

    def on_volume(self, value: int) -> None:
        with self.lock:
            if not (self.session and self.ownership == "owned" and not self.session.released_at_lms):
                self.volume.on_phone_volume(value, None)   # still track the reference
                return
            try:
                cur = self.lms.observe(self.cfg.target_player).volume
                delta = self.volume.on_phone_volume(value, cur)
                if delta:
                    self.lms.volume_delta(self.cfg.target_player, delta)
            except LmsUnreachable:
                self._set_reachability("unreachable")

    def on_metadata(self, title: str) -> None:
        self.stream.set_title(title)

    # ------------------------------------------------------------------ owner controls
    def set_audio_allowed(self, allowed: bool) -> None:
        with self.lock:
            self.cfg.audio_allowed = allowed
            self._ev("audio_allowed", allowed)
            if not allowed:
                self._release("audio_disabled")
                self.blocked_reason = "audio_disabled"
            else:
                self.blocked_reason = None
                if self._wanted():
                    self._maybe_takeover(fresh=True)

    def set_target(self, player: str) -> None:
        with self.lock:
            if player == self.cfg.target_player:
                return
            self._ev("target_change", player)
            self._release("target_change")          # releases at the OLD target (cfg not yet changed)
            self.cfg.target_player = player
            self.lost_at = None
            if self._wanted():
                self._maybe_takeover(fresh=True)

    # ------------------------------------------------------------------ owner diagnostic
    def begin_diagnostic(self) -> tuple[bool, str, str | None]:
        """Owner 'play test sound' (no phone needed). REFUSES - without touching LMS - if the target is
        playing anything, a bridge session exists, or LMS/target is unreachable. Returns (ok, reason, sid)."""
        with self.lock:
            if self.session:
                return False, "bridge_session_active", None
            obs = self._observe()
            if obs is None:
                return False, "lyrion_unreachable", None
            if obs.playing_something:
                self._ev("diagnostic_refused", "target_busy")
                return False, "target_busy", None
            self._start_session(obs)
            return (True, "started", self.session.sid) if self.session else (False, "start_failed", None)

    def end_diagnostic(self, sid: str) -> None:
        with self.lock:
            if self.session and self.session.sid == sid:     # only our own diagnostic session
                self._release("diagnostic_done")

    # ------------------------------------------------------------------ takeover
    def _maybe_takeover(self, fresh: bool) -> None:
        if not self._wanted():
            return
        obs = self._observe()
        if obs is None:
            self.blocked_reason = "lyrion_unreachable"
            return                                    # tick() retries; Bluetooth stays connected
        busy = obs.playing_something and not self._is_ours(obs)
        if not busy:
            self.blocked_reason = None
            self._start_session(obs)                  # idle target: prompt start, no filter
            return
        if not self.cfg.override_lms:
            self.blocked_reason = "override_disabled"
            self._ev("takeover_blocked", "override_disabled")
            return
        elapsed = self.clock.now() - (self.play_edge_at or self.clock.now())
        if elapsed >= self.cfg.notification_filter_s:
            self.blocked_reason = None
            self._ev("takeover_override", f"after {elapsed:.2f}s")
            self._start_session(obs)
        else:
            self.blocked_reason = "notification_filter"   # pending: tick() re-evaluates

    def _start_session(self, obs: Observation) -> None:
        now = self.clock.now()
        self.next_start_attempt = now + self.cfg.restart_backoff_s
        sid = secrets.token_urlsafe(6)
        url = self.url_for(sid)
        sess = Session(sid, url, now, self.device)
        self.stream.open_session(sid)
        try:
            if not obs.power and self.cfg.wake_standby:
                self.lms.power_on(self.cfg.target_player)
            self.lms.play(self.cfg.target_player, url, "Bluetooth")
        except LmsUnreachable:
            self.stream.close_session(sid)
            self._set_reachability("unreachable")
            self.blocked_reason = "lyrion_unreachable"
            return
        self.session, self.ownership, self.lost_at, self.paused_at = sess, "owned", None, None
        self.saw_outage = False
        self.blocked_reason = None
        self._ev("session_start", sid)

    def _resume_within_grace(self) -> None:
        s = self.session
        self._ev("resume_within_grace")
        if s and not s.released_at_lms:
            return                                    # keepalive_silence: stream never stopped
        # drop_restart: fresh session (new id) from live audio; re-evaluate target like a fresh Play
        self._finish_session_local()
        self._maybe_takeover(fresh=True)

    def _park_session(self) -> None:
        """drop_restart pause: remove *our* item now (fast silence), keep logical ownership for the grace period."""
        s = self.session
        if not s or s.released_at_lms:
            return
        self._lms_release(s)
        s.released_at_lms = True
        self.stream.close_session(s.sid)
        self._ev("session_parked", s.sid)

    # ------------------------------------------------------------------ release
    def _lms_release(self, s: Session, player: str | None = None) -> None:
        player = player or self.cfg.target_player
        try:
            self.lms.release(player, s.url)           # URL-addressed: harmless if foreign source is current
        except LmsUnreachable:
            self.stale_urls.append((player, s.url))   # retry when LMS returns
            self._set_reachability("unreachable")

    def _finish_session_local(self) -> None:
        if self.session:
            self.stream.close_session(self.session.sid)
        self.session = None
        self.ownership = "none"

    def _release(self, reason: str) -> None:
        s = self.session
        if not s:
            self.paused_at = None
            return
        if self.ownership == "owned" and not s.released_at_lms:
            self._lms_release(s)
        self._ev("session_release", f"{s.sid}:{reason}")
        self._finish_session_local()
        self.paused_at = None

    # ------------------------------------------------------------------ periodic
    def tick(self) -> None:
        with self.lock:
            now = self.clock.now()
            # 1. grace expiry: release only OUR stream (never anything else)
            if self.paused_at is not None and now - self.paused_at >= self.cfg.pause_grace_s:
                self._ev("grace_expired")
                self._release("grace_expired")
            # 2. notification filter pending
            if self.blocked_reason == "notification_filter" and self._wanted():
                self._maybe_takeover(fresh=False)
            # 3. LMS polling: ownership validation + recovery
            if now >= self.next_poll:
                self.next_poll = now + self.cfg.poll_interval_s
                self._poll(now)
            # 4. wanted but nothing running (e.g. LMS was unreachable at Play time)
            if self._wanted() and not self.session and self.ownership != "lost" \
                    and self.blocked_reason in (None, "lyrion_unreachable") and now >= self.next_start_attempt:
                self.next_start_attempt = now + self.cfg.restart_backoff_s
                self._maybe_takeover(fresh=False)

    def _poll(self, now: float) -> None:
        try:
            obs = self.lms.observe(self.cfg.target_player)
        except LmsUnreachable:
            self._set_reachability("unreachable")
            return
        self._set_reachability("reachable" if (obs.player_connected and obs.player_known) else "unreachable")
        if not obs.player_connected:
            return
        # deferred releases of stale URLs (release attempted while LMS was down)
        for item in list(self.stale_urls):
            if obs.url == item[1]:
                try:
                    self.lms.release(*item)
                except LmsUnreachable:
                    return
            self.stale_urls.remove(item)
        s = self.session
        if not s or self.ownership != "owned" or s.released_at_lms:
            return
        if now - s.started < self.cfg.settle_s:
            return
        if self._is_ours(obs):
            if obs.mode == "stop":                     # our stream died under us (not a user choice)
                self._ev("stream_died")
                self._finish_session_local()
                if self._wanted() and now >= self.next_start_attempt:
                    self._maybe_takeover(fresh=False)      # else tick() retries after the backoff
            return
        # LMS shows something else
        if obs.playing_something:
            self._lose_ownership("foreign_source")
        elif self.saw_outage:
            self._ev("restart_after_outage")            # LMS/player restarted: playlist gone, resume live audio
            self._finish_session_local()
            if self._wanted() and now >= self.next_start_attempt:
                self._maybe_takeover(fresh=False)
        else:
            self._lose_ownership("stopped_or_cleared")  # user stopped/cleared at LMS: don't resurrect

    def _lose_ownership(self, why: str) -> None:
        self._ev("ownership_lost", why)
        if self.session:
            self.stream.close_session(self.session.sid)   # local only; NO LMS command
        self.session = None
        self.ownership = "lost"
        self.lost_at = self.clock.now()
        self.paused_at = None
