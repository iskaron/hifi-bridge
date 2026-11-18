"""Network state machine of the root network agent (pure Python; adapters and clock injected - docs/NETWORK.md has the diagram).

The radio has exactly one role at a time: **client Wi-Fi XOR setup hotspot, never both** (PRODUCT_SPEC; the systemd units additionally
conflict with each other). Ethernet is configured independently and counts as an *uplink* when it has carrier and a usable address.

Radio states::

    off         no client Wi-Fi configured; the hotspot starts after NO_WIFI_GRACE_S without any uplink (if credentials exist)
    connecting  wpa_supplicant runs; the attempt fails after FALLBACK_S (60 s, provisional) -> hotspot (if allowed) else keep trying
    connected   associated and a usable address on the Wi-Fi interface
    hotspot     hostapd + DHCP run; probes the client Wi-Fi again on a back-off schedule (PROBE_DELAYS_S) while nobody uses the
                hotspot; closes when an Ethernet uplink is back and the hotspot has been idle for HOTSPOT_IDLE_S
    probing     like connecting, started from the hotspot; failure -> back to hotspot with the next back-off step
    unavailable no Wi-Fi interface (yet)

The hotspot is *allowed* only with owner-known credentials AND a regulatory country AND no working Ethernet uplink (DECISIONS #56).

Owner changes are **trials**: the new configuration runs at once; a change that affects the path the owner may be using (the connected
Wi-Fi, the active hotspot, Ethernet with carrier) must be confirmed within TRIAL_S or it is rolled back automatically. A trial whose Wi-Fi
attempt fails is rolled back as soon as the attempt fails. Changes without such an effect are confirmed at once.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import netconfig

FALLBACK_S = 60.0                         # PRODUCT_SPEC: provisional 60 s before the setup fallback
NO_WIFI_GRACE_S = 15.0                    # no client Wi-Fi configured: time for Ethernet/DHCP before the hotspot opens
HOTSPOT_IDLE_S = 30.0                     # an uplink is back: close the hotspot once nobody has used it for this long
PROBE_DELAYS_S = (120.0, 300.0, 600.0)    # retry the client Wi-Fi from the hotspot: after 2 min, 5 min, then every 10 min
PROBE_BUSY_RETRY_S = 60.0                 # a phone is on the hotspot: never interrupt it, look again later
SCAN_FRESH_S = 30.0
TRIAL_S = 180.0                           # owner must confirm a connectivity-affecting change within 3 min

OFF, CONNECTING, CONNECTED, HOTSPOT, PROBING, UNAVAILABLE = "off", "connecting", "connected", "hotspot", "probing", "unavailable"
CLIENT_STATES = (CONNECTING, CONNECTED, PROBING)


@dataclass
class Observation:
    eth_carrier: bool = False
    eth_up: bool = False                  # carrier and a usable (non-link-local) address
    wifi_present: bool = True             # a Wi-Fi interface exists
    wifi_state: str = ""                  # wpa_supplicant wpa_state; "" when it is not running
    wifi_address: bool = False            # usable address on the Wi-Fi interface
    wifi_addresses: list = field(default_factory=list)
    events: list = field(default_factory=list)   # wpa_supplicant events since the previous observation
    stations: int = 0                     # hotspot clients


@dataclass
class Trial:
    id: str
    old: dict
    changed: set
    deadline: float | None
    state: str = "pending"                # pending | confirmed | rolled_back
    reason: str | None = None
    detail: dict = field(default_factory=dict)


class NetworkManager:
    def __init__(self, system, clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = lambda m: None):
        self.system, self.clock, self.log = system, clock, log
        self.cfg = netconfig.defaults()
        self.state = OFF
        self.entered = clock()
        self.deadline: float | None = None
        self.failure: str | None = None          # result of the last failed client attempt (shown to the owner)
        self.reason: str | None = None           # evidence collected during the current attempt
        self.no_uplink_since: float | None = None
        self.last_station: float | None = None
        self.next_probe: float | None = None
        self.probe_index = 0
        self.scan: tuple[float, list | None] | None = None
        self.scan_wanted = False
        self.trial: Trial | None = None
        self.obs = Observation()

    # ------------------------------------------------------------------ helpers
    def _go(self, state: str, now: float) -> None:
        if state != self.state:
            self.log(f"network: {self.state} -> {state}")
        self.state, self.entered = state, now

    def hotspot_allowed(self) -> bool:
        return bool(self.cfg.get("hotspot") and self.cfg.get("country")) and not self.obs.eth_up

    def _trial_on_wifi(self) -> bool:
        return bool(self.trial and self.trial.state == "pending" and self.trial.changed & {"wifi", "country"})

    def _needs_deadline(self) -> bool:
        return self.hotspot_allowed() or self._trial_on_wifi()

    def _start_client(self, now: float, probing: bool = False) -> None:
        self.system.client(self.cfg["wifi"], self.cfg["country"])
        self.reason = None
        self._go(PROBING if probing else CONNECTING, now)
        self.deadline = now + FALLBACK_S if (probing or self._needs_deadline()) else None

    def _start_hotspot(self, now: float) -> None:
        results = self.system.hotspot(self.cfg["hotspot"], self.cfg["country"])
        if results is not None:
            self.scan = (now, results)
        self._go(HOTSPOT, now)
        self.deadline, self.last_station = None, now
        self.next_probe = now + PROBE_DELAYS_S[min(self.probe_index, len(PROBE_DELAYS_S) - 1)] if self.cfg.get("wifi") else None

    def _radio_off(self, now: float) -> None:
        self.system.radio_off()
        self._go(OFF, now)
        self.deadline, self.no_uplink_since = None, (None if self.obs.eth_up else now)

    def _plan(self, now: float, boot: bool = False) -> None:
        """Choose the radio role from scratch (start, Wi-Fi interface appeared, configuration loaded)."""
        if not self.obs.wifi_present:
            self.system.radio_off()
            self._go(UNAVAILABLE, now)
            return
        if self.cfg.get("wifi"):
            self._start_client(now)
        elif self.hotspot_allowed() and not boot:
            self._start_hotspot(now)
        else:
            self._radio_off(now)

    # ------------------------------------------------------------------ inputs
    def load(self, cfg: dict) -> None:
        """Initial configuration at agent start (no trial). Ethernet first, then the radio; at boot the hotspot waits for the grace."""
        now = self.clock()
        self.cfg = cfg
        self.system.ethernet(cfg["ethernet"])
        self._plan(now, boot=True)

    def _event(self, ev: str) -> None:
        if "CTRL-EVENT-SSID-TEMP-DISABLED" in ev and "reason=WRONG_KEY" in ev:
            self.reason = "wrong_password"
        elif "CTRL-EVENT-NETWORK-NOT-FOUND" in ev and self.reason is None:
            self.reason = "not_found"
        elif "CTRL-EVENT-ASSOC-REJECT" in ev and self.reason is None:
            self.reason = "rejected"

    def tick(self, obs: Observation) -> None:
        now = self.clock()
        appeared = obs.wifi_present and not self.obs.wifi_present
        self.obs = obs
        for ev in obs.events:
            self._event(ev)
        if obs.stations > 0:
            self.last_station = now
        if self.trial and self.trial.state == "pending" and self.trial.deadline is not None and now >= self.trial.deadline:
            self._rollback(now, "not_confirmed")
        if not obs.wifi_present:
            if self.state != UNAVAILABLE:
                self.system.radio_off()
                self._go(UNAVAILABLE, now)
            return
        if appeared or self.state == UNAVAILABLE:
            self._plan(now)
            return
        wifi_ok = obs.wifi_state == "COMPLETED" and obs.wifi_address
        st = self.state
        if st in (CONNECTING, PROBING):
            if wifi_ok:
                self._connected(now)
            else:
                if self.deadline is None and self._needs_deadline():
                    self.deadline = now + FALLBACK_S
                if self.deadline is not None and now >= self.deadline:
                    self._client_failed(now)
        elif st == CONNECTED:
            if not wifi_ok:
                self.log("network: Wi-Fi connection lost")
                self._go(CONNECTING, now)
                self.reason = None
                self.deadline = now + FALLBACK_S if self._needs_deadline() else None
        elif st == HOTSPOT:
            self._hotspot_tick(now)
        elif st == OFF:
            if self.hotspot_allowed():
                if self.no_uplink_since is None:
                    self.no_uplink_since = now
                if now - self.no_uplink_since >= NO_WIFI_GRACE_S:
                    self._start_hotspot(now)
            else:
                self.no_uplink_since = None

    def _connected(self, now: float) -> None:
        self._go(CONNECTED, now)
        self.deadline, self.failure, self.reason, self.probe_index = None, None, None, 0
        if self._trial_on_wifi():
            self.trial.detail = {"wifi_connected": True, "addresses": list(self.obs.wifi_addresses)}

    def _client_failed(self, now: float) -> None:
        reason = self.reason or ("no_address" if self.obs.wifi_state == "COMPLETED" else "timeout")
        self.failure = reason
        self.log(f"network: Wi-Fi attempt failed ({reason})")
        if self._trial_on_wifi():
            self._rollback(now, reason)
            return
        if self.state == PROBING:
            self.probe_index += 1
        else:
            self.probe_index = 0
        if self.hotspot_allowed():
            self._start_hotspot(now)
        else:
            self._go(CONNECTING, now)
            self.deadline = None                  # nothing to fall back to: wpa_supplicant keeps trying on its own

    def _hotspot_tick(self, now: float) -> None:
        obs = self.obs
        idle_since = self.last_station if self.last_station is not None else self.entered
        if obs.eth_up and obs.stations == 0 and now - idle_since >= HOTSPOT_IDLE_S:
            self.log("network: uplink is back and the setup hotspot is unused - closing it")
            if self.cfg.get("wifi"):
                self._start_client(now)
            else:
                self._radio_off(now)
            return
        if not (self.cfg.get("wifi") and self.next_probe is not None and now >= self.next_probe):
            return
        if obs.stations > 0:
            self.next_probe = now + PROBE_BUSY_RETRY_S      # never take the hotspot away from a phone that is using it
            return
        if self.cfg["wifi"].get("hidden"):
            self._start_client(now, probing=True)            # a hidden network cannot be seen in a scan: just try
            return
        if self.scan is None or now - self.scan[0] > SCAN_FRESH_S:
            self.scan_wanted = True                          # decide when a fresh scan is there
            return
        results = self.scan[1]
        if results is None or any(r.get("ssid") == self.cfg["wifi"]["ssid"] for r in results):
            self._start_client(now, probing=True)
        else:
            self.probe_index += 1
            self.failure = "not_found"
            self.next_probe = now + PROBE_DELAYS_S[min(self.probe_index, len(PROBE_DELAYS_S) - 1)]

    def scan_request(self) -> str | None:
        """Agent loop: a requested scan and its mode ('ap' while the hotspot runs, else 'station'), or None."""
        if not self.scan_wanted or not self.obs.wifi_present:
            return None
        self.scan_wanted = False
        return "ap" if self.state == HOTSPOT else "station"

    def request_scan(self) -> None:
        self.scan_wanted = True

    def scan_done(self, results: list | None) -> None:
        self.scan = (self.clock(), results)

    # ------------------------------------------------------------------ owner changes (trials)
    def _affects_active(self, changed: set) -> bool:
        if "ethernet" in changed and self.obs.eth_carrier:
            return True
        if self.state == CONNECTED and changed & {"wifi", "country"}:
            return True
        return self.state == HOTSPOT and bool(changed & {"wifi", "country", "hotspot"})

    def _apply(self, new: dict, changed: set, now: float) -> None:
        st = self.state
        self.cfg = new
        if "ethernet" in changed:
            self.system.ethernet(new["ethernet"])
        if st == UNAVAILABLE:
            return
        if new.get("wifi") and changed & {"wifi", "country"}:
            self.probe_index = 0
            self._start_client(now)
        elif not new.get("wifi") and "wifi" in changed:
            self.failure = None
            if self.hotspot_allowed():
                self._start_hotspot(now)
            else:
                self._radio_off(now)
        elif st == HOTSPOT and changed & {"hotspot", "country"}:
            if new.get("hotspot") and self.hotspot_allowed():
                self._start_hotspot(now)
            elif new.get("wifi"):
                self._start_client(now)
            else:
                self._radio_off(now)

    def begin_trial(self, new: dict, trial_id: str) -> str:
        """Apply an owner change. Returns 'unchanged', 'confirmed' (no effect on a path in use) or 'pending' (must be confirmed)."""
        now = self.clock()
        changed = netconfig.changed_sections(self.cfg, new)
        pending = self.trial if self.trial and self.trial.state == "pending" else None
        if not changed and pending is None:
            return "unchanged"
        impact = self._affects_active(changed)
        base = pending.old if pending else self.cfg
        if pending is None and not impact:
            self._apply(new, changed, now)
            self.trial = Trial(trial_id, base, changed, None, state="confirmed")
            return "confirmed"
        self.trial = Trial(trial_id, base, netconfig.changed_sections(base, new), now + TRIAL_S)
        self._apply(new, changed, now)
        self.log(f"network: change {trial_id[:8]} applied, waiting for the owner's confirmation")
        return "pending"

    def confirm(self, trial_id: str) -> bool:
        t = self.trial
        if t is None or t.id != trial_id or t.state != "pending":
            return False
        t.state, t.deadline = "confirmed", None
        self.log(f"network: change {trial_id[:8]} confirmed")
        return True

    def revert(self) -> bool:
        if self.trial is None or self.trial.state != "pending":
            return False
        self._rollback(self.clock(), "reverted")
        return True

    def _rollback(self, now: float, reason: str) -> None:
        t = self.trial
        t.state, t.reason, t.deadline = "rolled_back", reason, None
        self.log(f"network: change {t.id[:8]} rolled back ({reason})")
        self._apply(t.old, netconfig.changed_sections(self.cfg, t.old), now)

    # ------------------------------------------------------------------ status (never contains a password)
    def status(self) -> dict:
        now = self.clock()
        cfg, t = self.cfg, self.trial
        wifi, hs = cfg.get("wifi"), cfg.get("hotspot")
        fallback = (max(0, round(self.deadline - now)) if self.deadline is not None and self.state in (CONNECTING, PROBING)
                    and self.hotspot_allowed() else None)
        return {
            "state": self.state,
            "state_age_s": round(now - self.entered),
            "wifi": {"configured": bool(wifi), "ssid": wifi["ssid"] if wifi else None, "hidden": bool(wifi and wifi["hidden"]),
                     "failure": self.failure, "attempt_reason": self.reason},
            "fallback_in_s": fallback,
            "hotspot": {"configured": bool(hs), "ssid": hs["ssid"] if hs else None, "active": self.state == HOTSPOT,
                        "allowed": self.hotspot_allowed(), "stations": self.obs.stations if self.state == HOTSPOT else 0,
                        "address": netconfig.HOTSPOT_ADDRESS,
                        "next_probe_in_s": max(0, round(self.next_probe - now)) if self.state == HOTSPOT and self.next_probe else None},
            "country": cfg.get("country"),
            "ethernet": {"carrier": self.obs.eth_carrier, "up": self.obs.eth_up},
            "trial": None if t is None else {"id": t.id, "state": t.state, "reason": t.reason, "changed": sorted(t.changed),
                                             "confirm_in_s": max(0, round(t.deadline - now)) if t.deadline is not None else None,
                                             "detail": dict(t.detail)},
            "scan": {"pending": self.scan_wanted, "age_s": round(now - self.scan[0]) if self.scan else None,
                     "supported": None if self.scan is None else self.scan[1] is not None,
                     "results": list(self.scan[1] or []) if self.scan else []},
        }
