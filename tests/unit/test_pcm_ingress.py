"""UNIT: Bluetooth PCM ingress - discovery of the phone's PipeWire node and the capture lifecycle (G1 real-hardware finding,
2026-11-24, docs/PIPEWIRE_CAPTURE.md section "G1").

On the real Pi the phone's decoded A2DP audio was a PipeWire *playback stream* (`bluez_input.<MAC>.2`, Stream/Output/Audio) that
WirePlumber played on the Pi's headphone jack; the bridge targeted a guessed name without the `.2` and never got audio. These tests
replay that topology (tests/fixtures/pipewire, SIMULATED from the observation) and the capture/adapter behaviour around it.
Label: UNIT/SIMULATED - real child processes where a process is involved, no PipeWire daemon (that is tests/pipewire)."""
import json
import os
import subprocess
import sys
import time

import pytest
from fakes_bluez import FakeBus, Recorder, addr, dev_path

from btbridge.adapters.bluez import BluezAdapter, PipeWireCapture
from btbridge.adapters.bluez.bus import IF_TRANSPORT
from btbridge.adapters.bluez.pcm import CHUNK_BYTES, find_phone_node, parec_argv, record_argv

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pipewire")
PHONE, OTHER = "C0:FF:EE:00:00:01", "C0:FF:EE:00:00:02"


def dump(name):
    with open(os.path.join(FIX, name)) as fh:
        return json.load(fh)


def runner(stdout="", rc=0, exc=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, rc, stdout, "")
    run.calls = calls
    return run


# ---- discovery -------------------------------------------------------------------------------------------------------
def test_g1_playback_stream_is_never_taken_for_a_capture_source():
    """The real G1 graph: only a playback stream for the phone (linked to the headphones) -> no target, an actionable reason."""
    node, why = find_phone_node(PHONE, dump=dump("g1_playback_role.json"))
    assert node is None
    assert "Stream/Output/Audio" in why and "media-source-role" in why and "bluez_input.C0_FF_EE_00_00_01.2" in why


def test_input_role_source_is_found_with_its_dynamic_suffix_case_insensitively():
    node, why = find_phone_node(PHONE.lower(), dump=dump("g1_input_role.json"))
    assert node == "bluez_input.C0_FF_EE_00_00_01.2", why


def test_other_phones_hands_free_and_internal_nodes_are_never_chosen():
    d = [o for o in dump("g1_input_role.json") if (o.get("info") or {}).get("props", {}).get("object.serial") != 301]
    node, why = find_phone_node(PHONE, dump=d)                      # the A2DP source of PHONE removed: the rest must not match
    assert node is None and "no PipeWire node" in why
    node, _ = find_phone_node(OTHER, dump=dump("g1_input_role.json"))
    assert node == "bluez_input.C0_FF_EE_00_00_02.2"               # isolation by address, not by position


def test_several_matching_sources_pick_the_newest_deterministically():
    d = dump("g1_input_role.json")
    clone = json.loads(json.dumps(next(o for o in d if o.get("id") == 78)))
    clone["id"], clone["info"]["props"]["object.serial"] = 95, 999
    clone["info"]["props"]["node.name"] = "bluez_input.C0_FF_EE_00_00_01.2-1"
    node, _ = find_phone_node(PHONE, dump=d + [clone])
    assert node == "bluez_input.C0_FF_EE_00_00_01.2-1"             # the node of the latest connection (highest object.serial)


def test_unrelated_sources_and_monitors_do_not_count_as_the_phone():
    d = dump("g1_playback_role.json") + [
        {"id": 5, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_input.usb_mic", "media.class": "Audio/Source",
                                                                       "object.serial": 5}}}]
    node, why = find_phone_node(PHONE, dump=d)
    assert node is None and "alsa_input" not in why


def test_pw_dump_is_run_and_its_failures_are_reasons_not_exceptions():
    out = json.dumps(dump("g1_input_role.json"))
    run = runner(out)
    assert find_phone_node(PHONE, run=run)[0] == "bluez_input.C0_FF_EE_00_00_01.2" and run.calls[0][0] == "pw-dump"
    assert find_phone_node(PHONE, run=runner(exc=FileNotFoundError("pw-dump")))[1].startswith("pw-dump unavailable")
    assert "exit status 1" in find_phone_node(PHONE, run=runner("", rc=1))[1]
    assert "not valid JSON" in find_phone_node(PHONE, run=runner("{garbage"))[1]
    assert find_phone_node(PHONE, run=runner(exc=subprocess.TimeoutExpired("pw-dump", 5)))[0] is None


