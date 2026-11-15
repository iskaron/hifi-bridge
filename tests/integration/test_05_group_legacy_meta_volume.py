"""INTEGRATION (real LMS + Squeezelite, SIMULATED Bluetooth): sync group, legacy-capability emulation,
metadata, volume, pairing wiring.  Legacy = *capability negotiation emulation* (Squeezelite reporting only
pcm+mp3); NOT a real Squeezebox."""
import time

import pytest

from btkit import analyze as A
from btkit.lab import make_bridge, save, wait_for
from btkit.lms_rpc import PLAYERS

pytestmark = pytest.mark.integration
P = PLAYERS


def make_group(lab, members):
    for m in members[1:]:
        lab.lms.req(P[members[0]], "sync", P[m])
    assert wait_for(lambda: len((lab.lms.req("", "syncgroups", "?").get("syncgroups_loop") or [{}])[0].get("sync_members", "").split(",")) == len(members), 5)


def integrity_summary(res):
    return {k: res.get(k) for k in ("blocks", "matched", "mismatched", "jumps")}


@pytest.mark.slow
@pytest.mark.parametrize("target", ["player1", "player2"])        # master, and a slave member
def test_sync_group_as_single_target(lab, target):
    names = ["player1", "player2", "player3"]
    make_group(lab, names)
    rt, sim = make_bridge(player=target)
    try:
        sim.connect(); sim.play()
        assert wait_for(lambda: all(lab.mode(n) == "play" for n in names), 8), [lab.mode(n) for n in names]
        time.sleep(16)
        t_end = time.time(); sim.pause(); time.sleep(1)                      # drop_restart: group stops promptly
        assert all(lab.mode(n) == "stop" for n in names)
        caps = {n: lab.capture(n, until=t_end) for n in names}
        res = {n: A.integrity(c) for n, c in caps.items()}
        ref_idx = max(r["start_index"] for r in res.values()) + 4 * 44100
        offs = {}
        for n in names:
            offs[n] = [A.time_of_index(caps[n], res[n]["start_index"], ref_idx + k * 3 * 44100) for k in range(3)]
        spread_ms = [round((max(offs[n][k] for n in names) - min(offs[n][k] for n in names)) * 1000, 1) for k in range(3)]
        sync_skips = {n: [d for _, d in res[n]["jumps"] if d] for n in names}
        print(f"\ntarget={target} offsets spread ms {spread_ms}; sync correction skips (frames) {sync_skips}; bridge cmds {rt.lms.commands_sent[:3]}")
        save(f"sync_group_target_{target}", {"members": names, "bridge_target": target, "max_player_spread_ms_at_3_frames": spread_ms,
                                            "sync_correction_jumps_frames": sync_skips, "integrity": {n: integrity_summary(r) for n, r in res.items()},
                                            "bridge_only_commands": [c[0] for c in rt.lms.commands_sent],
                                            "label": "INTEGRATION real LMS sync + SIMULATED BT; virtual DACs, NOT acoustic"})
        assert max(spread_ms) < 30
        assert {c[0] for c in rt.lms.commands_sent} <= {"play", "release", "power_on", "volume_delta"}
    finally:
        rt.stop()


@pytest.mark.parametrize("fmt", ["wav", "flac"])
def test_legacy_pcm_mp3_only_player_capability_emulation(lab, fmt):
    """Player reports only pcm+mp3 (SB1-like capabilities): WAV streams direct; FLAC is transcoded by LMS."""
    rt, sim = make_bridge(player="legacy1", fmt=fmt)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode("legacy1") == "play", 8); time.sleep(10)
        t_end = time.time(); sim.disconnect(); time.sleep(1)
        cap = lab.capture("legacy1", until=t_end); res = A.integrity(cap); first, _ = A.edges(cap)
        start = first - sim.probes["t_play_event"]
        print(f"\nlegacy-emulation {fmt}: startup {start:.2f}s integrity {integrity_summary(res)}")
        save(f"legacy_emulation_{fmt}", {"startup_s": start, "integrity": integrity_summary(res),
                                         "note": "Squeezelite -c pcm,mp3 capability emulation only; real Squeezebox hardware UNVERIFIED",
                                         "label": "INTEGRATION real LMS + SIMULATED BT + EMULATED legacy capabilities"})
        assert res["mismatched"] <= 1 and all(d == 0 for _, d in res["jumps"])
    finally:
        rt.stop()


