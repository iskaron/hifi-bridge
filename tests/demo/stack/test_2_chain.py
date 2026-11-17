"""DEMO STACK, part 2 (demo image): the complete audio chain with SIMULATED phones and rooms, REAL bridge/Lyrion/Squeezelite/PipeWire.

phone (console) -> virtual bluetoothd -> PipeWire node -> bridge (pw-cat, BlueZ adapter over D-Bus) -> HTTP stream -> Lyrion
-> Squeezelite "Wohnzimmer" -> virtual DAC. The tests prove with cross-correlation that the room outputs the phone's track, and measure the
software latency phone -> room DAC (SIMULATED phone and speaker: not acoustic, not Bluetooth). Order matters (one shared demo)."""
import time

import numpy as np
import pytest

from demo_env import capture, lms, lms_status, phone, phone_state, save_json, wait
from hifidemo import music

RESULTS: dict = {"label": "SIMULATED phone and rooms; real bridge, Lyrion 9, Squeezelite, PipeWire 1.4 (Docker, no hardware)"}
RATE = music.RATE


def mono(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % 4], dtype="<i2").reshape(-1, 2).astype(np.float64).mean(axis=1)


def locate(segment: np.ndarray, track: np.ndarray) -> tuple[int, float]:
    """Where in the track does the captured segment occur? (offset in frames, normalised correlation)"""
    n = len(segment)
    size = 1 << int(np.ceil(np.log2(len(track) + n)))
    corr = np.fft.irfft(np.fft.rfft(track, size) * np.conj(np.fft.rfft(segment, size)), size)[: len(track) - n]
    k = int(np.argmax(corr))
    win = track[k: k + n]
    score = float(np.dot(win, segment) / (np.linalg.norm(win) * np.linalg.norm(segment) + 1e-9))
    return k, score


def room_plays_phone_track(room: str, pid: str, seconds: float = 3.0) -> dict:
    """Capture the room's output and find it in the phone's current track; also derive phone -> DAC latency."""
    st = phone_state(pid)
    t_snap, pos_snap = time.time(), st["position_s"]
    from hifidemo.phones import Library
    track = mono(Library("/tmp/lib").pcm(st["track"]["id"]))
    frames = capture(room, seconds)
    pcm = b"".join(p for _, p in frames)
    seg = mono(pcm)[-int(1.5 * RATE):]
    assert np.sqrt(np.mean(seg ** 2)) > 200, f"{room} is silent"
    # The generated pieces repeat their chord loop, so only search where the frame can physically be: already sent by the phone
    # (<= its current position) and at most 20 s ago. Without this bound a later, similar bar can match (found by this test).
    hi = int((pos_snap + (time.time() - t_snap)) * RATE)
    lo = max(0, hi - 20 * RATE)
    k, score = locate(seg, track[lo:hi])
    k += lo
    t_dac_last = frames[-1][0]
    t_sent = t_snap + ((k + len(seg)) / RATE - pos_snap)            # when the phone sent the last captured frame
    return {"score": round(score, 3), "offset_s": round(k / RATE, 2), "phone_to_dac_s": round(t_dac_last - t_sent, 2)}


def test_01_guest_pairs_and_connects_inside_the_window():
    vis = phone("a", "scan")["visible"]
    assert vis and vis[0]["name"] == "Hi-Fi Bridge Wohnzimmer"            # the name the owner chose in the wizard
    assert phone("a", "pair")["ok"]
    assert phone("a", "connect")["ok"]
    assert phone_state("a")["connected"]


def test_02_play_reaches_the_room_behind_lyrion_and_squeezelite():
    t0 = time.time()
    assert phone("a", "play")["ok"]
    st = wait(lambda: (s := lms_status("Wohnzimmer")).get("mode") == "play" and "/s/" in s["url"] and s, 20, what="Lyrion plays the bridge stream")
    RESULTS["play_to_lyrion_play_s"] = round(time.time() - t0, 2)
    assert "hifi-bridge.local" in st["url"]
    def audible():
        f = capture("Wohnzimmer", 0.5)
        return f and np.sqrt(np.mean(mono(b"".join(p for _, p in f)) ** 2)) > 200
    wait(audible, 25, step=0.2, what="audio at the Wohnzimmer DAC")
    RESULTS["play_to_first_audio_at_room_s"] = round(time.time() - t0, 2)
    time.sleep(2)
    m = room_plays_phone_track("Wohnzimmer", "a")
    RESULTS["chain"] = m
    assert m["score"] > 0.9, m                                            # the room plays exactly the phone's track
    assert 0 < m["phone_to_dac_s"] < 10, m


def test_03_track_change_reaches_the_room():
    """The new track plays in the room (titles in Lyrion need the owner's ICY setting: tested in part 3)."""
    before = phone_state("a")["track"]["title"]
    phone("a", "next")
    assert phone_state("a")["track"]["title"] != before
    time.sleep(4)
    m = room_plays_phone_track("Wohnzimmer", "a")
    RESULTS["after_track_change"] = m
    assert m["score"] > 0.9, m


