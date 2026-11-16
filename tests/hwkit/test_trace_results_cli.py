"""Kit tests: trace recording against the FAKE BlueZ on a private D-Bus (SIMULATED-BLUEZ), replay through the bridge's translator,
results-file rules, catalogue consistency, environment capture with a fake runner, PCM capture with a fake capture source."""
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

from hwkit import results as R
from hwkit.anon import Anonymizer
from hwkit.env import collect
from hwkit.replay import replay
from hwkit.trace import TraceRecorder, load

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---- trace + replay against the fake BlueZ ------------------------------------------------------------------------------
@pytest.fixture
def fake_bluez():
    pytest.importorskip("jeepney")
    from btkit.fake_bluez import FakeBluez, PrivateBus
    pb = PrivateBus(); fb = FakeBluez(pb.address); fb.start()
    yield pb, fb
    fb.stop(); pb.close()


def scenario(fb, rec):
    """Hand-written phone behaviour (SIMULATED): a notification beep, then real music, then pause, then disconnect."""
    dev = fb.add_device(1)
    fb.add_object(f"{dev}/player0", {"org.bluez.MediaPlayer1": {"Device": ("o", dev), "Status": "paused"}})
    fb.add_object(f"{dev}/fd0", {"org.bluez.MediaTransport1": {"Device": ("o", dev), "State": "idle", "Volume": ("q", 70)}})
    fb.set_props(dev, "org.bluez.Device1", Connected=True); time.sleep(0.3)
    rec.marker("notification beep"); rec.marker("expect:no-media")
    fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="active"); time.sleep(0.8)
    fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="idle"); time.sleep(0.3)
    rec.marker("spotify play"); rec.marker("expect:media")
    fb.set_props(f"{dev}/fd0", "org.bluez.MediaTransport1", State="active")
    fb.set_props(f"{dev}/player0", "org.bluez.MediaPlayer1", Status="playing"); time.sleep(3.0)
    rec.marker("pause"); rec.marker("expect:no-media")
    fb.set_props(f"{dev}/player0", "org.bluez.MediaPlayer1", Status="paused"); time.sleep(3.0)
    d2 = fb.add_device(2); fb.set_props(d2, "org.bluez.Device1", Connected=True); time.sleep(0.3)   # second phone
    fb.set_props(dev, "org.bluez.Device1", Connected=False); time.sleep(0.3)


def test_trace_records_anonymised_events_and_replay_checks_expectations(fake_bluez, tmp_path):
    from btbridge.adapters.bluez.jeepney_bus import JeepneyBus
    pb, fb = fake_bluez
    path = tmp_path / "trace.jsonl"
    rec = TraceRecorder(JeepneyBus(bus=pb.address), str(path), label="SIMULATED-BLUEZ")
    rec.start(); scenario(fb, rec); time.sleep(0.3); rec.stop()
    text = path.read_text()
    assert "AA:BB:CC:DD:EE:01" not in text and "AA_BB_CC_DD_EE_01" not in text            # pseudonymised
    records = load(str(path))
    assert records[0]["type"] == "Header" and records[0]["label"] == "SIMULATED-BLUEZ" and records[-1]["type"] == "End"
    kinds = {r["type"] for r in records}
    assert {"Snapshot", "InterfacesAdded", "PropertiesChanged", "Marker"} <= kinds
    res = replay(records)
    assert [c["marker"] for c in res["checks"]] == ["expect:no-media", "expect:media", "expect:no-media"]
    assert res["all_checks_ok"], res["checks"]
    connects = [v for _, k, v in res["timeline"] if k == "connect"]
    assert len(connects) == 1 and len(res["refused_devices"]) == 1                          # second phone refused, first kept
    assert connects[0].replace(":", "_") not in res["refused_devices"][0]
    # keep this run as the (SIMULATED) example fixture for the fixture replay unit test
    if os.environ.get("HWKIT_WRITE_FIXTURE"):
        (open(os.path.join(ROOT, "tests/fixtures/traces/simulated_notification_play_pause.jsonl"), "w")).write(text)


def test_replay_flags_a_wrong_expectation(tmp_path):
    recs = [{"type": "Header", "label": "SIMULATED", "t": 0, "mono": 0.0},
            {"type": "Snapshot", "objects": {}, "t": 0, "mono": 0.0},
            {"type": "Marker", "text": "expect:media", "t": 0, "mono": 0.1}]
    res = replay(recs)
    assert res["checks"] and res["all_checks_ok"] is False


def test_anonymizer_is_stable_and_covers_both_mac_spellings():
    a = Anonymizer()
    s = a.text("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01 and aa:bb:cc:dd:ee:01 and 11:22:33:44:55:66")
    assert s == "/org/bluez/hci0/dev_02_00_00_00_00_01 and 02:00:00:00:00:01 and 02:00:00:00:00:02"
    assert Anonymizer(enabled=False).text("AA:BB:CC:DD:EE:01") == "AA:BB:CC:DD:EE:01"


