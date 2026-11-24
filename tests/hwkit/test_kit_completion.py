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
    show_g1_before = "Controller B8:27:EB:00:00:01 (public)\n\tUUID: Generic Access Profile (00001800-0000-1000-8000-00805f9b34fb)"
    show_good = show_g1_before + "\n\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)"
    def runner_for(show):
        return lambda argv, timeout=5: {"bluetoothd": "5.82", "wireplumber": "wireplumber\nCompiled with libwireplumber 0.5.8",
                                         "pactl": "40 alsa_output.x.monitor PipeWire s16le 2ch 44100Hz IDLE",
                                         "systemctl": "multi-user.target", "bluetoothctl": show}.get(argv[0])
    model = {"/proc/device-tree/model": "Raspberry Pi 3 Model B Plus Rev 1.3"}
    # the G1 state (2026-11-24): logind present (loginctl exists), headless, no seat override, no role rule, no BlueZ plugin
    bad = checks(runner=runner_for(show_g1_before), reader=fake_env(**model), which=lambda t: f"/usr/bin/{t}" if t in tools | {"loginctl"} else None,
                 globber=lambda pat, **kw: [], home=str(tmp_path))
    status = {c["check"]: c["status"] for c in bad}
    assert status["pipewire:allowed-rates"] == "FAIL" and status["board"] == "PASS"
    assert status["wireplumber:seat-monitoring"] == "FAIL"           # NOT passed just because loginctl exists (old rule)
    assert status["wireplumber:a2dp-input-role"] == "FAIL" and status["pipewire:bluez5-plugin"] == "FAIL"
    assert status["bluez:audio-sink-uuid"] == "FAIL"
    text, rc = report(bad)
    assert rc == 1 and "fix:" in text and "10-hifi-bridge-rates.conf" in text and "libspa-0.2-bluetooth" in text
    assert "50-hifi-bridge-a2dp-input.conf" in text and "--headless" in text
    d = os.path.join(ROOT, "hwkit", "pi-setup")
    conf = {"/etc/pipewire/pipewire.conf.d/10.conf": "context.properties = { default.clock.allowed-rates = [ 44100 48000 ] }",
            "/etc/wireplumber/wireplumber.conf.d/80-no-logind.conf": open(os.path.join(d, "wireplumber-no-logind.conf")).read(),
            "/etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf": open(os.path.join(d, "50-hifi-bridge-a2dp-input.conf")).read(),
            "/usr/lib/aarch64-linux-gnu/spa-0.2/bluez5/libspa-bluez5.so": "\x7fELF", **model}
    def globber(pat, **kw):
        prefix = pat.split("*")[0]
        return [p for p in conf if p.startswith(prefix) and p.endswith(pat.rsplit("*", 1)[-1])]
    good = checks(runner=runner_for(show_good), reader=fake_env(**conf), which=lambda t: f"/usr/bin/{t}" if t in tools else None,
                  globber=globber, home=str(tmp_path))
    st = {c["check"]: c["status"] for c in good}
    assert st["pipewire:allowed-rates"] == "PASS" and st["wireplumber:seat-monitoring"] == "PASS"
    assert st["wireplumber:a2dp-input-role"] == "PASS" and st["pipewire:bluez5-plugin"] == "PASS" and st["bluez:audio-sink-uuid"] == "PASS"
    cap = next(c for c in good if c["check"] == "capture:tool")
    assert cap["status"] == "PASS" and "parec" in cap["detail"]          # fake pw-cat has no --raw in this runner -> parec


def test_preflight_rejects_wireplumber_0_4_for_the_input_role(tmp_path):
    runner = lambda argv, timeout=5: {"wireplumber": "Compiled with libwireplumber 0.4.13", "systemctl": "multi-user.target"}.get(argv[0])
    conf = {"/etc/wireplumber/main.lua.d/x.lua": 'bluez5.media-source-role = "input"'}
    r = checks(runner=runner, reader=fake_env(**conf), which=lambda t: None, globber=lambda pat, **kw: list(conf), home=str(tmp_path))
    role = next(c for c in r if c["check"] == "wireplumber:a2dp-input-role")
    assert role["status"] == "FAIL" and "0.4" in role["detail"]


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


