"""UNIT (real service threads, SIMULATED phone, FAKE LMS): failure paths of the appliance service that an owner can run into - an optional
integration that cannot start, a Bluetooth stack that refuses a name, a crash leftover at Lyrion, a LAN that answers badly. The rule they
prove (review H4): one failing part never stops the others and never takes the service down. Coverage package 7b-3."""
import pytest
from test_service import P1, svc, wait  # noqa: F401  (svc is a fixture)

from btbridge.lms.client import LmsUnreachable
from btbridge.service import ApplianceService, UnconfiguredLms


def logged(s, text: str) -> bool:
    return any(text in m for _, m in s.logs.recent(300))


# ---------------------------------------------------------------- remote syslog: optional, never fatal
def test_a_syslog_ca_that_tls_no_longer_accepts_is_reported_and_leaves_logging_local(svc, monkeypatch):
    """The settings page only stores a CA that TLS accepts (config/schema.py). A value stored earlier (restored backup, another OpenSSL)
    can still be refused at start: that must not stop the service or the owner's other settings."""
    import ssl

    import btbridge.system.syslog as syslog_mod

    def refuse(self, *a, **k):
        raise ssl.SSLError("PEM lib")
    monkeypatch.setattr(syslog_mod.RemoteSyslog, "__init__", refuse)
    svc.store.update({"syslog_enabled": True, "syslog_host": "logs.example"})
    assert svc.syslog is None and svc.logs.forward is None
    assert logged(svc, "remote syslog: invalid CA certificate")
    svc.logs("still logging locally")
    assert logged(svc, "still logging locally")


def test_a_syslog_forwarder_that_cannot_start_is_reported_and_dropped(svc, monkeypatch):
    import btbridge.system.syslog as syslog_mod

    def boom(self):
        raise OSError("no thread for you")
    monkeypatch.setattr(syslog_mod.RemoteSyslog, "start", boom)
    svc.store.update({"syslog_enabled": True, "syslog_host": "logs.example"})
    assert svc.syslog is None and svc.logs.forward is None
    assert logged(svc, "remote syslog could not start: OSError: no thread for you")


def test_changing_syslog_settings_stops_the_running_forwarder_first_and_disabling_ends_forwarding(svc, monkeypatch):
    import btbridge.system.syslog as syslog_mod
    started, stopped = [], []

    class Forwarder:
        def __init__(self, host, port, ca, hostname=""):
            self.host, self.sent = host, []
            started.append(host)

        def start(self):
            pass

        def stop(self, flush_s=2.0):
            stopped.append(self.host)

        def emit(self, line):
            self.sent.append(line)
    monkeypatch.setattr(syslog_mod, "RemoteSyslog", Forwarder)
    svc.store.update({"syslog_enabled": True, "syslog_host": "first.example"})
    first = svc.syslog
    svc.logs("goes to the first collector")
    svc.store.update({"syslog_host": "second.example"})
    assert started == ["first.example", "second.example"] and stopped == ["first.example"]
    assert svc.syslog is not first and svc.logs.forward == svc.syslog.emit
    svc.store.update({"syslog_enabled": False})
    assert svc.syslog is None and svc.logs.forward is None and stopped == ["first.example", "second.example"]
    assert any("goes to the first collector" in line for line in first.sent)


