"""UNIT (real BridgeRuntime threads, SIMULATED phone, FAKE LMS): regressions for review findings on the appliance service
(reports/REVIEW_PHASE2B_EXISTING_CODE.md: H3, H4, L3, L6, L8, M7, M10)."""
import time

import pytest
from test_service import P1, Lmses, fake_tree, wait

import btbridge.service as service_mod
from btbridge.service import ApplianceService, lms_url
from btkit.sim_a2dp import SimulatedA2dpSource


@pytest.fixture
def auto_svc(tmp_path, monkeypatch):
    """A service that computes the stream address itself (advertise_host='auto', as on the appliance)."""
    addr = {"ip": None}
    monkeypatch.setattr(service_mod, "local_ip_for", lambda host, port, **k: addr["ip"])
    lmses = Lmses(); sim = SimulatedA2dpSource()
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="auto", lms_factory=lmses,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    s.start(); s.sim, s.lmses, s.addr = sim, lmses, addr
    yield s
    s.stop()


# ---------------------------------------------------------------- H3: stream address per session, never loopback to a remote LMS
def test_no_route_at_start_then_network_up_uses_the_new_address(auto_svc):
    s = auto_svc
    lms = s.lmses(lms_url("10.0.0.5", 9000))
    s.sim.connect(); s.sim.play()
    assert wait(lambda: s.status()["bridge"]["blocked_reason"] == "stream_address_unavailable")
    assert not [c for c in lms.commands if c[0] == "play"]                 # never tells LMS to fetch from 127.0.0.1
    assert "stream_address_unavailable" in s.status()["degraded"]
    s.addr["ip"] = "192.168.1.20"                                           # Wi-Fi/DHCP comes up
    assert wait(lambda: [c for c in lms.commands if c[0] == "play"], t=6)
    assert "http://192.168.1.20:" in [c for c in lms.commands if c[0] == "play"][0][2]
    assert "stream_address_unavailable" not in s.status()["degraded"]


def test_dhcp_address_change_applies_to_the_next_session(auto_svc):
    s = auto_svc
    lms = s.lmses(lms_url("10.0.0.5", 9000))
    s.addr["ip"] = "192.168.1.20"
    s.sim.connect(); s.sim.play()
    assert wait(lambda: [c for c in lms.commands if c[0] == "play"])
    s.addr["ip"] = "192.168.1.21"
    s.sim.pause(); time.sleep(0.3); s.sim.play()
    assert wait(lambda: len([c for c in lms.commands if c[0] == "play"]) == 2, t=6)
    assert "http://192.168.1.21:" in [c for c in lms.commands if c[0] == "play"][1][2]


def test_local_ip_for_has_no_loopback_fallback():
    assert service_mod.local_ip_for("192.0.2.1", 9000, connect=lambda *a: (_ for _ in ()).throw(OSError("no route"))) is None