# ---- capture supervisor with discovery ----------------------------------------------------------------------------------
def _streamer(nbytes=CHUNK_BYTES * 3, sleep=30):
    return [sys.executable, "-c", f"import sys,time\nsys.stdout.buffer.write(b'\\x07'*{nbytes})\nsys.stdout.flush()\ntime.sleep({sleep})"]


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_capture_waits_without_any_child_while_only_the_playback_stream_exists_and_says_why():
    logs, launched = [], []
    graph = {"d": dump("g1_playback_role.json")}
    cap = PipeWireCapture(resolve=lambda a: find_phone_node(a, dump=graph["d"]), argv_for_node=lambda n: launched.append(n) or _streamer(),
                          retry_s=0.05, log=logs.append)
    got = []
    cap.start(PHONE, got.append)
    time.sleep(0.5)
    assert launched == [] and cap.starts == 0                                  # never records the stream, the monitor or a default
    st = cap.status()
    assert st["state"] == "waiting" and "media-source-role" in st["reason"]
    assert sum("media-source-role" in m for m in logs) == 1                    # logged once, not every retry
    graph["d"] = dump("g1_input_role.json")                                    # the rule is active / the node appears
    assert wait_for(lambda: got)
    cap.stop()
    assert launched[0] == "bluez_input.C0_FF_EE_00_00_01.2" and got[0] == b"\x07" * CHUNK_BYTES
    assert cap.status()["state"] == "stopped"


def test_capture_target_is_resolved_again_for_every_child():
    """After a reconnect the node gets a new suffix/serial: the restarted child must target the current node, not a cached name."""
    names = iter(["bluez_input.X.2", "bluez_input.X.2-1", "bluez_input.X.2-1"])
    launched = []
    fail = [sys.executable, "-c", "import sys; sys.exit(1)"]
    cap = PipeWireCapture(resolve=lambda a: (next(names), "ok"), argv_for_node=lambda n: launched.append(n) or fail, retry_s=0.02)
    cap.start(PHONE, lambda b: None)
    assert wait_for(lambda: len(launched) >= 2)
    cap.stop()
    assert launched[:2] == ["bluez_input.X.2", "bluez_input.X.2-1"]


def test_child_stderr_is_kept_bounded_and_reported_once_per_distinct_failure():
    logs = []
    noisy = [sys.executable, "-c", "import sys\nsys.stderr.write('x'*200000 + '\\nerror: target not found\\n'); sys.exit(3)"]
    cap = PipeWireCapture(resolve=lambda a: ("bluez_input.X.2", "ok"), argv_for_node=lambda n: noisy, retry_s=0.02, log=logs.append)
    cap.start(PHONE, lambda b: None)
    assert wait_for(lambda: cap.starts >= 4)
    cap.stop()
    exits = [m for m in logs if "exited" in m]
    assert exits and "status 3" in exits[0] and "error: target not found" in exits[0]
    assert len(exits) < cap.starts                                           # repeated identical failures are not logged every time
    assert all(len(m) < 600 for m in logs)                                   # 200 kB of stderr never reaches the RAM log
    assert "target not found" in cap.status()["last_error"]


def test_child_that_writes_to_stderr_while_streaming_does_not_block():
    """A full stderr pipe would block the child (and the audio). The supervisor must drain it while reading PCM."""
    code = ("import sys,time\nfor i in range(400):\n    sys.stderr.write('w'*1000+'\\n'); sys.stderr.flush()\n"
            f"    sys.stdout.buffer.write(b'\\x01'*{CHUNK_BYTES}); sys.stdout.flush()\ntime.sleep(30)")
    got = []
    cap = PipeWireCapture(resolve=lambda a: ("n", "ok"), argv_for_node=lambda n: [sys.executable, "-c", code], retry_s=0.05, stall_s=5)
    cap.start(PHONE, got.append)
    assert wait_for(lambda: len(got) >= 400, timeout=10)                     # 400 kB of stderr: more than a pipe buffer
    cap.stop()
    assert all(c == b"\x01" * CHUNK_BYTES for c in got)


def test_stop_reaps_the_child_and_closes_its_pipes():
    cap = PipeWireCapture(resolve=lambda a: ("n", "ok"), argv_for_node=lambda n: _streamer(), retry_s=0.05)
    cap.start(PHONE, lambda b: None)
    assert wait_for(lambda: cap._proc is not None)
    proc = cap._proc
    cap.stop()
    assert proc.poll() is not None and proc.stdout.closed and proc.stderr.closed
    assert cap._proc is None and cap._thread is None


def test_status_reports_capturing_with_the_target():
    cap = PipeWireCapture(resolve=lambda a: ("bluez_input.X.2", "ok"), argv_for_node=lambda n: _streamer(), retry_s=0.05)
    got = []
    cap.start(PHONE, got.append)
    assert wait_for(lambda: got)
    st = cap.status()
    cap.stop()
    assert st["state"] == "capturing" and st["node"] == "bluez_input.X.2" and st["address"] == PHONE and st["starts"] == 1


def test_default_capture_resolves_the_node_through_pw_dump_and_uses_no_fallback_argv():
    cap = PipeWireCapture()
    assert cap.resolve is not None and cap.argv_for_node is parec_argv
    argv = record_argv("bluez_input.X.2")
    assert argv[argv.index("--target") + 1] == "bluez_input.X.2" and "node.dont-fallback = true" in argv[argv.index("-P") + 1]