def test_stopping_the_service_flushes_and_stops_the_forwarder(tmp_path, monkeypatch):
    import btbridge.system.syslog as syslog_mod
    from test_service import Lmses, fake_tree
    from btkit.sim_a2dp import SimulatedA2dpSource
    stopped = []

    class Forwarder:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self, flush_s=2.0):
            stopped.append(flush_s)

        def emit(self, line):
            pass
    monkeypatch.setattr(syslog_mod, "RemoteSyslog", Forwarder)
    s = ApplianceService(str(tmp_path / "data"), source=SimulatedA2dpSource(), stream_port=0, advertise_host="bridge", lms_factory=Lmses(),
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.store.update({"syslog_enabled": True, "syslog_host": "logs.example"})
    s.start()
    s.stop()
    assert stopped and stopped[-1] == 2.0                      # the default flush window at shutdown, 0.5 s only when re-configuring


# ---------------------------------------------------------------- Bluetooth name and paired-device list
class NamelessSource:
    """A Bluetooth stack that refuses to set its name (e.g. bluetoothd restarting) and cannot list devices."""
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def set_alias(self, name):
        raise RuntimeError("org.bluez.Error.Failed")

    def paired_devices(self):
        raise RuntimeError("bluetoothd is restarting")


def test_a_refused_bluetooth_name_is_logged_and_does_not_block_start_or_the_settings_change(tmp_path):
    from test_service import Lmses, fake_tree
    from btkit.sim_a2dp import SimulatedA2dpSource
    s = ApplianceService(str(tmp_path / "data"), source=NamelessSource(SimulatedA2dpSource()), stream_port=0, advertise_host="bridge",
                         lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.start()
    try:
        assert logged(s, "could not set Bluetooth name: RuntimeError: org.bluez.Error.Failed")      # at start
        s.logs.lines.clear()
        s.store.update({"device_name": "Living room", "pairing_seconds": 77})
        assert logged(s, "could not set Bluetooth name")                                          # when the owner renames it
        assert s.runtime.pairing.duration == 77.0                                                  # the rest of the change still applied
        assert s.store.get("device_name") == "Living room"
    finally:
        s.stop()


def test_paired_devices_degrade_to_an_empty_list_when_the_stack_fails_or_cannot_list(tmp_path):
    from test_service import Lmses, fake_tree
    from btkit.sim_a2dp import SimulatedA2dpSource
    mk = lambda src: ApplianceService(str(tmp_path / "d"), source=src, stream_port=0, advertise_host="bridge", lms_factory=Lmses(),  # noqa: E731
                                      monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    failing = mk(NamelessSource(SimulatedA2dpSource()))
    assert failing.paired_devices() == [] and logged(failing, "paired_devices failed: RuntimeError: bluetoothd is restarting")
    assert mk(None).paired_devices() == []                                                       # no Bluetooth source at all


def test_removing_a_phone_also_forgets_its_label_and_only_its_label(svc):
    svc.set_device_label("aa:bb:cc:dd:ee:01", "Anna")
    svc.set_device_label("AA:BB:CC:DD:EE:02", "Ben")
    assert svc.remove_device("AA:BB:CC:DD:EE:01") is False                                      # unknown to the simulator: not found ...
    assert svc.store.get("device_labels") == {"AA:BB:CC:DD:EE:02": "Ben"}                      # ... but the stale label is gone anyway
    assert svc.remove_device("AA:BB:CC:DD:EE:99") is False and svc.store.get("device_labels") == {"AA:BB:CC:DD:EE:02": "Ben"}


# ---------------------------------------------------------------- Lyrion: discovery, reachability, crash leftovers
def test_a_failing_discovery_broadcast_is_an_empty_list_not_an_error_page(tmp_path):
    from test_service import Lmses, fake_tree

    def no_network(timeout):
        raise OSError("Network is unreachable")
    s = ApplianceService(str(tmp_path / "data"), source=None, stream_port=0, advertise_host="bridge", lms_factory=Lmses(),
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, discover=no_network)
    assert s.discover_servers() == [] and logged(s, "discovery failed: Network is unreachable")


def test_check_server_reports_an_unreachable_server_with_a_short_reason(tmp_path):
    from test_service import fake_tree

    def factory(url):
        raise LmsUnreachable("connection refused " + "x" * 400)
    s = ApplianceService(str(tmp_path / "data"), source=None, stream_port=0, advertise_host="bridge", lms_factory=factory,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    r = s.check_server("192.0.2.7", 9000)
    assert r["reachable"] is False and r["error"].startswith("connection refused") and len(r["error"]) == 200
    assert s.list_targets() == []                                                                # no server configured: nothing to ask


def test_a_crash_leftover_is_dropped_when_the_server_setting_was_removed_meanwhile(svc):
    svc.journal.record(P1, "http://old.example:8090/stream")
    assert isinstance(svc.runtime.lms, UnconfiguredLms)
    assert svc.recover_stale_session() == "dropped_unconfigured"                                 # nothing to release it with
    assert svc.journal.pending() is None


def test_the_watch_loop_releases_a_crash_leftover_as_soon_as_lyrion_is_reachable(svc):
    lms = svc.lmses("http://10.0.0.5:9000")
    svc.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    svc.journal.record(P1, "http://bridge:8090/stale.flac")
    assert wait(lambda: ("release", P1, "http://bridge:8090/stale.flac") in lms.commands, t=8)
    assert wait(lambda: svc.journal.pending() is None)
    assert logged(svc, "recovered: released the stream item a previous (crashed) process left at Lyrion")


def test_an_unreachable_server_keeps_the_leftover_for_the_next_attempt(svc):
    lms = svc.lmses("http://10.0.0.5:9000")
    svc.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    svc.journal.record(P1, "http://bridge:8090/stale.flac")
    lms.release = lambda *a: (_ for _ in ()).throw(LmsUnreachable("down"))
    assert svc.recover_stale_session() == "pending" and svc.journal.pending()["url"] == "http://bridge:8090/stale.flac"


# ---------------------------------------------------------------- headline texts and addresses
@pytest.mark.parametrize("bridge,expect", [
    ({"bluetooth_connection": "connected", "target_ownership": "released", "bt_media_activity": "playing",
      "blocked_reason": "override_disabled", "audio_permission": "allowed"}, "waiting_for_target"),
    ({"bluetooth_connection": "connected", "target_ownership": "released", "bt_media_activity": "playing",
      "blocked_reason": "notification_filter", "audio_permission": "allowed"}, "waiting_for_target"),
    ({"bluetooth_connection": "connected", "target_ownership": "released", "bt_media_activity": "paused",
      "blocked_reason": None, "audio_permission": "allowed"}, "paused"),
    ({"bluetooth_connection": "connected", "target_ownership": "released", "bt_media_activity": "stopped",
      "blocked_reason": None, "audio_permission": "allowed"}, "connected"),
    ({"bluetooth_connection": "disconnected", "target_ownership": "released", "bt_media_activity": "stopped",
      "blocked_reason": None, "audio_permission": "allowed"}, "ready"),
])
def test_the_headline_an_owner_reads_for_each_situation(bridge, expect):
    assert ApplianceService._headline(bridge, []) == expect


def test_a_fixed_advertise_host_is_used_as_configured_and_a_named_lms_is_resolved_in_the_background(tmp_path):
    from test_service import Lmses, fake_tree
    s = ApplianceService(str(tmp_path / "data"), source=None, stream_port=0, advertise_host="auto", lms_factory=Lmses(),
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.store.update({"lms_host": "lyrion.invalid"})
    assert s.stream_host() is None                                                               # not resolvable (yet): no invented address
    assert s._resolvers[("lyrion.invalid", 9000)] is not None                                  # a name gets a resolver, an IP literal none
    s.advertise_cfg = "bridge.lan"
    assert s.stream_host() == "bridge.lan"


# ---------------------------------------------------------------- listeners and the watch loop never kill each other
def test_a_failing_event_listener_is_logged_and_does_not_stop_the_others(svc):
    seen = []

    def bad(kind, data):
        raise ValueError("listener bug")
    svc.on_event(bad); svc.on_event(lambda kind, data: seen.append(kind))
    svc._emit("settings_changed", {})
    assert seen == ["settings_changed"] and logged(svc, "event listener error: ValueError: listener bug")
    svc.off_event(bad); svc.off_event(bad)                                                        # removing twice is harmless
    svc._emit("settings_changed", {})
    assert seen == ["settings_changed"] * 2


def test_a_failing_system_sample_is_logged_by_the_watch_loop_which_keeps_running(svc):
    def broken():
        raise OSError("/proc went away")
    svc.monitor.sample = broken
    assert wait(lambda: logged(svc, "watch error: OSError: /proc went away"), t=6)
    beat = svc.watch_heartbeat
    assert wait(lambda: svc.watch_heartbeat > beat)                                              # still alive, still beating


def test_a_failing_settings_step_does_not_stop_the_other_steps(svc):
    class BrokenAccess:
        def reconfigure(self, keys):
            raise RuntimeError("avahi-daemon is not running")

        def stop(self):
            pass
    svc.access = BrokenAccess()
    svc.store.update({"device_name": "Kitchen", "pairing_seconds": 61})
    assert logged(svc, "local access reconfiguration failed: RuntimeError: avahi-daemon is not running")
    assert svc.runtime.pairing.duration == 61.0


def test_a_restored_mqtt_password_restarts_the_mqtt_connection(svc):
    restarted = []
    svc._restart_mqtt = lambda: restarted.append(1)
    svc.apply_restored_secrets({"mqtt_password"})
    assert restarted == [1]


def test_switching_track_titles_in_the_stream_on_and_off_restarts_a_running_session_only_when_it_changes_something(svc):
    from test_service import configure
    lms = configure(svc)
    svc.sim.connect(); svc.sim.play()
    assert wait(lambda: [c for c in lms.commands if c[0] == "play"])
    plays = lambda: len([c for c in lms.commands if c[0] == "play"])  # noqa: E731
    svc.store.update({"icy_metadata": False})                          # already off: nothing to do, the phone keeps playing undisturbed
    assert plays() == 1
    svc.store.update({"icy_metadata": True})                           # a change replaces the session so that it applies now
    assert wait(lambda: plays() == 2, t=6) and svc.runtime.stream.icy is True
    svc.store.update({"icy_metadata": False})
    assert wait(lambda: plays() == 3, t=6) and svc.runtime.stream.icy is False


def test_a_mqtt_connection_that_cannot_be_stopped_is_logged_and_replaced(svc):
    class Stuck:
        def stop(self):
            raise OSError("socket already closed")
    svc.mqtt = Stuck()
    svc._restart_mqtt()
    assert svc.mqtt is None and logged(svc, "MQTT stop failed: OSError: socket already closed")


def test_the_log_survives_an_unwritable_fault_file_and_an_empty_secret(tmp_path):
    from btbridge.service import LogBuffer
    lb = LogBuffer(str(tmp_path / "missing-directory"), echo=None)         # e.g. the runtime directory is gone
    lb.secrets.extend(["", "s3cret-token"])                                  # an unset secret must not blank out every line
    lb.fault("storage", "write failed with s3cret-token")
    text = " ".join(m for _, m in lb.recent(5))
    assert "write failed with" in text and "s3cret-token" not in text
    assert [(f["kind"], f["detail"]) for f in lb.faults()] == [("storage", "write failed with ***")]   # kept in RAM anyway
