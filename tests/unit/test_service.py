"""UNIT (real BridgeRuntime threads, SIMULATED phone, FAKE LMS): the appliance service composition and its safety properties."""
import json
import os
import time

import pytest
from fakes import FakeLms

from btbridge.service import ApplianceService, UnconfiguredLms, lms_url
from btbridge.system import SystemMonitor
from btkit.sim_a2dp import SimulatedA2dpSource

P1, P2 = "02:00:00:00:00:01", "02:00:00:00:00:02"


class Lmses:
    """One FakeLms per server URL (so a server switch is observable)."""
    def __init__(self):
        self.by_url = {}

    def __call__(self, url):
        if url not in self.by_url:
            f = FakeLms(); f.add(P1); f.add(P2)
            f.server_version = lambda: "9.1.2"
            f.targets = lambda: [{"id": P1, "kind": "player", "name": "Kitchen", "connected": True}]
            self.by_url[url] = f
        return self.by_url[url]


def fake_tree(tmp_path, temp_milli="45000"):
    proc = tmp_path / "proc"; (proc / "net").mkdir(parents=True, exist_ok=True); (proc / "self").mkdir(exist_ok=True)
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 1000000 kB\nMemAvailable: 600000 kB\n")
    (proc / "loadavg").write_text("0.10 0.20 0.30 1/100 1\n"); (proc / "uptime").write_text("1234.5 100\n")
    (proc / "self" / "status").write_text("VmRSS:\t 20480 kB\n")
    (proc / "net" / "dev").write_text("h\nh\n  eth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n    lo: 1 0 0 0 0 0 0 0 1 0 0 0 0 0 0 0\n")
    thermal = tmp_path / "temp"; thermal.write_text(temp_milli)
    return SystemMonitor(proc=str(proc), thermal=str(thermal), data_dir=str(tmp_path))


@pytest.fixture
def svc(tmp_path):
    lmses = Lmses(); sim = SimulatedA2dpSource()
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge", lms_factory=lmses,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, discover=lambda timeout: [
                             {"address": "10.0.0.5", "JSON": "9000", "NAME": "Living room LMS", "VERS": "9.1.2"}])
    s.start(); s.sim, s.lmses = sim, lmses
    yield s
    s.stop()


def wait(cond, t=3.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.02)
    return False


def configure(s, host="10.0.0.5", target=P1):
    s.store.update({"lms_host": host, "target_player": target})
    return s.lmses(lms_url(host, 9000))


def test_unconfigured_bridge_never_sends_anything(svc):
    assert isinstance(svc.runtime.lms, UnconfiguredLms) and svc.status()["headline"] == "not_configured"
    svc.sim.connect(); svc.sim.play(); time.sleep(1.0)
    assert svc.runtime.lms.commands_sent == [] and svc.lmses.by_url == {}
    assert svc.diagnostic_test() == "refused:not_configured"


def test_configuring_server_and_target_starts_playback_of_a_playing_phone(svc):
    svc.sim.connect(); svc.sim.play(); time.sleep(0.3)
    lms = configure(svc)
    assert wait(lambda: [c[0] for c in lms.commands] == ["play"])
    assert lms.commands[0][1] == P1 and "http://bridge:" in lms.commands[0][2]
    assert wait(lambda: svc.status()["headline"] == "playing")


def test_target_change_releases_only_our_url_on_the_old_target(svc):
    lms = configure(svc); svc.sim.connect(); svc.sim.play()
    assert wait(lambda: lms.commands and lms.commands[-1][0] == "play")
    url = lms.commands[-1][2]
    svc.store.update({"target_player": P2})
    assert wait(lambda: lms.commands[-1][:2] == ("play", P2))
    assert ("release", P1, url) in lms.commands


def test_server_change_releases_on_old_server_and_continues_on_new(svc):
    old = configure(svc); svc.sim.connect(); svc.sim.play()
    assert wait(lambda: old.commands and old.commands[-1][0] == "play")
    svc.store.update({"lms_host": "10.0.0.9"})
    new = svc.lmses(lms_url("10.0.0.9", 9000))
    assert wait(lambda: new.commands and new.commands[-1][0] == "play")
    assert old.commands[-1][0] == "release" and old.commands[-1][2] == old.commands[0][2]
    assert svc.status()["bridge"]["bluetooth_connection"] == "connected"            # phone stayed connected


def test_format_change_restarts_our_session_with_the_new_format(svc):
    lms = configure(svc); svc.sim.connect(); svc.sim.play()
    assert wait(lambda: lms.commands and lms.commands[-1][2].endswith(".wav"))
    svc.store.update({"stream_format": "flac"})
    assert wait(lambda: lms.commands[-1][0] == "play" and lms.commands[-1][2].endswith(".flac"))
    assert lms.commands[-2][0] == "release"


def test_audio_toggle_and_override_and_pause_strategy_apply_in_place(svc):
    lms = configure(svc); svc.sim.connect(); svc.sim.play()
    assert wait(lambda: lms.commands and lms.commands[-1][0] == "play")
    svc.set_audio_allowed(False)
    assert wait(lambda: lms.commands[-1][0] == "release") and svc.status()["headline"] == "audio_disabled"
    svc.store.update({"override_lms": False, "pause_strategy": "keepalive_silence"})
    assert svc.runtime.controller.cfg.override_lms is False and svc.runtime.controller.cfg.pause_strategy == "keepalive_silence"
    assert {c[0] for c in lms.commands} <= {"play", "release", "power_on", "volume_delta"}


