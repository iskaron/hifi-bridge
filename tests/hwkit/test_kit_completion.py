"""Kit completion (UNIT, SIMULATED inputs): preflight with fake tools/configs, E2 latency analysis on synthetic data, guided session
with scripted operator input and fake actions, procedure/catalogue consistency, the Pi setup script's refusal guards."""
import json
import os
import subprocess

import pytest

from hwkit import analyze as A
from hwkit import results as R
from hwkit import signals as S
from hwkit.guide import procedures, run
from hwkit.latency import measure
from hwkit.preflight import checks, report

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_every_catalogue_test_has_a_procedure_with_steps():
    procs = procedures()
    assert set(procs) == {t["id"] for t in R.catalog()}
    assert all(p["steps"] for p in procs.values())
    assert {a for p in procs.values() for a in p["auto"]} <= {"env", "trace", "nodes", "capture", "capture_long", "capture_mic"}


def fake_env(**files):
    def reader(p):
        return files.get(p)
    return reader


def test_preflight_flags_the_lab_findings_and_passes_a_good_setup(tmp_path):
    tools = {"bluetoothd", "bluetoothctl", "pipewire", "wireplumber", "pactl", "parec", "pw-dump", "busctl"}
    runner = lambda argv, timeout=5: {"bluetoothd": "5.72", "wireplumber": "wireplumber\nCompiled with libwireplumber 0.4.13",
                                       "pactl": "40 bluez_input.X PipeWire s16le 2ch 44100Hz IDLE"}.get(argv[0])
    bad = checks(runner=runner, reader=fake_env(**{"/proc/device-tree/model": "Raspberry Pi 3 Model B Plus Rev 1.3"}),
                 which=lambda t: f"/usr/bin/{t}" if t in tools else None, globber=lambda pat, **kw: [], home=str(tmp_path))
    status = {c["check"]: c["status"] for c in bad}
    assert status["pipewire:allowed-rates"] == "FAIL" and status["wireplumber:logind"] == "FAIL" and status["board"] == "PASS"
    text, rc = report(bad)
    assert rc == 1 and "fix:" in text and "10-hifi-bridge-rates.conf" in text
    conf = {"/etc/pipewire/pipewire.conf.d/10.conf": "context.properties = { default.clock.allowed-rates = [ 44100 48000 ] }",
            "/etc/wireplumber/bluetooth.lua.d/80.lua": 'bluez_monitor.properties["with-logind"] = false',
            "/proc/device-tree/model": "Raspberry Pi 3 Model B Plus Rev 1.3"}
    good = checks(runner=runner, reader=fake_env(**conf), which=lambda t: f"/usr/bin/{t}" if t in tools else None,
                  globber=lambda pat, **kw: [p for p in conf if p.startswith(pat.split("*")[0])], home=str(tmp_path))
    assert {c["check"]: c["status"] for c in good}["pipewire:allowed-rates"] == "PASS"
    assert {c["check"]: c["status"] for c in good}["wireplumber:logind"] == "PASS"
    cap = next(c for c in good if c["check"] == "capture:tool")
    assert cap["status"] == "PASS" and "parec" in cap["detail"]          # fake pw-cat has no --raw in this runner -> parec


def test_e2_latency_from_trace_events_and_microphone_blocks():
    # synthetic: AVRCP playing at t=100.0, the track is heard from 101.6 s; paused at 110.0, sound ends 110.2
    x = S.indexed_track(8.6, first_index=1)                         # 8.6 s of blocks
    blocks = A.find_blocks(x, S.RATE)
    t0 = 101.6                                                       # wall time of the mic recording's first sample
    rec = [{"type": "PropertiesChanged", "interface": "org.bluez.MediaPlayer1", "changed": {"Status": "playing"}, "t": 100.0},
           {"type": "PropertiesChanged", "interface": "org.bluez.MediaPlayer1", "changed": {"Status": "paused"}, "t": 110.0}]
    m = measure(rec, blocks, t0)
    assert abs(m["play_median_s"] - 1.6) < 0.02 and abs(m["pause_median_s"] - 0.2) < 0.12 and m["events"] == 2
    assert "not_included" in m


class FakeActions:
    def __init__(self): self.calls = []
    def env(self, d): open(os.path.join(d, "env.json"), "w").write("{}"); self.calls.append("env"); return ["env.json"]
    def nodes(self, d): open(os.path.join(d, "sources.txt"), "w").write("x"); self.calls.append("nodes"); return ["sources.txt"]
    def trace(self, d, body):
        marks = []; body(marks.append); open(os.path.join(d, "trace.jsonl"), "w").write(json.dumps(marks))
        self.calls.append(("trace", marks)); return ["trace.jsonl"]
    def capture(self, d, seconds=60, mic=False):
        os.makedirs(os.path.join(d, "capture"), exist_ok=True); open(os.path.join(d, "capture", "capture.json"), "w").write("{}")
        self.calls.append(("capture", seconds, mic)); return ["capture/capture.json"]


def test_guided_session_records_verdicts_with_evidence(tmp_path):
    f = str(tmp_path / "results.json")
    R.new_session(f, "Pi 3 B+", "owner", ["Pixel"], ["Squeezelite"])
    (tmp_path / "env.json").write_text("{}")
    answers = iter(["y", "", "PARTIAL", "codec SBC only", "Pixel 8",                                                  # A5 (catalogue order)
                    "y", "android play", "expect:media", "", "PASS", "music detected", "Pixel 8 / Android 15 / Spotify",   # C1
                    "s"])                                                                                                   # C2 skipped
    act = FakeActions()
    done = run(f, ask=lambda q: next(answers), say=lambda m: None, only=["C1", "C2", "A5"], actions=act)
    assert done == {"A5": "PARTIAL", "C1": "PASS"}
    assert ("trace", ["android play", "expect:media"]) in act.calls and "nodes" in act.calls
    doc = R.load(f)
    c1 = next(t for t in doc["tests"] if t["id"] == "C1")
    assert c1["label"] == "REAL-HARDWARE" and c1["evidence"][0].startswith("sessions/C1/") and c1["devices"] == ["Pixel 8 / Android 15 / Spotify"]
    assert R.check(f) == []


def test_pi_setup_script_refuses_without_confirmation_and_off_a_pi():
    script = os.path.join(ROOT, "hwkit", "pi-setup", "setup_test_pi.sh")
    r = subprocess.run(["sh", script], capture_output=True, text=True)
    assert r.returncode == 2 and "refusing" in r.stderr
    r = subprocess.run(["sh", script, "--i-am-on-the-test-pi"], capture_output=True, text=True)
    assert r.returncode == 3 and "not a Raspberry Pi" in r.stderr                  # the lab container is not a Pi: nothing changed


def test_pi_setup_files_contain_the_lab_findings():
    d = os.path.join(ROOT, "hwkit", "pi-setup")
    assert "allowed-rates = [ 44100 48000 ]" in open(os.path.join(d, "10-hifi-bridge-rates.conf")).read()
    assert 'with-logind"] = false' in open(os.path.join(d, "wireplumber-no-logind.lua")).read()
    assert "seat-monitoring = disabled" in open(os.path.join(d, "wireplumber-no-logind.conf")).read()
