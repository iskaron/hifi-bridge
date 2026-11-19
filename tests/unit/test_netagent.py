"""UNIT: the root network agent and the bridge-side NetworkService over a REAL UNIX socket, with the SIMULATED radio (btkit.sim_net)
and a fake clock: request validation, peer-uid check, strict reading of the bridge's files, the pending-file protocol (confirm, automatic
rollback, power cut), redaction, unmanaged mode, and the certificate/mDNS hooks."""
import json
import os
import shutil
import tempfile
import threading

import pytest

from btbridge.net import netagent as A
from btbridge.net import netclient as C
from btbridge.net import netconfig as N
from btbridge.net import netmanager as M
from btkit.sim_net import SimulatedNetworkSystem

WIFI = {"ssid": "Home", "psk": "wifi-secret-123"}
HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, root, cfg=None, ethernet=False, settings=None, synced=True):
        self.root, self.clock, self.logs = root, Clock(), []
        self.data, self.run = os.path.join(root, "d"), os.path.join(root, "r")
        os.makedirs(self.data, mode=0o700); os.makedirs(self.run, mode=0o700)
        if cfg is not None:
            N.write_private(os.path.join(self.data, N.FILE_NAME), cfg)
        if settings is not None:
            fd = os.open(os.path.join(self.data, "settings.json"), os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, json.dumps(settings).encode()); os.close(fd)
        self.sys = SimulatedNetworkSystem(ethernet=ethernet, clock=self.clock, connect_s=3.0, synced=synced)
        self.agent = A.Agent(self.sys, self.data, os.getuid(), self.run, clock=self.clock, log=self.logs.append)
        self.agent.start()
        self.path = self.agent.bind(None)
        threading.Thread(target=self.agent.serve, daemon=True).start()
        self.secrets = []
        self.svc = C.NetworkService(self.data, C.AgentClient(self.path, timeout=5), log=self.logs.append, secrets=self.secrets)

    def advance(self, seconds, step=1.0):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += step
            self.agent.tick()

    def close(self):
        self.agent.stop()


@pytest.fixture
def root():
    d = tempfile.mkdtemp(prefix="ha", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def call(path, raw: bytes):
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5); s.connect(path); s.sendall(raw)
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def test_status_over_the_socket_has_no_passwords_and_lists_interfaces(root):
    r = Rig(root, N.validate({"country": "DE", "wifi": WIFI, "hotspot": HOTSPOT}), settings={"local_hostname": "wohnzimmer"})
    try:
        r.advance(4)
        st = r.svc.status()
        assert st["managed"] is True and st["state"] == "connected" and st["hostname"] == "wohnzimmer"
        assert st["wifi"]["signal_dbm"] == -48 and st["firewall"] == "applied" and st["time_synchronized"] is True
        names = {i["name"]: i for i in st["interfaces"]}
        assert names["wlan0"]["role"] == "wifi" and names["wlan0"]["addresses"] == ["192.168.1.57/24"] and names["eth0"]["role"] == "ethernet"
        text = json.dumps(st)
        assert WIFI["psk"] not in text and HOTSPOT["psk"] not in text and r.sys.hostnames == ["wohnzimmer"]
        assert sorted(r.secrets) == sorted([WIFI["psk"], HOTSPOT["psk"]])                 # registered for log redaction
    finally:
        r.close()


def test_bad_requests_are_refused(root):
    r = Rig(root)
    try:
        for raw in (b"not json\n", b"[]\n", b'{"op": "rm -rf"}\n', b'{"op": "trial"}\n', b'{"op": "confirm", "id": "../x"}\n',
                    b'{"op": "trial", "id": "' + b"A" * 64 + b'"}\n'):
            assert call(r.path, raw) == {"error": "bad_request"}, raw
        assert call(r.path, b"{" + b" " * 5000 + b"}\n") == {"error": "too_large"}
        assert call(r.path, b'{"op": "trial", "id": "' + b"a" * 64 + b'"}\n') == {"error": "mismatch"}      # no pending file
        assert call(r.path, b'{"op": "hostname"}\n') == {"hostname": "hifi-bridge"}                       # no settings: default name
    finally:
        r.close()


