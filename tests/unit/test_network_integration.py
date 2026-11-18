"""UNIT: network management tied into the existing parts - SD-card network reset (exactly once, power-cut safe), boot status hints without
passwords, factory reset of the pending file, the certificate rule for a wrong clock, LocalAccess hooks (rename, address change),
redacted diagnostics of the service."""
import datetime as dt
import json
import os

import pytest

from btbridge import bootfiles, recovery as R
from btbridge.net import netconfig as N
from btbridge.net.access import LocalAccess
from btbridge.net.certs import CertManager

WIFI = {"ssid": "Home", "psk": "wifi-secret-123"}
HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}


def broken_network(d):
    cfg = N.validate({"country": "DE", "wifi": WIFI, "hotspot": HOTSPOT,
                      "ethernet": {"ipv4": {"method": "static", "address": "192.168.77.5/24", "gateway": "192.168.77.1"}}})
    N.write_private(str(d / N.FILE_NAME), cfg)
    N.write_private(str(d / N.PENDING_NAME), cfg)
    return cfg


def test_network_reset_keeps_country_and_hotspot_and_never_prints_a_password(tmp_path):
    broken_network(tmp_path)
    text = R.network_reset(str(tmp_path))
    cfg = N.load(str(tmp_path / N.FILE_NAME))
    assert cfg["wifi"] is None and cfg["ethernet"] == N.default_ip() and cfg["hotspot"] == HOTSPOT and cfg["country"] == "DE"
    assert not (tmp_path / N.PENDING_NAME).exists() and os.stat(tmp_path / N.FILE_NAME).st_mode & 0o777 == 0o600
    assert "Hi-Fi Bridge 4F2A" in text and "10.42.0.1" in text and HOTSPOT["psk"] not in text and WIFI["psk"] not in text
    assert R.network_reset(str(tmp_path)) == text                                     # idempotent


def test_network_reset_without_hotspot_or_with_a_broken_file(tmp_path):
    assert "connect the device by Ethernet" in R.network_reset(str(tmp_path))
    (tmp_path / N.FILE_NAME).write_text("{broken")
    assert "unreadable" in R.network_reset(str(tmp_path)) and N.load(str(tmp_path / N.FILE_NAME)) == N.defaults()