def test_metadata_reaches_lms_via_icy_without_corrupting_pcm(lab):
    rt, sim = make_bridge(icy=True)
    try:
        sim.connect(); sim.play(); assert wait_for(lambda: lab.mode() == "play", 6); time.sleep(3)
        sim.set_metadata("Blue in Green", "Miles Davis", "Kind of Blue")
        assert wait_for(lambda: "Miles Davis - Blue in Green" in str(lab.status().get("current_title", "")), 10), lab.status()
        time.sleep(6); t_end = time.time(); sim.disconnect(); time.sleep(1)
        res = A.integrity(lab.capture(until=t_end))
        assert res["mismatched"] <= 1 and all(d == 0 for _, d in res["jumps"]), res
        save("metadata_icy", {"lms_current_title": lab.status().get("current_title"), "integrity": integrity_summary(res),
                              "label": "INTEGRATION real LMS + SIMULATED AVRCP metadata; real phone AVRCP metadata UNVERIFIED"})
    finally:
        rt.stop()


def test_phone_volume_relative_bounded_and_group_semantics_recorded(lab):
    rt, sim = make_bridge()
    try:
        lab.lms.req(P["player1"], "mixer", "volume", 50)
        sim.connect(); sim.set_volume(60)                                       # reference only
        sim.play(); assert wait_for(lambda: lab.mode() == "play", 6)
        assert lab.status().get("mixer volume") == 50                           # connect/first value: no jump
        sim.set_volume(127)                                                     # big phone jump -> bounded step
        assert wait_for(lambda: lab.status().get("mixer volume") == 60, 3), lab.status().get("mixer volume")
        sim.set_volume(120); time.sleep(0.5)                                    # small phone decrease -> LMS goes DOWN, never up
        v = lab.status().get("mixer volume"); assert v < 60, v
        # ceiling: LMS volume 85 is never exceeded by phone increases
        lab.lms.req(P["player1"], "mixer", "volume", 84); sim.set_volume(10); time.sleep(0.3); sim.set_volume(127); time.sleep(0.5)
        assert lab.status().get("mixer volume") <= 85
    finally:
        rt.stop()


def test_group_volume_semantics_for_relative_changes(lab):
    """Records (does not assert) what relative `mixer volume +/-N` on one member does to the others."""
    make_group(lab, ["player1", "player2"])
    lab.lms.req(P["player1"], "mixer", "volume", 60); lab.lms.req(P["player2"], "mixer", "volume", 30)
    before = {n: lab.status(n).get("mixer volume") for n in ("player1", "player2")}
    lab.lms.req(P["player1"], "mixer", "volume", "-10"); time.sleep(0.5)
    after = {n: lab.status(n).get("mixer volume") for n in ("player1", "player2")}
    print("\ngroup volume before", before, "after relative -10 on player1:", after)
    save("group_volume_semantics", {"before": before, "after_relative_minus10_on_player1": after,
                                    "label": "REAL LMS (JSON-RPC only); sync-group volume semantics as observed in lab"})
    assert after["player1"] == before["player1"] - 10


def test_pairing_window_wiring_to_adapter_and_expiry(lab):
    rt, sim = make_bridge(pairing_s=1.5)
    try:
        assert not sim.pairable
        rt.pairing.open(); assert sim.pairable
        assert wait_for(lambda: not sim.pairable, 4) and not rt.pairing.is_open()
    finally:
        rt.stop()
