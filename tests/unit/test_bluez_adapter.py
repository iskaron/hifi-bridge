"""UNIT tests for the hardware-free prepared BlueZ/PipeWire adapter layers.
Label: UNIT/SIMULATED - scripted events written from BlueZ documentation; this does NOT show that a real bluetoothd, phone
or Pi behaves this way (docs/HARDWARE_VALIDATION_PLAN.md)."""
import sys
import threading
import time

import pytest
from fakes import PLAYER, Rig
from fakes_bluez import ADAPTER, FakeBus, Recorder, addr, dev_path

from btbridge.adapters.bluez import BluezAdapter, BluezModel, PipeWireCapture
from btbridge.adapters.bluez.bus import A2DP_SOURCE_UUID, IF_DEVICE, Rejected
from btbridge.adapters.bluez.pcm import CHUNK_BYTES, node_name_for, record_argv


def model(bus=None, accept=True):
    bus = bus or FakeBus()
    rec = Recorder(accept)
    return BluezModel(rec, bus), bus, rec


# ---- connection policy ----------------------------------------------------------------------------------
def test_phone_connect_and_disconnect_map_to_callbacks():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1); assert rec.events == []                    # known but not connected: nothing
    bus.connect(1); bus.disconnect(1)
    assert rec.events == [("connect", addr(1)), ("disconnect", addr(1))]


def test_second_source_is_refused_and_first_is_never_evicted():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_device(2); bus.connect(2)
    assert bus.calls == [(dev_path(2), IF_DEVICE, "Disconnect")]
    assert rec.events == [("connect", addr(1))] and m.active == dev_path(1)
    bus.add_transport(2, "active"); bus.add_player(2, "playing")   # refused phone's events are not forwarded
    assert rec.kinds() == ["connect"]
    bus.disconnect(2); assert rec.kinds() == ["connect"]              # its disconnect is not ours either
    bus.disconnect(1); assert rec.events[-1] == ("disconnect", addr(1))


def test_connection_refused_by_bridge_triggers_disconnect_call():
    m, bus, rec = model(accept=False); bus.handler = m.handle
    bus.add_device(1); bus.connect(1)
    assert bus.calls == [(dev_path(1), IF_DEVICE, "Disconnect")] and m.active is None


def test_non_audio_source_devices_are_ignored_without_disconnect():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, uuids=["00001124-0000-1000-8000-00805f9b34fb"])   # HID only
    bus.connect(1)
    assert rec.events == [] and bus.calls == []
    bus.add_device(2, uuids=None); bus.connect(2)                       # UUIDs unknown yet -> treated as phone (hardware item)
    assert rec.events == [("connect", addr(2))]


def test_phone_already_connected_at_startup_is_picked_up_from_snapshot():
    bus = FakeBus({dev_path(1): {IF_DEVICE: {"Address": addr(1), "Connected": True, "UUIDs": [A2DP_SOURCE_UUID]}},
                   f"{dev_path(1)}/fd0": {"org.bluez.MediaTransport1": {"Device": dev_path(1), "State": "active", "Volume": 64}},
                   f"{dev_path(1)}/player0": {"org.bluez.MediaPlayer1": {"Device": dev_path(1), "Status": "playing"}}})
    m, _, rec = model(bus)
    m.load(bus.managed_objects())
    assert rec.events == [("connect", addr(1)), ("transport", "active"), ("volume", 64), ("avrcp", "playing")]


def test_objects_that_appear_before_the_connected_event_are_replayed():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1); bus.add_transport(1, "pending", volume=40); bus.add_player(1, "paused"); assert rec.events == []
    bus.connect(1)
    assert rec.events == [("connect", addr(1)), ("transport", "pending"), ("volume", 40), ("avrcp", "paused")]


# ---- transport / player / volume / metadata -------------------------------------------------------------------
def test_transport_states_status_mapping_and_volume_clamp():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1); bus.add_player(1, "stopped"); rec.events.clear()
    bus.transport(1, State="pending"); bus.transport(1, State="active"); bus.transport(1, State="bogus")
    bus.player(1, Status="playing"); bus.player(1, Status="forward-seek"); bus.player(1, Status="error"); bus.player(1, Status="paused")
    bus.transport(1, Volume=200); bus.transport(1, Volume=-5); bus.transport(1, Volume=77)
    assert rec.events == [("transport", "pending"), ("transport", "active"), ("avrcp", "playing"), ("avrcp", "playing"),
                          ("avrcp", None), ("avrcp", "paused"), ("volume", 127), ("volume", 0), ("volume", 77)]