def _dry_run(tmp_path, *args, target="multi-user.target", model="Raspberry Pi 3 Model B Plus Rev 1.3"):
    root = tmp_path / "root"
    (root / "proc" / "device-tree").mkdir(parents=True)
    (root / "proc" / "device-tree" / "model").write_bytes(model.encode() + b"\0")
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    os.symlink(f"/lib/systemd/system/{target}", root / "etc" / "systemd" / "system" / "default.target")
    script = os.path.join(ROOT, "hwkit", "pi-setup", "setup_test_pi.sh")
    env = {**os.environ, "HIFI_SETUP_ROOT": str(root)}
    env.pop("SUDO_USER", None)
    r = subprocess.run(["sh", script, "--i-am-on-the-test-pi", *args], capture_output=True, text=True, env=env, timeout=30)
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "proc" not in p.parts)
    return r, files


def test_pi_setup_installs_the_bluez_plugin_and_the_input_role_and_handles_headless_with_logind(tmp_path):
    """G1 (2026-11-24): libspa-0.2-bluetooth was missing under --no-install-recommends, and the seat override was installed only when
    loginctl was missing - but a headless Pi OS Lite HAS logind. DRY RUN: nothing outside the temporary root, no package, no service."""
    r, files = _dry_run(tmp_path)
    assert r.returncode == 0, r.stderr
    pkgs = r.stdout.split("would install:")[1].splitlines()[0].split()
    assert "libspa-0.2-bluetooth" in pkgs and "pipewire-bin" in pkgs and "wireplumber" in pkgs
    assert "etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf" in files
    assert "etc/wireplumber/wireplumber.conf.d/80-no-logind.conf" in files            # headless (multi-user.target)
    assert "etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf" in files
    again, files2 = _dry_run(tmp_path / "second")                                       # idempotent: same result on a second run
    assert again.returncode == 0 and files2 == files


def test_pi_setup_keeps_seat_monitoring_on_a_desktop_unless_told_headless(tmp_path):
    r, files = _dry_run(tmp_path / "a", target="graphical.target")
    assert r.returncode == 0 and "etc/wireplumber/wireplumber.conf.d/80-no-logind.conf" not in files
    assert "etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf" in files
    r, files = _dry_run(tmp_path / "b", "--headless", target="graphical.target")
    assert "etc/wireplumber/wireplumber.conf.d/80-no-logind.conf" in files
    r, files = _dry_run(tmp_path / "c", "--desktop")
    assert "etc/wireplumber/wireplumber.conf.d/80-no-logind.conf" not in files
    r, _ = _dry_run(tmp_path / "d", model="Some PC")
    assert r.returncode == 3


def test_pi_setup_does_not_recommend_just_works_repairing():
    snippet = open(os.path.join(ROOT, "hwkit", "pi-setup", "main.conf.snippet")).read()
    assert not any(ln.strip().lstrip("#").strip().startswith("JustWorksRepairing") for ln in snippet.splitlines())


def test_pi_setup_files_contain_the_lab_findings():
    d = os.path.join(ROOT, "hwkit", "pi-setup")
    assert "allowed-rates = [ 44100 48000 ]" in open(os.path.join(d, "10-hifi-bridge-rates.conf")).read()
    assert 'with-logind"] = false' in open(os.path.join(d, "wireplumber-no-logind.lua")).read()
    assert "seat-monitoring = disabled" in open(os.path.join(d, "wireplumber-no-logind.conf")).read()
    role = open(os.path.join(d, "50-hifi-bridge-a2dp-input.conf")).read()
    assert 'bluez5.media-source-role = "input"' in role and 'node.name = "~bluez_input.*"' in role