# ---- adapter: transport events -> one capture worker ----------------------------------------------------------------------
class CountingPcm:
    def __init__(self):
        self.log = []

    def start(self, address, on_pcm):
        self.log.append(("start", address))

    def stop(self):
        self.log.append(("stop",))


def rig():
    bus, rec, pcm, logs = FakeBus(), Recorder(), CountingPcm(), []
    a = BluezAdapter(bus, pcm, log=logs.append)
    a.attach(rec)
    a.start()
    return a, bus, rec, pcm, logs


def starts(pcm):
    return [e for e in pcm.log if e[0] == "start"]


def test_repeated_and_duplicate_transport_states_start_one_worker():
    a, bus, rec, pcm, logs = rig()
    bus.add_device(1, connected=True); bus.add_transport(1, "pending")
    bus.transport(1, State="active"); bus.transport(1, State="active"); bus.transport(1, State="pending")
    assert starts(pcm) == [("start", addr(1))]
    bus.transport(1, State="idle"); bus.transport(1, State="idle")
    assert pcm.log[-1] == ("stop",) and pcm.log.count(("stop",)) == 1
    bus.transport(1, State="pending")                                        # resume after a pause: exactly one new worker
    assert starts(pcm) == [("start", addr(1))] * 2


def test_transport_that_appears_before_the_device_connects_starts_once_on_connect():
    a, bus, rec, pcm, logs = rig()
    bus.add_device(1); bus.add_transport(1, "active")
    assert starts(pcm) == []
    bus.connect(1)
    assert starts(pcm) == [("start", addr(1))]


def test_transport_removal_disconnect_and_reconnect_never_leave_two_workers():
    a, bus, rec, pcm, logs = rig()
    bus.add_device(1, connected=True); bus.add_transport(1, "active")
    bus.removed(f"{dev_path(1)}/fd0", IF_TRANSPORT)
    assert pcm.log[-1] == ("stop",)
    bus.add_transport(1, "pending", idx=1)
    bus.disconnect(1)
    assert pcm.log[-1] == ("stop",)
    bus.connect(1); bus.transport(1, idx=1, State="active")
    s = starts(pcm)
    assert s == [("start", addr(1))] * 3
    opened = 0
    for e in pcm.log:                                                       # never two workers at once
        opened = opened + 1 if e[0] == "start" else 0
        assert opened <= 1


def test_transport_transitions_are_logged_once_per_change():
    a, bus, rec, pcm, logs = rig()
    bus.add_device(1, connected=True); bus.add_transport(1, "pending"); bus.transport(1, State="active"); bus.transport(1, State="active")
    t = [m for m in logs if m.startswith("transport")]
    assert t == [f"transport pending ({addr(1)})", f"transport active ({addr(1)})"]


# ---- agent capability is passed through (G1 note) ----------------------------------------------------------------------
def test_jeepney_bus_registers_the_capability_it_was_given():
    from btbridge.adapters.bluez.jeepney_bus import JeepneyBus
    calls = []
    jb = JeepneyBus.__new__(JeepneyBus)
    jb.call = lambda path, iface, method, sig="", args=(): calls.append((method, args))
    jb._agent = ("/org/btbridge/agent", lambda *a: None, "DisplayYesNo")
    jb._register_agent_with_bluez()
    assert calls[0] == ("RegisterAgent", ("/org/btbridge/agent", "DisplayYesNo"))
    assert calls[1] == ("RequestDefaultAgent", ("/org/btbridge/agent",))


@pytest.mark.parametrize("state", ["pending", "active"])
def test_no_capture_for_a_transport_without_an_active_phone(state):
    a, bus, rec, pcm, logs = rig()
    bus.add_device(1); bus.add_transport(1, state)
    assert pcm.log == []


# ---- diagnostics bundle -------------------------------------------------------------------------------------------------
def test_the_diagnostics_bundle_carries_the_capture_state_with_pseudonymised_addresses(tmp_path):
    from test_service import Lmses, fake_tree

    from btbridge.service import ApplianceService
    from btkit.sim_a2dp import SimulatedA2dpSource
    sim = SimulatedA2dpSource()
    why = find_phone_node(PHONE, dump=dump("g1_playback_role.json"))[1]
    sim.capture_status = lambda: {"transport": "active", "capturing_for": PHONE, "state": "waiting", "reason": why, "starts": 0}
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge", lms_factory=Lmses(),
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, discover=lambda timeout: [])
    s.start()
    try:
        b = s.diagnostics_bundle()
        text = json.dumps(b)
        sim.capture_status = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        broken = s.diagnostics_bundle()["audio_capture"]
    finally:
        s.stop()
    cap = b["audio_capture"]
    assert cap["state"] == "waiting" and "media-source-role" in cap["reason"] and cap["transport"] == "active"
    assert PHONE not in text and "C0_FF_EE_00_00_01" not in text            # both spellings of the phone's MAC are pseudonymised
    assert broken == {"error": "RuntimeError"}