def test_04_phone_volume_changes_the_room_volume():
    v0 = int(lms_status("Wohnzimmer")["mixer volume"])
    phone("a", "volume", value=40)                                        # from 80: roughly half
    v1 = wait(lambda: (v := int(lms_status("Wohnzimmer")["mixer volume"])) != v0 and v, 10, what="Lyrion volume follows")
    RESULTS["volume"] = {"before": v0, "after_phone_40_of_127": v1}
    assert v1 < v0
    phone("a", "volume", value=80)


def test_05_second_phone_is_refused_and_never_interrupts_the_first():
    assert phone("b", "scan")["visible"]
    assert phone("b", "pair")["ok"]
    r = phone("b", "connect")
    assert r == {"ok": False, "message": "refused"}
    assert phone_state("a")["connected"] and lms_status("Wohnzimmer")["mode"] == "play"


def test_06_pause_and_resume():
    phone("a", "pause")
    wait(lambda: lms_status("Wohnzimmer")["mode"] != "play", 15, what="Lyrion stops/pauses after the phone pauses")
    t0 = time.time()
    phone("a", "play")
    wait(lambda: lms_status("Wohnzimmer")["mode"] == "play", 20, what="resume")
    RESULTS["resume_to_lyrion_play_s"] = round(time.time() - t0, 2)


def test_07_short_notification_does_not_take_over_the_radio_but_music_does():
    phone("a", "pause")
    phone("a", "disconnect")
    wait(lambda: lms_status("Wohnzimmer")["mode"] != "play", 15, what="bridge released the room")
    from demo_env import console
    pid = lms("", "players", 0, 10)["players_loop"]
    wz = next(p["playerid"] for p in pid if p["name"] == "Wohnzimmer")
    console("/api/lms/radio", {"player": wz})
    wait(lambda: "radio.wav" in lms_status("Wohnzimmer")["url"] and lms_status("Wohnzimmer")["mode"] == "play", 20, what="radio plays")
    assert phone("a", "connect")["ok"]
    phone("a", "notify")                                                 # 0.7 s chime: must NOT interrupt the radio
    time.sleep(5)
    st = lms_status("Wohnzimmer")
    RESULTS["notification_vs_radio"] = {"radio_kept": "radio.wav" in st["url"]}
    assert "radio.wav" in st["url"] and st["mode"] == "play", st
    t0 = time.time()
    phone("a", "play")                                                   # real music: takes over after the notification filter
    wait(lambda: "/s/" in lms_status("Wohnzimmer")["url"] and lms_status("Wohnzimmer")["mode"] == "play", 20, what="bridge takes over")
    RESULTS["takeover_from_radio_s"] = round(time.time() - t0, 2)
    time.sleep(3)
    assert room_plays_phone_track("Wohnzimmer", "a")["score"] > 0.9


def test_08_synchronised_group_plays_in_two_rooms():
    from demo_env import ROOM_MAC, console
    console("/api/lms/sync", {"players": [ROOM_MAC["Wohnzimmer"], ROOM_MAC["Schlafzimmer"]]})
    time.sleep(6)                                                        # Lyrion re-buffers the group
    m = room_plays_phone_track("Schlafzimmer", "a")
    RESULTS["multiroom_schlafzimmer"] = m
    assert m["score"] > 0.9, m
    console("/api/lms/unsync", {"players": [ROOM_MAC["Schlafzimmer"]]})


def test_09_connection_loss_stops_the_room():
    t0 = time.time()
    phone("a", "drop")
    wait(lambda: lms_status("Wohnzimmer")["mode"] != "play", 15, what="room stops after link loss")
    RESULTS["link_loss_to_stop_s"] = round(time.time() - t0, 2)


def test_10_manual_source_change_the_bridge_steps_back_and_stays_back():
    """Someone starts other music on the room in Lyrion while the phone plays: the bridge must not fight for the room."""
    from demo_env import ROOM_MAC, console
    assert phone("a", "connect")["ok"] and phone("a", "play")["ok"]
    wait(lambda: "/s/" in lms_status("Wohnzimmer")["url"] and lms_status("Wohnzimmer")["mode"] == "play", 20, what="bridge plays")
    console("/api/lms/radio", {"player": ROOM_MAC["Wohnzimmer"]})          # the 'manual' change, as in Lyrion's own UI
    wait(lambda: "radio.wav" in lms_status("Wohnzimmer")["url"], 15, what="radio started by hand")
    time.sleep(8)                                                          # phone keeps sending music all the time
    st = lms_status("Wohnzimmer")
    RESULTS["manual_change_respected"] = "radio.wav" in st["url"] and st["mode"] == "play"
    assert RESULTS["manual_change_respected"], st
    phone("a", "pause")
    console("/api/lms/stop", {"player": ROOM_MAC["Wohnzimmer"]})


def test_99_write_measurements():
    from demo_env import STATE_DIR
    save_json(f"{STATE_DIR}/demo_chain.json", RESULTS)