def test_track_metadata_forwarded_once_per_change():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_player(1, "playing"); rec.events.clear()
    t = {"Title": "Song", "Artist": "Band", "Album": "LP"}
    bus.player(1, Track=t); bus.player(1, Track=dict(t)); bus.player(1, Track={})
    bus.player(1, Track={"Title": "Next", "Artist": "Band"})
    assert rec.events == [("meta", "Song", "Band", "LP"), ("meta", "Next", "Band", "")]


def test_removed_transport_means_idle_and_removed_player_means_avrcp_unknown():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "active"); bus.add_player(1, "playing"); rec.events.clear()
    bus.removed(f"{dev_path(1)}/fd0", "org.bluez.MediaTransport1"); bus.removed(f"{dev_path(1)}/player0", "org.bluez.MediaPlayer1")
    assert rec.events == [("transport", "idle"), ("avrcp", None)]


def test_device_removal_while_connected_releases_like_a_disconnect():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); rec.events.clear()
    bus.removed(dev_path(1), IF_DEVICE)
    assert rec.events == [("disconnect", addr(1))] and m.active is None


def test_events_after_disconnect_are_not_forwarded_and_a_new_phone_can_connect():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "active"); bus.disconnect(1); rec.events.clear()
    bus.transport(1, State="idle")                                    # late event of the gone phone: ignored
    bus.add_device(2); bus.connect(2)
    assert rec.events == [("connect", addr(2))] and not bus.calls


# ---- the translator feeding the REAL core logic (scripted phone behaviours) -----------------------------------
def _wire(rig):
    from btbridge.core.fusion import MediaFusion
    fusion = MediaFusion(rig.clock)

    class CB(Recorder):
        pass
    cb = CB()
    cb.on_connect = lambda d: rig.ctl.on_connected(d)
    cb.on_disconnect = lambda d: (rig.ctl.on_disconnected(d), fusion.reset())
    cb.on_transport = lambda s: (fusion.on_transport(s), rig.ctl.on_media(fusion.playing()))
    cb.on_avrcp = lambda s: (fusion.on_avrcp(s), rig.ctl.on_media(fusion.playing()))
    cb.on_volume = rig.ctl.on_volume
    bus = FakeBus()
    m = BluezModel(cb, bus); bus.handler = m.handle
    return m, bus, fusion


def test_trace_notification_sound_on_avrcp_phone_never_takes_radio():
    """Phone with an AVRCP player whose Status stays 'paused' while the transport briefly becomes active (typical beep)."""
    rig = Rig(); rig.lms.user_plays(PLAYER, "http://radio/x")
    m, bus, fusion = _wire(rig)
    bus.add_device(1, connected=True); bus.add_player(1, "paused"); bus.add_transport(1, "idle")
    bus.transport(1, State="active"); rig.run(1.0); bus.transport(1, State="idle"); rig.run(6)
    assert rig.lms.commands == [] and rig.lms.players[PLAYER]["url"] == "http://radio/x"


def test_trace_real_play_takes_over_after_filter_and_pause_releases_fast():
    rig = Rig(); rig.lms.user_plays(PLAYER, "http://radio/x")
    m, bus, fusion = _wire(rig)
    bus.add_device(1, connected=True); bus.add_player(1, "paused"); bus.add_transport(1, "idle")
    bus.transport(1, State="active"); bus.player(1, Status="playing"); rig.run(2.5)
    assert len(rig.cmds("play")) == 1
    bus.player(1, Status="paused"); rig.run(0.5)
    assert len(rig.cmds("release")) == 1                      # drop_restart default: our item removed at once
    bus.disconnect(1); rig.run(1)
    assert {c[0] for c in rig.lms.commands} <= {"play", "release", "power_on", "volume_delta"}


# ---- adapter composition -------------------------------------------------------------------------------------
class FakePcm:
    def __init__(self): self.log = []
    def start(self, address, on_pcm): self.log.append(("start", address)); self.on_pcm = on_pcm
    def stop(self): self.log.append(("stop",))


def adapter(bus=None, rec=None):
    bus, rec, pcm = bus or FakeBus(), rec or Recorder(), FakePcm()
    a = BluezAdapter(bus, pcm); a.attach(rec); a.start()
    return a, bus, rec, pcm


