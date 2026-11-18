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
