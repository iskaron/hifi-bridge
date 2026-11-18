"""Fuse A2DP transport state, AVRCP playback status and PCM activity into one 'media playing' signal.

SIMULATED/UNVERIFIED against real phones: the rules encode the documented
semantics of BlueZ (MediaPlayer1.Status, MediaTransport1.State) and plausible
phone behaviour; real Android/iPhone behaviour must be validated on hardware
(docs/OPEN_QUESTIONS.md #2).

Rules
  * AVRCP status is authoritative when the phone provides it ('playing' / 'paused'
    / 'stopped'), but 'playing' additionally requires the A2DP transport not to be idle.
  * Without AVRCP: transport 'active' AND PCM seen within ``pcm_timeout_s`` => playing.
    Digital silence *inside* an active stream (quiet passage) still counts as playing;
    only the absence of PCM frames / transport going idle means paused.
  * Transport-only activity while AVRCP is available but not 'playing' (typical
    notification sound) does NOT count as media playing.
  * A ``None`` status (the phone's MediaPlayer1 was removed, or it reported 'error'/an unknown status) means "no usable
    AVRCP information": the PCM rule applies again until the phone reports a real status (review L11; conservative fix,
    HARDWARE-PENDING - real phones' player lifecycle is unverified).
"""
from __future__ import annotations


class MediaFusion:
    def __init__(self, clock, pcm_timeout_s: float = 1.5):
        self.clock = clock
        self.pcm_timeout_s = pcm_timeout_s
        self.avrcp: str | None = None          # playing|paused|stopped|None (unknown/unsupported)
        self.avrcp_supported = False
        self.transport = "idle"                # idle|pending|active
        self.last_pcm = None

    def on_avrcp(self, status: str | None) -> None:
        self.avrcp = status
        self.avrcp_supported = status is not None      # None: player gone -> fall back to transport + PCM

    def on_transport(self, state: str) -> None:
        self.transport = state

    def on_pcm(self) -> None:
        self.last_pcm = self.clock.now()

    def reset(self) -> None:
        self.avrcp, self.avrcp_supported, self.transport, self.last_pcm = None, False, "idle", None

    def playing(self) -> bool:
        if self.transport == "idle":
            return False
        if self.avrcp_supported:
            return self.avrcp == "playing"
        pcm_recent = self.last_pcm is not None and self.clock.now() - self.last_pcm <= self.pcm_timeout_s
        return self.transport == "active" and pcm_recent