def test_start_subscribes_registers_agent_and_closes_pairing():
    a, bus, rec, pcm = adapter()
    assert bus.handler and bus.agent[:2] == ("/org/btbridge/agent", "NoInputNoOutput")
    assert (ADAPTER, "org.bluez.Adapter1", "Pairable", False) in bus.props and (ADAPTER, "org.bluez.Adapter1", "Discoverable", False) in bus.props


def test_pairing_window_mirrors_to_adapter_properties_and_agent_policy():
    a, bus, rec, pcm = adapter(); handler = bus.agent[2]
    with pytest.raises(Rejected):
        handler("AuthorizeService", dev_path(9), ())            # unknown device outside the window
    a.set_pairable(True)
    assert (ADAPTER, "org.bluez.Adapter1", "Pairable", True) in bus.props and (ADAPTER, "org.bluez.Adapter1", "PairableTimeout", 0) in bus.props
    handler("RequestAuthorization", dev_path(9), ())            # accepted while open
    a.set_pairable(False)
    with pytest.raises(Rejected):
        handler("RequestAuthorization", dev_path(9), ())
    with pytest.raises(Rejected):
        handler("RequestPinCode", dev_path(9), ())               # never any PIN/passkey flow


def test_agent_accepts_service_authorization_for_paired_connected_device_even_when_closed():
    a, bus, rec, pcm = adapter(); bus.add_device(1, connected=True, paired=True)
    bus.agent[2]("AuthorizeService", dev_path(1), ())


def test_pcm_capture_follows_transport_and_disconnect():
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); bus.add_transport(1, "idle"); bus.transport(1, State="active")
    assert ("start", addr(1)) in pcm.log
    bus.transport(1, State="idle"); assert pcm.log[-1] == ("stop",)
    bus.transport(1, State="pending"); bus.disconnect(1)
    assert pcm.log[-1] == ("stop",) and rec.events[-1] == ("disconnect", addr(1))


def test_pcm_frames_reach_the_consumer_callback():
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); bus.add_transport(1, "active")
    pcm.on_pcm(b"\0" * 8); assert ("pcm", 8) in rec.events


def test_second_phone_does_not_start_pcm_capture():
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); bus.add_device(2); bus.connect(2); bus.add_transport(2, "active")
    assert pcm.log == []


def test_disconnect_device_calls_device1_disconnect_for_connected_address_only():
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); a.disconnect_device(addr(2)); assert bus.calls == []
    a.disconnect_device(addr(1)); assert bus.calls == [(dev_path(1), IF_DEVICE, "Disconnect")]


def test_stop_closes_pairing_and_bus():
    a, bus, rec, pcm = adapter(); a.set_pairable(True); a.stop()
    assert bus.closed and pcm.log[-1] == ("stop",)
    last = {(p[2]): p[3] for p in bus.props}                        # final value per property
    assert last["Pairable"] is False and last["Discoverable"] is False


def test_event_handler_errors_do_not_escape_the_bus_thread():
    a, bus, rec, pcm = adapter()
    rec.on_connect = lambda d: (_ for _ in ()).throw(RuntimeError("boom"))
    a.attach(rec); a.start()
    bus.add_device(1, connected=True)                          # must not raise
    assert a.errors and "boom" in a.errors[0]


# ---- PCM capture process supervision (real child processes, fake audio) ------------------------------------------
def _child(code):
    return lambda addr: [sys.executable, "-c", code]


def test_node_name_and_argv_shape():
    assert node_name_for("aa:bb:cc:dd:ee:01") == "bluez_input.AA_BB_CC_DD_EE_01"
    argv = record_argv("node1")
    assert argv[:4] == ["pw-cat", "--record", "--target", "node1"] and argv[-1] == "-" and "44100" in argv and "s16" in argv
    assert "--raw" in argv and "--raw" not in record_argv("node1", raw=False)      # --raw exists only in PipeWire >= 1.x
    assert "node.dont-fallback = true" in argv[argv.index("-P") + 1]
    from btbridge.adapters.bluez.pcm import parec_argv
    p = parec_argv("n")
    assert p[0] == "parec" and "--raw" in p and "--format=s16le" in p and "--device=n" in p
    assert "--property=node.dont-fallback=true" in p and "--property=node.dont-reconnect=true" in p


def test_default_capture_command_is_parec_with_no_fallback():
    cap = PipeWireCapture()
    argv = cap.argv_for("AA:BB:CC:DD:EE:01")
    assert argv[0] == "parec" and "--device=bluez_input.AA_BB_CC_DD_EE_01" in argv


