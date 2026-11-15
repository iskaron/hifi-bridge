"""Scenario: infrastructure outage while a (simulated) phone keeps playing.
Run via scripts/test_outage.sh, which stops/starts a container (lms | player1) on a timeline from the host.

Checks (docs/PLAYBACK_STATES.md): Bluetooth stays connected; degraded status is shown; no stale buffered audio
is replayed after recovery (audio resumes from the live edge); only OUR stream is ever released."""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import analyze as A
from . import signal as S
from .lab import Lab, make_bridge, save


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["lms", "player1"], required=True)
    ap.add_argument("--duration", type=float, default=100)
    a = ap.parse_args()
    lab = Lab(); lab.reset()
    rt, sim = make_bridge()
    t0 = time.time(); lab.t_reset = t0
    sim.connect(); sim.play()
    tl, last = [], None
    first_unreach = back_at = None
    bt_always_connected = True
    while time.time() - t0 < a.duration:
        st = rt.status(); now = time.time() - t0
        bt_always_connected &= (st["bluetooth_connection"] == "connected")
        key = (st["lyrion_reachability"], st["target_ownership"], st["blocked_reason"])
        if key != last:
            last = key; tl.append((round(now, 1), *key)); print(f"+{now:5.1f}s status {key}", flush=True)
        if st["lyrion_reachability"] == "unreachable" and first_unreach is None:
            first_unreach = time.time()
        if first_unreach and back_at is None and st["lyrion_reachability"] == "reachable":
            back_at = time.time()
        time.sleep(0.5)
    try:
        mode, url = lab.mode(), lab.url()
    except Exception as e:
        mode, url = f"error {e}", None
    t_end = time.time()
    ok_url = url is not None and rt.controller.session is not None and url == rt.controller.session.url
    # recovery analysis: audio that reaches the player AFTER the bridge saw the target come back
    outage_cap = lab.capture(since=first_unreach or t0, until=back_at or t_end)
    during = A.audible_fraction(outage_cap, (first_unreach or t0) + 1, (back_at or t_end) - 1) if first_unreach else None
    cap = lab.capture(since=back_at or t0, until=t_end)
    res = A.integrity(cap)
    edges_first, _ = A.edges(cap)
    stale_age = None
    if "start_index" in res and edges_first:
        idx = res["start_index"]
        produced = next((w for w, f, n in sim.sent if f <= idx < f + n), None)
        stale_age = (edges_first - produced) if produced else None
    out = {"kind": a.kind, "timeline": tl, "bt_stayed_connected": bt_always_connected,
           "lms_mode_at_end": mode, "lms_plays_current_bridge_session": ok_url,
           "outage_window_s": [round(first_unreach - t0, 1), round(back_at - t0, 1)] if first_unreach and back_at else None,
           "audible_fraction_during_outage": float(during) if during is not None else None,
           "first_audio_after_target_back_s": (edges_first - back_at) if edges_first and back_at else None,
           "age_of_first_recovered_frame_s": stale_age,
           "audio_never_stopped_during_outage": bool(during is not None and during > 0.9),
           "bridge_commands": [c[0] for c in rt.lms.commands_sent],
           "label": "INTEGRATION real LMS/player container outage + SIMULATED BT"}
    print(json.dumps(out, indent=1))
    save(f"outage_{a.kind}", out)
    rt.stop()
    never_stopped = bool(during is not None and during > 0.9)          # e.g. LMS-only outage: player kept playing the live stream itself
    good = bt_always_connected and ok_url and back_at is not None and (never_stopped or (stale_age is not None and stale_age < 6.0))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
