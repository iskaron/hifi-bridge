"""E2 timing on the Pi's clock: from the phone's AVRCP "playing" / "paused" event (BlueZ trace) to the first / last decoded test-track block
at the speaker microphone (hwkit capture --mic). This measures bridge + network + LMS + player + speaker. NOT included: the phone's own
delay between the user's tap and the AVRCP event, and BT radio delay before the event - report those as "not measured"."""
from __future__ import annotations


def avrcp_edges(records: list[dict]) -> list[tuple[float, str]]:
    """(wall time, 'playing'|'paused'|'stopped') for every MediaPlayer1.Status change in a trace."""
    out = []
    for r in records:
        if r.get("type") == "PropertiesChanged" and r.get("interface") == "org.bluez.MediaPlayer1" and "Status" in r.get("changed", {}):
            out.append((r["t"], str(r["changed"]["Status"])))
    return out


def sound_edges(blocks: list[tuple[float, int | None]], t0: float, gap_s: float = 0.35) -> list[tuple[float, float]]:
    """Intervals (start, end) in wall time where decodable test-track blocks are heard; a pause > gap_s splits intervals."""
    times = [t0 + t for t, i in blocks if i is not None]
    if not times:
        return []
    out, start, prev = [], times[0], times[0]
    for t in times[1:]:
        if t - prev > gap_s:
            out.append((start, prev + 0.1))
            start = t
        prev = t
    out.append((start, prev + 0.1))
    return out


def measure(records: list[dict], blocks: list[tuple[float, int | None]], t0: float) -> dict:
    edges, sound = avrcp_edges(records), sound_edges(blocks, t0)
    play, pause = [], []
    for t, status in edges:
        if status == "playing":
            nxt = next((s for s, _ in sound if s >= t - 0.05), None)
            if nxt is not None:
                play.append(round(nxt - t, 3))
        elif status in ("paused", "stopped"):
            cur = next(((s, e) for s, e in sound if s <= t <= e + 0.5), None)
            if cur is not None:
                pause.append(round(max(0.0, cur[1] - t), 3))
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {"play_to_sound_s": play, "pause_to_silence_s": pause, "play_median_s": med(play), "pause_median_s": med(pause),
            "events": len(edges), "sound_intervals": len(sound),
            "not_included": "phone tap -> AVRCP event; radio delay before the event"}