def test_source_present_parses_pactl_output():
    from btbridge.adapters.bluez.pcm import source_present
    class R:
        stdout = "32\tauto_null.monitor\tPipeWire\tfloat32le 2ch 48000Hz\tSUSPENDED\n40\tbluez_input.AA_BB\tPipeWire\ts16le 2ch 44100Hz\tIDLE\n"
    assert source_present("bluez_input.AA_BB", run=lambda *a, **k: R()) is True
    assert source_present("bluez_input.AA", run=lambda *a, **k: R()) is False
    def boom(*a, **k):
        raise OSError("no pactl")
    assert source_present("x", run=boom) is False


def test_capture_watchdog_restarts_a_silent_child():
    cap = PipeWireCapture(_child("import time; time.sleep(60)"), retry_s=0.05, stall_s=0.3)
    cap.start("AA:BB:CC:DD:EE:01", lambda b: None); time.sleep(1.5); cap.stop()
    assert cap.starts >= 2


def test_capture_presence_check_gates_starts():
    present = {"v": False}
    cap = PipeWireCapture(_child("import time; time.sleep(60)"), retry_s=0.05, present=lambda a: present["v"])
    cap.start("AA:BB:CC:DD:EE:01", lambda b: None); time.sleep(0.4)
    assert cap.starts == 0
    present["v"] = True; time.sleep(0.4); cap.stop()
    assert cap.starts == 1


def test_capture_delivers_chunk_aligned_pcm_from_child():
    got = []
    code = "import sys,time\nsys.stdout.buffer.write(bytes(range(256))*80)\nsys.stdout.flush()\ntime.sleep(30)"
    cap = PipeWireCapture(_child(code), retry_s=0.1); cap.start("AA:BB:CC:DD:EE:01", got.append)
    t0 = time.time()
    while len(got) < 4 and time.time() - t0 < 5:
        time.sleep(0.02)
    cap.stop()
    assert len(got) >= 4 and all(len(c) == CHUNK_BYTES for c in got)
    assert b"".join(got)[:512] == bytes(range(256)) * 2                  # byte-exact
    assert cap._proc is None


def test_capture_retries_while_node_is_missing_then_streams():
    """The first two child processes exit at once (PipeWire node not there yet), the third one streams."""
    import subprocess
    got, launched = [], []
    fail = [sys.executable, "-c", "import sys; sys.exit(1)"]
    ok = [sys.executable, "-c", "import sys,time\nsys.stdout.buffer.write(b'\\1'*%d)\nsys.stdout.flush()\ntime.sleep(30)" % CHUNK_BYTES]

    def popen(argv, **kw):
        launched.append(argv)
        return subprocess.Popen(fail if len(launched) <= 2 else ok, **kw)
    cap = PipeWireCapture(lambda a: ["pw-cat"], popen=popen, retry_s=0.05)
    cap.start("AA:BB:CC:DD:EE:01", got.append)
    t0 = time.time()
    while not got and time.time() - t0 < 5:
        time.sleep(0.02)
    cap.stop()
    assert got and got[0] == b"\1" * CHUNK_BYTES and len(launched) >= 3 and cap.starts >= 3


def test_capture_missing_tool_does_not_crash_and_stop_is_prompt():
    cap = PipeWireCapture(lambda a: ["/nonexistent/pw-cat"], retry_s=0.05)
    cap.start("AA:BB:CC:DD:EE:01", lambda b: None); time.sleep(0.2)
    t0 = time.time(); cap.stop()
    assert time.time() - t0 < 3 and cap.starts == 0


def test_capture_stop_terminates_child():
    cap = PipeWireCapture(_child("import time; time.sleep(60)"), retry_s=0.05)
    cap.start("AA:BB:CC:DD:EE:01", lambda b: None); time.sleep(0.3)
    proc = cap._proc; assert proc is not None and proc.poll() is None
    cap.stop(); time.sleep(0.2)
    assert proc.poll() is not None


# ---- bluetoothd restart ------------------------------------------------------------------------------------------
def test_service_vanished_releases_active_phone_and_appeared_resyncs():
    from btbridge.adapters.bluez.bus import ServiceAppeared, ServiceVanished
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); a.set_pairable(True); rec.events.clear()
    bus.emit(ServiceVanished()); assert rec.events == [("disconnect", addr(1))] and a.model.active is None
    bus.objects = {dev_path(1): {IF_DEVICE: {"Address": addr(1), "Connected": True, "UUIDs": [A2DP_SOURCE_UUID]}}}
    bus.props.clear(); bus.emit(ServiceAppeared())
    assert bus.agent_reregistered == 1
    assert (ADAPTER, "org.bluez.Adapter1", "Pairable", True) in bus.props          # pairing state re-applied
    assert rec.events[-1] == ("connect", addr(1))