def test_other_users_are_refused(root, monkeypatch):
    r = Rig(root)
    try:
        monkeypatch.setattr(A, "peer_uid", lambda conn: 4242)
        assert call(r.path, b'{"op": "status"}\n') == {"error": "forbidden"}
        assert any("uid 4242 refused" in m for m in r.logs)
    finally:
        r.close()


def test_peer_uid_of_a_real_connection(root):
    import socket
    a, b = socket.socketpair()
    try:
        assert A.peer_uid(a) == os.getuid()
    finally:
        a.close(); b.close()

    class Bad:
        def getsockopt(self, *x): raise OSError()
    assert A.peer_uid(Bad()) is None


def test_join_from_the_hotspot_confirmed(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}))
    try:
        r.advance(16)
        assert r.svc.status()["state"] == "hotspot"
        res = r.svc.save(N.validate({"country": "DE", "hotspot": HOTSPOT, "wifi": WIFI}))
        assert res["result"] == "pending" and os.path.exists(r.svc.pending_path)
        assert N.load(r.svc.cfg_path)["wifi"] is None                                      # network.json unchanged until confirmed
        assert r.svc.config()["wifi"]["ssid"] == "Home"                                    # the pending one is in effect
        r.advance(4)
        st = r.svc.status()
        assert st["state"] == "connected" and st["trial"]["state"] == "pending" and st["pending_change"] is True
        assert r.svc.confirm() is True and not os.path.exists(r.svc.pending_path)
        assert N.load(r.svc.cfg_path)["wifi"]["ssid"] == "Home" and r.svc.confirm() is False
    finally:
        r.close()


def test_wrong_password_rolls_back_and_the_pending_file_disappears(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}))
    try:
        r.advance(16)
        r.svc.save(N.validate({"country": "DE", "hotspot": HOTSPOT, "wifi": {"ssid": "Home", "psk": "wrong-password"}}))
        r.advance(61)
        st = r.svc.status()
        assert st["trial"]["state"] == "rolled_back" and st["trial"]["reason"] == "wrong_password" and st["state"] == "hotspot"
        assert not os.path.exists(r.svc.pending_path) and N.load(r.svc.cfg_path)["wifi"] is None
    finally:
        r.close()


def test_change_without_effect_is_promoted_at_once_and_revert_works(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}), ethernet=True)
    try:
        r.advance(2)
        assert r.svc.save(N.validate({"country": "DE", "hotspot": HOTSPOT, "wifi": WIFI}))["result"] == "confirmed"
        assert not os.path.exists(r.svc.pending_path) and N.load(r.svc.cfg_path)["wifi"]["ssid"] == "Home"
        assert r.svc.save(N.load(r.svc.cfg_path))["result"] == "unchanged"
        static = N.load(r.svc.cfg_path); static["ethernet"]["ipv4"] = {"method": "static", "address": "192.168.1.30/24"}
        assert r.svc.save(static)["result"] == "pending"
        assert r.svc.revert() is True and not os.path.exists(r.svc.pending_path)
        assert N.load(r.svc.cfg_path)["ethernet"]["ipv4"] == {"method": "dhcp"} and r.svc.revert() is False
    finally:
        r.close()


def test_unconfirmed_change_rolls_back_after_three_minutes(root):
    r = Rig(root, N.defaults(), ethernet=True)
    try:
        r.advance(1)
        static = N.defaults(); static["ethernet"]["ipv4"] = {"method": "static", "address": "192.168.1.30/24"}
        r.svc.save(static)
        r.advance(181)
        assert r.svc.status()["trial"]["reason"] == "not_confirmed" and not os.path.exists(r.svc.pending_path)
        assert r.sys.calls[-1] == ("ethernet", "dhcp")
    finally:
        r.close()


