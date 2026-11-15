"""Replay a recorded BlueZ trace through the bridge's REAL translator (``BluezModel``) and media fusion (``MediaFusion``) on a virtual
clock.  Shows what the bridge would have decided for the recorded phone behaviour and checks operator expectations per *segment*
(from an expectation marker to the next marker or the end of the trace):
  * ``expect:media``    - media is detected at the end of the segment (the bridge would play it);
  * ``expect:no-media`` - no continuous media detection as long as the notification filter (2 s), i.e. a playing radio would not be taken.
"""
from __future__ import annotations

from btbridge.adapters.bluez.bus import InterfacesAdded, InterfacesRemoved, PropertiesChanged, ServiceAppeared, ServiceVanished
from btbridge.adapters.bluez.model import BluezModel
from btbridge.core.clock import FakeClock
from btbridge.core.fusion import MediaFusion


class _NullBus:
    def __init__(self): self.calls = []
    def call(self, path, iface, method, signature="", args=()): self.calls.append((path, iface, method))


def _event(rec: dict):
    t = rec["type"]
    if t == "PropertiesChanged":
        return PropertiesChanged(rec["path"], rec["interface"], rec["changed"], tuple(rec.get("invalidated", ())))
    if t == "InterfacesAdded":
        return InterfacesAdded(rec["path"], rec["interfaces"])
    if t == "InterfacesRemoved":
        return InterfacesRemoved(rec["path"], tuple(rec["interfaces"]))
    if t == "ServiceVanished":
        return ServiceVanished()
    if t == "ServiceAppeared":
        return ServiceAppeared()
    return None


def replay(records: list[dict], settle_s: float = 2.5, pcm_while_active: bool = True, filter_s: float = 2.0,
           max_segment_s: float = 10.0) -> dict:
    """``pcm_while_active``: traces carry no PCM, so assume audio frames flow while the transport is 'active' (stated in the result)."""
    clock = FakeClock()
    fusion = MediaFusion(clock)
    timeline, state = [], {"connected": None, "transport": "idle"}

    class CB:
        @staticmethod
        def on_connect(d): state["connected"] = d; timeline.append((clock.now(), "connect", d)); return True
        @staticmethod
        def on_disconnect(d): state["connected"] = None; fusion.reset(); timeline.append((clock.now(), "disconnect", d))
        @staticmethod
        def on_transport(s): state["transport"] = s; fusion.on_transport(s); timeline.append((clock.now(), "transport", s))
        @staticmethod
        def on_avrcp(s): fusion.on_avrcp(s); timeline.append((clock.now(), "avrcp", s))
        @staticmethod
        def on_volume(v): timeline.append((clock.now(), "volume", v))
        @staticmethod
        def on_metadata(t, a, b): timeline.append((clock.now(), "metadata", f"{a} - {t}"))
        on_pcm = staticmethod(lambda b: None)

    bus = _NullBus()
    model = BluezModel(CB, bus)
    t0 = next((r["mono"] for r in records), 0.0)
    base = clock.now()                             # virtual clock origin (FakeClock does not start at 0)
    playing_edges, last_playing = [], False
    samples: list[tuple[float, bool]] = []         # (virtual time, fused 'media playing') every <= 100 ms
    markers: list[tuple[float, str]] = []

    def advance(to):
        nonlocal last_playing
        while clock.now() < to:
            step = min(0.1, to - clock.now())
            clock.advance(step)
            if pcm_while_active and state["transport"] == "active":
                fusion.on_pcm()
            p = fusion.playing()
            samples.append((clock.now(), p))
            if p != last_playing:
                playing_edges.append((round(clock.now() - base, 3), p)); last_playing = p

    for r in records:
        advance(base + r["mono"] - t0)
        if r["type"] == "Snapshot":
            model.load(r["objects"])
        elif r["type"] == "Marker":
            timeline.append((clock.now(), "marker", r["text"]))   # converted to relative times below
            markers.append((clock.now(), r["text"]))
        else:
            ev = _event(r)
            if ev is not None:
                model.handle(ev)
    advance(clock.now() + settle_s + 0.2)
    end = clock.now()
    checks = []
    for k, (tm, text) in enumerate(markers):
        if text not in ("expect:media", "expect:no-media"):
            continue
        te = min(next((t for t, _ in markers[k + 1:] if t > tm), end), tm + max_segment_s)
        seg = [(t, p) for t, p in samples if tm <= t <= te]
        longest, run_start = 0.0, None
        for t, p in seg:
            if p and run_start is None:
                run_start = t
            if not p and run_start is not None:
                longest, run_start = max(longest, t - run_start), None
        if run_start is not None:
            longest = max(longest, te - run_start)
        at_end = seg[-1][1] if seg else False
        ok = at_end if text == "expect:media" else longest < filter_s
        checks.append({"marker": text, "segment_s": [round(tm - base, 3), round(te - base, 3)], "media_at_end": at_end,
                       "longest_media_run_s": round(longest, 3), "ok": ok})
    header = next((r for r in records if r["type"] == "Header"), {})
    return {"label": header.get("label"), "assumption": "PCM assumed present while transport is active" if pcm_while_active else None,
            "timeline": [(round(t - base, 3), k, v) for t, k, v in timeline], "media_playing_edges": playing_edges, "checks": checks,
            "all_checks_ok": all(c["ok"] for c in checks), "refused_devices": [c[0] for c in bus.calls]}