def test_network_reset_boot_request_runs_once_and_survives_power_cuts(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    broken_network(data)
    (boot / R.REQUEST_FILE).write_text("network-reset\n")

    class Cut(Exception):
        pass

    def cut():
        raise Cut()
    with pytest.raises(Cut):
        R.boot_request(str(data), str(boot), hooks={"after_running": cut})            # power cut before the action
    assert N.load(str(data / N.FILE_NAME))["wifi"] is not None
    with pytest.raises(Cut):
        R.boot_request(str(data), str(boot), hooks={"after_done": cut})               # power cut after "done"
    assert N.load(str(data / N.FILE_NAME))["wifi"] is None
    assert R.boot_request(str(data), str(boot)) == "already-done" and not (boot / R.REQUEST_FILE).exists()
    assert R.boot_request(str(data), str(boot)) == "none"


def test_boot_status_names_the_hotspot_but_never_its_password(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    broken_network(data)
    (boot / R.REQUEST_FILE).write_text("network-reset")
    r = bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)
    status = (boot / R.STATUS_FILE).read_text()
    assert r["recovery"] == "network-reset" and "Recovery request processed: network-reset" in status
    assert "Setup hotspot (opens when no network works): 'Hi-Fi Bridge 4F2A' -> http://10.42.0.1/" in status
    assert HOTSPOT["psk"] not in status and WIFI["psk"] not in status
    N.write_private(str(data / N.FILE_NAME), N.validate({"country": "DE", "wifi": WIFI}))
    assert bootfiles.network_lines(str(data)) == ["Wi-Fi network: 'Home'"]
    (data / N.FILE_NAME).write_text("{")
    assert "network-reset" in bootfiles.network_lines(str(data))[0]


def test_factory_reset_also_removes_a_pending_network_change(tmp_path):
    broken_network(tmp_path)
    removed = R.factory_reset(str(tmp_path))
    assert {N.FILE_NAME, N.PENDING_NAME} <= set(removed)


def test_console_network_reset(tmp_path, capsys, monkeypatch):
    broken_network(tmp_path)
    monkeypatch.setattr(R, "run_as_data_owner", lambda d: None)
    assert R.main(["--data", str(tmp_path), "network-reset"]) == 0
    assert "network reset" in capsys.readouterr().out and N.load(str(tmp_path / N.FILE_NAME))["wifi"] is None


def test_certificate_issued_with_a_clock_ahead_is_renewed_once_the_clock_is_right(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    cm = CertManager(str(tmp_path), "ab12cd34")
    cm.issue_server(["hifi-bridge.local"])
    assert cm.needs_renewal(["hifi-bridge.local"]) is None
    behind = CertManager(str(tmp_path), "ab12cd34", clock=lambda: now - dt.timedelta(days=2))     # NTP corrected a clock that was ahead
    assert behind.needs_renewal(["hifi-bridge.local"]) == "not_yet_valid"
    slightly = CertManager(str(tmp_path), "ab12cd34", clock=lambda: now - dt.timedelta(minutes=2))
    assert slightly.needs_renewal(["hifi-bridge.local"]) is None                        # small skew tolerated


class Store:
    def __init__(self, **kw):
        self.v = {"local_hostname": "hifi-bridge", "extra_hostnames": "", "tls_mode": "proxy", "mdns_enabled": True,
                  "mdns_backend": "avahi", "device_name": "Lab", "trusted_proxies": "", "https_only": False, **kw}

    def get(self, k):
        return self.v[k]


class Pub:
    instances = []

    def __init__(self, host, inst, services, on_name, log):
        self.host, self.started, self.stopped = host, False, False
        Pub.instances.append(self)

    def connect(self):
        return "0.8"

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def test_access_rename_hook_runs_before_mdns_restarts_and_failures_do_not_block(tmp_path):
    Pub.instances = []
    order = []
    acc = LocalAccess(Store(), str(tmp_path), "ab12cd34", 80, 443, log=order.append, addresses=lambda: ["192.168.1.20"],
                      avahi_factory=lambda *a, **k: (order.append("publish"), Pub(*a, **k))[1])
    acc._start_mdns()
    acc.before_rename = lambda: order.append("hostname")
    acc.reconfigure({"local_hostname"})
    assert order[-2:] == ["hostname", "publish"]

    def boom():
        raise RuntimeError("agent gone")
    acc.before_rename = boom
    acc.reconfigure({"local_hostname"})
    assert any("host name hook failed (RuntimeError" in m for m in order) and order[-1] == "publish"


def test_access_network_changed_republishes_avahi_records_and_checks_certificates(tmp_path):
    Pub.instances = []
    addrs = ["192.168.1.20"]
    acc = LocalAccess(Store(), str(tmp_path), "ab12cd34", 80, 443, log=lambda m: None, addresses=lambda: list(addrs), avahi_factory=Pub)
    acc._start_mdns()
    acc._last_addrs = set(addrs)
    calls = []
    acc.ensure_certificates = lambda: calls.append("certs") or "names_changed"
    acc.network_changed()
    assert len(Pub.instances) == 1 and calls == ["certs"]                                # same addresses: no re-publication
    addrs[:] = ["10.42.0.1"]
    acc.network_changed()
    assert len(Pub.instances) == 2 and Pub.instances[0].stopped and Pub.instances[1].started
    acc.mdns_backend = "builtin"
    addrs[:] = ["192.168.1.21"]
    acc.network_changed()
    assert len(Pub.instances) == 2                                                     # the built-in responder is always current


def test_service_diagnostics_include_the_redacted_network(tmp_path):
    from test_service import fake_tree
    from btbridge.net.netclient import NetworkService
    from btbridge.service import ApplianceService
    svc = ApplianceService(str(tmp_path / "data"), source=None, stream_port=0, monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000)
    assert svc.diagnostics_bundle()["network"] is None
    N.write_private(str(tmp_path / "data" / N.FILE_NAME), N.validate({"country": "DE", "wifi": WIFI, "hotspot": HOTSPOT}))
    svc.network = NetworkService(str(tmp_path / "data"), None, secrets=svc.logs.secrets, interfaces=lambda: {})
    bundle = json.dumps(svc.diagnostics_bundle())
    assert WIFI["psk"] not in bundle and HOTSPOT["psk"] not in bundle and '"password_set": true' in bundle
    svc.logs(f"oops {WIFI['psk']} leaked")                                             # defence in depth: log lines are redacted
    assert WIFI["psk"] not in json.dumps(svc.logs.recent(5))
    svc.stop()