def test_pairing_window_and_bluetooth_name_reach_the_source(svc):
    assert svc.sim.alias == "Hi-Fi Bridge"
    svc.open_pairing()
    assert svc.sim.pairable and svc.status()["bluetooth"]["pairing_open"] and svc.status()["bluetooth"]["pairing_remaining_s"] > 290
    svc.close_pairing(); assert not svc.sim.pairable
    svc.store.update({"device_name": "Wohnzimmer", "pairing_seconds": 120})
    assert svc.sim.alias == "Wohnzimmer" and svc.runtime.pairing.duration == 120


def test_paired_devices_labels_and_removal(svc):
    svc.sim.connect()
    assert [d["address"] for d in svc.paired_devices()] == ["sim-phone-1"]
    assert svc.remove_device("sim-phone-1") and svc.paired_devices() == []
    assert svc.status()["bridge"]["bluetooth_connection"] == "disconnected"


def test_token_rotation_and_no_secret_in_status_logs_or_diagnostics(svc):
    lms = configure(svc)
    code = open(svc.auth.code_file).read().strip()
    svc.auth.complete_setup("c", code, "a good long passphrase")
    old = svc.store.secret("stream_token")
    svc.sim.connect(); svc.sim.play()
    assert wait(lambda: lms.commands and old in lms.commands[-1][2])               # LMS must get the token, nobody else
    svc.rotate_stream_token(); new = svc.store.secret("stream_token")
    svc.store.update({"stream_format": "flac"})                                      # forces a new session URL
    assert wait(lambda: new in lms.commands[-1][2])
    blob = json.dumps(svc.status()) + json.dumps(svc.diagnostics_bundle()) + json.dumps(svc.logs.recent())
    for secret in (old, new, svc.store.secret("admin_password_hash"), svc.store.secret("session_key")):
        assert secret and secret not in blob


def test_thermal_critical_is_a_fault_and_an_immediate_event(svc, tmp_path):
    events = []
    svc.on_event(lambda k, d: events.append((k, d)))
    svc.check_thermal(); assert svc.thermal()["state"] == "ok"
    (tmp_path / "temp").write_text("83500")
    svc.check_thermal()
    assert ("thermal", svc.thermal()) in events and svc.thermal()["state"] == "critical"
    assert svc.logs.faults()[-1]["kind"] == "thermal" and "thermal_critical" in svc.status()["degraded"]
    n = len(events); svc.check_thermal(); assert len(events) == n                     # no repeated events without a change
    (tmp_path / "temp").write_text("50000"); svc.check_thermal()
    assert events[-1][1]["state"] == "ok" and "thermal_critical" not in svc.status()["degraded"]


def test_the_watch_loop_checks_temperature_far_more_often_than_telemetry(svc, tmp_path):
    from btbridge import service as S
    assert S.THERMAL_CHECK_S <= 10 < S.TELEMETRY_S
    events = []
    svc.on_event(lambda k, d: events.append(k) if k == "thermal" else None)
    (tmp_path / "temp").write_text("84000")
    import time as _t
    t0 = _t.time()
    while "thermal" not in events and _t.time() - t0 < S.THERMAL_CHECK_S + 3:
        _t.sleep(0.1)
    assert "thermal" in events, "the running service must notice over-temperature within one check interval"


def test_discovery_check_and_targets_are_read_only(svc):
    assert svc.discover_servers() == [{"host": "10.0.0.5", "port": 9000, "name": "Living room LMS", "version": "9.1.2"}]
    assert svc.check_server("10.0.0.5", 9000) == {"reachable": True, "version": "9.1.2"}
    assert svc.list_targets("10.0.0.5", 9000)[0]["name"] == "Kitchen"
    assert all(f.commands == [] for f in svc.lmses.by_url.values())


def test_settings_survive_a_restart(tmp_path):
    d = str(tmp_path / "data")
    s = ApplianceService(d, stream_port=0, lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.store.update({"device_name": "Küche", "lms_host": "10.0.0.5", "target_player": P1, "pause_strategy": "keepalive_silence"})
    tok = s.store.secret("stream_token")
    s2 = ApplianceService(d, stream_port=0, lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    assert s2.store.get("device_name") == "Küche" and s2.runtime.controller.cfg.pause_strategy == "keepalive_silence"
    assert s2.runtime.token == tok and s2.configured()


def test_system_monitor_reads_the_fake_tree(tmp_path):
    m = fake_tree(tmp_path)
    a = m.sample()
    assert a["memory"]["used_percent"] == 40.0 and a["temperature_c"] == 45.0 and a["thermal_state"] == "ok"
    assert a["uptime_s"] == 1234 and a["load"] == [0.1, 0.2, 0.3] and a["process_rss_mb"] == 20.0 and "eth0" in a["network"]
    assert a["cpu_percent"] is None                                                   # needs two samples
    (tmp_path / "proc" / "stat").write_text("cpu  150 0 150 900 0 0 0 0 0 0\n")
    assert m.sample()["cpu_percent"] == 50.0
    assert SystemMonitor(proc=str(tmp_path / "nope"), thermal=None, data_dir="/nonexistent").sample()["memory"] is None


def test_system_monitor_reports_wifi_signal_and_tolerates_garbage(tmp_path):
    m = fake_tree(tmp_path)
    (tmp_path / "proc" / "net" / "wireless").write_text(
        "Inter-| sta-|   Quality        |   Discarded packets\n face | tus | link level noise |  nwid  crypt\n"
        " wlan0: 0000   54.  -56.  -256        0      0\n broken: x\n")
    net = m.sample()["network"]
    assert net["wlan0"]["wifi_signal_dbm"] == -56.0 and "wifi_signal_dbm" not in net.get("broken", {})
    (tmp_path / "bad-temp").write_text("not a number\n")
    assert SystemMonitor(proc=str(tmp_path / "proc"), thermal=str(tmp_path / "bad-temp"), data_dir=str(tmp_path)).sample()["temperature_c"] is None