# ---- operator entry point never pretends --------------------------------------------------------------------------
def test_source_bluez_without_a_reachable_bus_fails_cleanly(capsys):
    from btbridge.__main__ import main
    rc = main(["--player", "02:00:00:00:00:01", "--source", "bluez", "--dbus", "unix:path=/nonexistent/bus.sock"])
    assert rc == 2 and "cannot start the BlueZ adapter" in capsys.readouterr().err


# ---- device management (WebUI pairing page) ---------------------------------------------------------------------------
def test_paired_devices_listing_remove_and_alias():
    a, bus, rec, pcm = adapter()
    bus.add_device(1, connected=True); bus.add_device(2, paired=True); bus.add_device(3, paired=False)
    bus.changed(dev_path(2), IF_DEVICE, Alias="Annas iPhone")
    devs = a.paired_devices()
    assert [d["address"] for d in devs] == [addr(1), addr(2)] or [d["address"] for d in devs] == [addr(2), addr(1)]
    assert {d["address"]: d["connected"] for d in devs} == {addr(1): True, addr(2): False}
    assert any(d["name"] == "Annas iPhone" for d in devs)
    assert a.remove_device(addr(2)) is True and bus.calls[-1] == (ADAPTER, "org.bluez.Adapter1", "RemoveDevice")
    assert a.remove_device("00:00:00:00:00:00") is False
    a.set_alias("Wohnzimmer")
    assert bus.props[-1] == (ADAPTER, "org.bluez.Adapter1", "Alias", "Wohnzimmer")


def test_pairing_inside_the_window_marks_the_phone_trusted():
    a, bus, rec, pcm = adapter(); a.set_pairable(True)
    bus.agent[2]("RequestAuthorization", dev_path(5), ())
    assert (dev_path(5), "org.bluez.Device1", "Trusted", True) in bus.props


def test_simulated_source_offers_the_same_device_management():
    from btkit.sim_a2dp import SimulatedA2dpSource
    sim = SimulatedA2dpSource(); rec = Recorder(); sim.attach(rec); sim.connect()
    assert sim.paired_devices() == [{"address": "sim-phone-1", "name": "Simulated phone", "trusted": True, "connected": True}]
    assert sim.remove_device("sim-phone-1") and ("disconnect", "sim-phone-1") in rec.events and sim.paired_devices() == []
    sim.set_alias("x"); assert sim.alias == "x"


def test_capture_tool_choice_prefers_pw_cat_raw_then_parec_then_old_pw_cat():
    from btbridge.adapters.bluez.pcm import choose_capture

    class R:
        def __init__(self, out): self.stdout = out
    def which_all(tools):
        return lambda t: f"/usr/bin/{t}" if t in tools else None
    name, f = choose_capture(which_all({"pw-cat", "parec"}), run=lambda *a, **k: R("  --raw   RAW mode"))
    argv = f("bluez_input.X")
    assert name == "pw-cat --raw" and argv[0] == "/usr/bin/pw-cat" and "--raw" in argv and "node.dont-fallback = true" in argv[argv.index("-P") + 1]
    name, f = choose_capture(which_all({"pw-cat", "parec"}), run=lambda *a, **k: R("no raw option"))
    assert name == "parec" and "--property=node.dont-fallback=true" in f("n")
    name, f = choose_capture(which_all({"pw-cat"}), run=lambda *a, **k: R("no raw option"))
    assert name == "pw-cat (0.3 raw)" and "--raw" not in f("n") and "node.dont-fallback = true" in f("n")[f("n").index("-P") + 1]
    assert choose_capture(which_all(set()))[0] == "none"


def test_simulated_unpaired_phone_needs_the_pairing_window():
    from btkit.sim_a2dp import SimulatedA2dpSource
    sim = SimulatedA2dpSource(paired=False); rec = Recorder(); sim.attach(rec)
    assert sim.connect() is False and sim.refused == 1 and rec.events == []
    sim.set_pairable(True); assert sim.connect() is True and sim.paired
    sim.disconnect(); sim.set_pairable(False); assert sim.connect() is True             # once paired, it reconnects without the window