# ---- results -------------------------------------------------------------------------------------------------------------
def test_results_lifecycle_and_rules(tmp_path):
    f = str(tmp_path / "results.json")
    R.new_session(f, "Pi 3 B+ rev 1.3", "owner", ["Pixel 8 / Android 15"], ["Squeezebox Boom fw 7.7.3"])
    probs = R.check(f)
    assert "env.json missing (run: python3 -m hwkit env)" in probs and len([p for p in probs if "missing" in p]) == 1
    (tmp_path / "env.json").write_text("{}")
    assert R.check(f) == []                                                  # all NOT_RUN is consistent
    with pytest.raises(KeyError):
        R.set_result(f, "Z9", "PASS", "x")
    with pytest.raises(ValueError):
        R.set_result(f, "B1", "GREEN", "x")
    R.set_result(f, "B1", "PASS", "paired in 20 s", evidence=["missing.jsonl"], devices=["Pixel 8"])
    assert any("evidence file not found" in p for p in R.check(f))
    (tmp_path / "trace_b1.jsonl").write_text("{}\n")
    R.set_result(f, "B1", "PASS", "paired in 20 s", evidence=["trace_b1.jsonl"], devices=["Pixel 8"])
    assert R.check(f) == [] and R.load(f)["tests"][7]["label"] == "REAL-HARDWARE"
    doc = R.load(f); doc["tests"][0].update(status="PASS", label="SIMULATED", observations="x", evidence=["trace_b1.jsonl"], devices=["d"])
    R.save(f, doc)
    assert "A1: executed test must be labelled REAL-HARDWARE" in R.check(f)  # simulated results cannot pose as hardware results
    rep = R.report(f)
    assert "| B1 |" in rep and "PASS" in rep and "A1: executed test must be labelled REAL-HARDWARE" in rep


def test_catalog_matches_the_validation_plan():
    plan = open(os.path.join(ROOT, "docs/HARDWARE_VALIDATION_PLAN.md")).read()
    ids_plan = re.findall(r"^\| ([A-H]\d) \|", plan, re.M)
    assert [t["id"] for t in R.catalog()] == ids_plan and len(ids_plan) == len(set(ids_plan))


# ---- env + capture -----------------------------------------------------------------------------------------------------------
def test_env_collect_with_fake_runner_marks_missing_tools():
    def runner(argv, timeout=5):
        return {"bluetoothd": "5.72", "pipewire": "pipewire 1.2.7"}.get(argv[0])
    e = collect(runner=runner, reader=lambda p: "Raspberry Pi 3 Model B Plus Rev 1.3" if "model" in p else None)
    assert e["bluetoothd"] == "5.72" and e["board_model"].startswith("Raspberry Pi 3")
    assert "wireplumber" in e["missing"] and "bluetoothd" not in e["missing"]


def test_capture_writes_wav_and_timestamps_with_a_fake_capture(tmp_path):
    import numpy as np
    from hwkit import analyze as A
    from hwkit import capture as C
    from hwkit import signals as S
    pcm = S.to_int16_stereo(S.indexed_track(3, first_index=7)).tobytes()

    class FakeCap:
        def __init__(self, argv_for, present=None, **kw): self.argv_for, self.t = argv_for, None
        def start(self, node, on_pcm):
            def run():
                for k in range(0, len(pcm), 3528):
                    on_pcm(pcm[k:k + 3528]); time.sleep(0.001)
            self.t = threading.Thread(target=run); self.t.start()
        def stop(self): self.t.join()
    meta = C.record(str(tmp_path), "AA:BB:CC:DD:EE:01", 0.5, capture_factory=FakeCap, label="SIMULATED")
    assert meta["nodes"] == {"phone": "bluez_input.AA_BB_CC_DD_EE_01"} and meta["label"] == "SIMULATED"
    assert meta["recordings"]["phone"]["first_sample_wall"] and meta["recordings"]["phone"]["seconds"] == 3.0
    x, rate = A.read_wav(str(tmp_path / "phone.wav"))
    c = A.continuity(A.find_blocks(x, rate))
    assert c["clean"] and c["first"] == 7 and c["last"] == 36


def test_cli_results_and_help(tmp_path):
    env = dict(os.environ)
    run = lambda *a: subprocess.run([sys.executable, "-m", "hwkit", *a], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert "replay" in run("--help").stdout
    f = str(tmp_path / "r.json")
    assert run("results", "new", f, "--board", "Pi 4", "--operator", "o").returncode == 0
    assert run("results", "check", f).returncode == 1                       # env.json missing
    (tmp_path / "env.json").write_text("{}")
    assert run("results", "check", f).returncode == 0
    assert "| G1 |" in run("results", "report", f).stdout