def test_agent_validates_the_pending_file_itself(root):
    r = Rig(root)
    try:
        p = os.path.join(r.data, N.PENDING_NAME)
        raw = json.dumps({"wifi": WIFI}).encode()                                          # invalid: no country
        fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600); os.write(fd, raw); os.close(fd)
        rep = call(r.path, json.dumps({"op": "trial", "id": N.digest(raw)}).encode() + b"\n")
        assert rep == {"error": "invalid", "problems": ["country:required_iso_3166_alpha2"]}
        os.chmod(p, 0o644)
        rep = call(r.path, json.dumps({"op": "trial", "id": N.digest(raw)}).encode() + b"\n")
        assert rep["problems"] == ["file:permissions_too_open"]
    finally:
        r.close()


def test_agent_error_drops_the_pending_file(root, monkeypatch):
    r = Rig(root)
    try:
        monkeypatch.setattr(r.agent, "handle", lambda req: {"error": "invalid", "problems": ["x:y"]})
        assert r.svc.save(N.validate({"country": "DE", "wifi": WIFI})) == {"result": "error", "error": "invalid", "problems": ["x:y"]}
        assert not os.path.exists(r.svc.pending_path)
        with pytest.raises(N.NetConfigError):
            r.svc.save({"wifi": WIFI})
        assert not os.path.exists(r.svc.pending_path)
    finally:
        r.close()


def test_power_cut_during_a_trial_comes_back_with_the_old_configuration(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}))
    r.advance(16)
    r.svc.save(N.validate({"country": "DE", "hotspot": HOTSPOT, "wifi": WIFI}))
    r.close()                                                                               # power cut: agent and bridge gone
    r2 = A.Agent(SimulatedNetworkSystem(), r.data, os.getuid(), r.run, log=lambda m: None)
    r2.start()
    assert r2.manager.cfg["wifi"] is None                                                   # network.json is the old one
    path = r2.bind(None)
    threading.Thread(target=r2.serve, daemon=True).start()
    try:
        svc = C.NetworkService(r.data, C.AgentClient(path))
        assert os.path.exists(svc.pending_path)
        svc.status()                                                                        # the new agent has no such trial
        assert not os.path.exists(svc.pending_path)
    finally:
        r2.stop()


def test_confirmed_but_not_yet_promoted_is_promoted_by_the_next_status(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}))
    try:
        r.advance(16)
        r.svc.save(N.validate({"country": "DE", "hotspot": HOTSPOT, "wifi": WIFI}))
        pid = r.svc._pending_id()
        assert call(r.path, json.dumps({"op": "confirm", "id": pid}).encode() + b"\n") == {"ok": True}  # bridge crashed before promoting
        r.svc.status()
        assert not os.path.exists(r.svc.pending_path) and N.load(r.svc.cfg_path)["wifi"]["ssid"] == "Home"
    finally:
        r.close()


def test_invalid_network_json_means_ethernet_only_and_is_reported(root):
    d = os.path.join(root, "d"); os.makedirs(d, mode=0o700)
    fd = os.open(os.path.join(d, N.FILE_NAME), os.O_WRONLY | os.O_CREAT, 0o600); os.write(fd, b'{"wifi": 1}'); os.close(fd)
    logs = []
    agent = A.Agent(SimulatedNetworkSystem(), d, os.getuid(), root, log=logs.append)
    agent.start()
    assert agent.config_error and agent.manager.cfg == N.defaults() and any("rejected" in m for m in logs)
    assert agent.status()["config_error"] == agent.config_error