def test_loopback_only_for_a_local_lms_and_nothing_without_a_server(tmp_path):
    lmses = Lmses()
    s = ApplianceService(str(tmp_path / "data"), source=None, stream_port=0, advertise_host="auto", lms_factory=lmses,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    assert s.stream_host() is None                                          # no server configured: no address invented
    s.store.update({"lms_host": "127.0.0.1", "target_player": P1})
    assert s.stream_host() == "127.0.0.1"                                   # LMS on the same host: the route says loopback


# ---------------------------------------------------------------- M10: MAC addresses pseudonymised in logs and diagnostics
PHONE = "11:22:33:44:55:66"


def test_log_lines_carry_a_stable_salted_pseudonym_instead_of_the_mac(tmp_path):
    from btbridge.service import LogBuffer
    from btbridge import privacy
    forwarded = []
    lb = LogBuffer(str(tmp_path), echo=None)
    lb.forward = forwarded.append
    privacy.configure(b"salt-one")
    lb(f"refusing {PHONE}: second source"); lb("path /org/bluez/hci0/dev_11_22_33_44_55_66 gone")
    lb.fault("bluetooth", f"lost {PHONE.lower()}")
    text = " ".join(m for _, m in lb.recent(10)) + " ".join(forwarded) + open(tmp_path / "faults.jsonl").read()
    assert PHONE not in text.upper() and "11_22_33_44_55_66" not in text
    pseudo = privacy.mac(PHONE)
    assert pseudo.startswith("dev-") and text.count(pseudo) >= 4               # same device -> same pseudonym everywhere
    privacy.configure(b"salt-two")
    assert privacy.mac(PHONE) != pseudo                                        # per-device salt: not linkable across devices


def test_adapter_console_log_is_pseudonymised(capsys):
    from btbridge import privacy
    privacy.configure(b"s")
    privacy.scrubbed_print(f"ignoring non-audio-source device {PHONE}")
    out = capsys.readouterr().out
    assert PHONE not in out and privacy.mac(PHONE) in out


def test_diagnostics_bundle_has_no_mac_addresses_device_labels_or_phone_names(tmp_path):
    import json
    import re
    lmses = Lmses(); sim = SimulatedA2dpSource(device=PHONE)
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge", lms_factory=lmses,
                         monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    s.store.update({"lms_host": "10.0.0.5", "target_player": P1})
    s.start()
    try:
        sim.connect(); sim.play()
        assert wait(lambda: s.runtime.controller.device)
        dev = s.runtime.controller.device
        assert wait(lambda: s.status()["bluetooth"]["connected_device"])
        s.set_device_label(dev, "Annas Handy")
        s.logs(f"test line for {dev}")
        blob = json.dumps(s.diagnostics_bundle())
        assert not re.search(r"(?i)\b([0-9a-f]{2}[:_-]){5}[0-9a-f]{2}\b", blob), "a MAC address leaked into the bundle"
        assert "Annas Handy" not in blob
        assert s.status()["bluetooth"]["connected_device"]["name"] == "Annas Handy"   # the owner's own UI is unchanged
    finally:
        s.stop()


# ---------------------------------------------------------------- H4: a bad optional integration never aborts start or settings
BOGUS_PEM = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"


def test_schema_rejects_a_ca_field_with_markers_but_no_certificate():
    from btbridge.config.schema import validate
    for key in ("mqtt_ca_pem", "syslog_ca_pem"):
        with pytest.raises(ValueError, match="invalid_pem"):
            validate({key: BOGUS_PEM})


def test_stored_bad_ca_from_an_older_version_does_not_stop_the_appliance(tmp_path, monkeypatch):
    import json
    import os
    import threading
    from btbridge import appliance as AP
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    d = tmp_path / "data"; os.makedirs(d, mode=0o700)
    (d / "settings.json").write_text(json.dumps({"schema_version": 1, "mqtt_enabled": True, "mqtt_host": "broker.invalid",
                                                 "mqtt_tls": True, "mqtt_ca_pem": BOGUS_PEM + "\n"}))
    a = AP.parser().parse_args(["--data", str(d), "--http-port", "0", "--https-port", "0", "--stream-port", "0", "--no-mdns",
                                "--source", "none", "--thermal-path", "", "--advertise-host", "127.0.0.1"])
    done, info = threading.Event(), {}
    t = threading.Thread(target=AP.run, args=(a, None), kwargs={"ready": lambda s, u: info.update(svc=s), "done": done}, daemon=True)
    t.start()
    assert wait(lambda: "svc" in info, t=15), "appliance.run did not come up"
    assert info["svc"].store.get("mqtt_ca_pem") == ""                      # the unusable stored value fell back to the default
    done.set(); t.join(10)


def test_an_mqtt_integration_that_fails_to_start_degrades_and_the_rest_of_the_settings_apply(tmp_path):
    import ssl

    def broken(svc):
        raise ssl.SSLError("bad CA")
    lmses = Lmses(); sim = SimulatedA2dpSource()
    s = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=0, advertise_host="bridge",
                         lms_factory=lmses, monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, mqtt_factory=broken)
    s.store.update({"mqtt_enabled": True, "mqtt_host": "broker.lan"})
    s.start()                                                               # was: ssl.SSLError out of start() -> crash loop
    try:
        assert "mqtt_disconnected" in s.status()["degraded"]
        events = []
        s.on_event(lambda kind, data: events.append(kind))
        s.store.update({"mqtt_port": 8884, "device_name": "Küche"})         # MQTT restart fails again, the rest must still apply
        assert sim.alias == "Küche" and "settings_changed" in events
        assert any("mqtt" in m.lower() and "could not start" in m for _, m in s.logs.recent(50))
    finally:
        s.stop()