def test_hostname_from_settings_is_validated(root):
    r = Rig(root, settings={"local_hostname": "Bad Name!"})
    try:
        assert r.agent.hostname == "hifi-bridge"
        fd = os.open(os.path.join(r.data, "settings.json"), os.O_WRONLY | os.O_TRUNC); os.write(fd, b"{broken"); os.close(fd)
        assert r.agent.read_hostname() is None
        fd = os.open(os.path.join(r.data, "settings.json"), os.O_WRONLY | os.O_TRUNC); os.write(fd, b'{"local_hostname": "kueche"}'); os.close(fd)
        r.svc.apply_hostname()
        assert r.sys.hostnames[-1] == "kueche"
    finally:
        r.close()


def test_scan_request_runs_in_the_background(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}), ethernet=True)
    try:
        assert r.svc.scan() is True
        r.agent.tick()
        for _ in range(50):
            if r.svc.status()["scan"]["results"]:
                break
            import time; time.sleep(0.02)
        res = r.svc.status()["scan"]["results"]
        assert [n["ssid"] for n in res][:2] == ["Home", "Gartenhaus"] and "Versteckt" not in [n["ssid"] for n in res]
        assert all("psk" not in n for n in res)
    finally:
        r.close()


def test_scan_errors_never_block_further_scans(root):
    r = Rig(root)
    try:
        r.sys.scan = lambda mode: (_ for _ in ()).throw(RuntimeError("driver"))
        r.agent._scan("station")
        assert r.agent._scanning is False and r.agent.manager.scan[1] is None and any("scan error" in m for m in r.logs)
    finally:
        r.close()


def test_tick_errors_are_logged_by_the_loop(root):
    r = Rig(root)
    r.sys.observe = lambda: (_ for _ in ()).throw(ValueError("bad"))
    t = threading.Thread(target=r.agent.loop, kwargs={"interval": 0.01}, daemon=True); t.start()
    import time; time.sleep(0.1)
    r.close(); t.join(1)
    assert any("tick error ValueError" in m for m in r.logs)


# ---------------------------------------------------------------- bridge side without / with a broken agent
def test_unmanaged_service_reports_the_kernel_view_and_changes_nothing(root):
    data = os.path.join(root, "d"); os.makedirs(data)
    open(os.path.join(data, N.PENDING_NAME), "w").write("{}")
    svc = C.NetworkService(data, None, interfaces=lambda: {"eth0": {"carrier": True, "mac": "m", "default_route": True,
                                                                     "addresses": [{"address": "172.18.0.5", "prefix": 16}]}},
                           timesync_flag=os.path.join(root, "nope", "synchronized"))
    st = svc.status()
    assert st["managed"] is False and st["state"] == "unmanaged" and st["interfaces"][0]["addresses"] == ["172.18.0.5/16"]
    assert st["time_synchronized"] is None
    with pytest.raises(C.NotManaged):
        svc.save(N.defaults())
    assert svc.confirm() is False and svc.revert() is False and svc.scan() is False
    svc.apply_hostname()
    svc.start(); svc.stop()
    assert not os.path.exists(svc.pending_path)                                           # no agent -> no pending change can exist
    assert svc.diagnostics()["config"] == N.defaults()


def test_agent_unreachable(root):
    data = os.path.join(root, "d"); os.makedirs(data)
    sock = os.path.join(root, "agent.sock"); open(sock, "w").close()                     # exists, but nobody listens
    logs = []
    svc = C.NetworkService(data, C.AgentClient(sock, timeout=1), log=logs.append, interfaces=lambda: {})
    assert svc.status()["state"] == "agent_unreachable"
    with pytest.raises(C.AgentUnavailable):
        svc.save(N.validate({"country": "DE", "wifi": WIFI}))
    assert not os.path.exists(svc.pending_path)
    N.write_private(svc.pending_path, N.defaults())
    assert svc.confirm() is False and svc.revert() is False and svc.scan() is False
    svc.apply_hostname()
    assert any("host name not applied" in m for m in logs)


def test_agent_client_rejects_non_object_replies(root):
    import socket
    path = os.path.join(root, "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(path); srv.listen(1)

    def serve():
        c, _ = srv.accept(); c.recv(100); c.sendall(b"[1]\n"); c.close()
    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(C.AgentUnavailable):
        C.AgentClient(path).call("status")
    srv.close()


def test_invalid_pending_file_falls_back_to_network_json(root):
    data = os.path.join(root, "d"); os.makedirs(data)
    N.write_private(os.path.join(data, N.FILE_NAME), N.validate({"country": "DE", "wifi": WIFI}))
    open(os.path.join(data, N.PENDING_NAME), "w").write("{broken")
    svc = C.NetworkService(data, None)
    assert svc.config()["wifi"]["ssid"] == "Home" and svc.secrets == [WIFI["psk"]]
    open(os.path.join(data, N.FILE_NAME), "w").write("{broken")
    assert C.NetworkService(data, None).secrets == []
    d = svc.diagnostics()
    assert d["config"] is None and d["config_error"] == ["document:not_json"]


def test_diagnostics_are_redacted(root):
    r = Rig(root, N.validate({"country": "DE", "wifi": WIFI, "hotspot": HOTSPOT}))
    try:
        d = json.dumps(r.svc.diagnostics())
        assert WIFI["psk"] not in d and HOTSPOT["psk"] not in d and '"password_set": true' in d
    finally:
        r.close()


class FakeAccess:
    def __init__(self):
        self.calls = []

    def ensure_certificates(self):
        self.calls.append("certs")

    def network_changed(self):
        self.calls.append("network")


def test_poll_rechecks_certificates_after_the_first_time_sync_and_follows_address_changes(root):
    r = Rig(root, N.validate({"country": "DE", "hotspot": HOTSPOT}), synced=False)
    try:
        acc = FakeAccess(); r.svc.access = acc
        seen = []
        r.svc.on_change.append(seen.append)
        r.svc.on_change.append(lambda st: 1 / 0)
        r.svc.poll()
        assert acc.calls == [] and len(seen) == 1
        r.svc.poll()
        assert len(seen) == 1                                                             # unchanged status: no event
        r.advance(16)                                                                     # hotspot: 10.42.0.1 appears
        r.svc.poll()
        assert acc.calls == ["network"]
        r.sys.synced = True
        r.svc.poll()
        assert acc.calls == ["network", "certs"] and any("listener error" in m for m in r.logs)
    finally:
        r.close()


def test_watch_thread_polls(root, monkeypatch):
    monkeypatch.setattr(C, "WATCH_S", 0.01)
    data = os.path.join(root, "d"); os.makedirs(data)
    logs = []
    svc = C.NetworkService(data, None, log=logs.append, interfaces=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    svc.start()
    import time; time.sleep(0.1)
    svc.stop()
    assert any("network watch error" in m for m in logs)


def test_manager_constants_match_the_documentation():
    assert (M.FALLBACK_S, M.TRIAL_S, M.PROBE_DELAYS_S) == (60.0, 180.0, (120.0, 300.0, 600.0))


def test_main_entry_point(root, monkeypatch):
    import pwd
    from btbridge import supervision
    from btbridge.net import netsys
    assert A.main(["--bridge-user", "no-such-user-xyz", "--run-dir", os.path.join(root, "r0")]) == 2
    monkeypatch.setattr(netsys, "LinuxSystem", lambda *a, **k: SimulatedNetworkSystem())
    monkeypatch.setattr(supervision.StopSignal, "wait", lambda self, *a, **k: None)
    data = os.path.join(root, "d"); os.makedirs(data, mode=0o700)
    run = os.path.join(root, "r")
    import grp
    from types import SimpleNamespace as NS
    monkeypatch.setattr(pwd, "getpwnam", lambda n: NS(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(grp, "getgrgid", lambda g: NS(gr_gid=g))
    assert A.main(["--bridge-user", "hifi-bridge", "--bridge-data", data, "--run-dir", run]) == 0
    assert os.path.exists(os.path.join(run, A.SOCKET_NAME))
