"""UNIT: the appliance entry point end to end in-process (service + LocalAccess + WebUI + watchdog) with the simulated phone, and the
adapter-interface conformance of the BlueZ adapter and the simulator."""
import http.client
import json
import os
import ssl
import threading
import time

import pytest

from btbridge import appliance as AP
from btbridge.adapters import base


def test_simulator_and_bluez_adapter_implement_the_adapter_interfaces():
    from btbridge.adapters.bluez import BluezAdapter
    from btkit.sim_a2dp import SimulatedA2dpSource
    needed = [n for n in base.AudioSourceAdapter.__dict__ if not n.startswith("_")]
    devmgmt = [n for n in base.DeviceManagement.__dict__ if not n.startswith("_")]
    assert {"attach", "start", "stop", "set_pairable", "disconnect_device"} <= set(needed)
    for cls in (BluezAdapter, SimulatedA2dpSource):
        for n in needed + devmgmt:
            assert callable(getattr(cls, n, None)), (cls.__name__, n)
    assert {"on_connect", "on_disconnect", "on_transport", "on_avrcp", "on_volume", "on_metadata", "on_pcm"} <= set(base.SourceCallbacks.__annotations__)


def test_appliance_run_starts_https_healthz_and_stops_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "")                              # no systemd here
    from btkit.sim_a2dp import SimulatedA2dpSource
    import socket as _s
    def free():
        with _s.socket() as x:
            x.bind(("127.0.0.1", 0)); return str(x.getsockname()[1])
    a = AP.parser().parse_args(["--data", str(tmp_path / "data"), "--http-port", free(), "--https-port", free(), "--stream-port", "0",
                                "--advertise-host", "127.0.0.1", "--source", "none", "--thermal-path", "", "--no-mdns", "--no-mqtt",
                                "--boot-dir", str(tmp_path)])
    done, info = threading.Event(), {}

    def ready(svc, ui):
        info.update(svc=svc, ui=ui)
    t = threading.Thread(target=AP.run, args=(a, SimulatedA2dpSource()), kwargs={"ready": ready, "done": done}, daemon=True)
    t.start()
    t0 = time.time()
    while "ui" not in info and time.time() - t0 < 15:
        time.sleep(0.05)
    svc, ui = info["svc"], info["ui"]
    try:
        c = http.client.HTTPConnection("127.0.0.1", ui.port, timeout=5); c.request("GET", "/healthz", headers={"Host": "localhost"})
        h = json.loads(c.getresponse().read())
        assert h["status"] in ("ok", "degraded") and h["checks"]["control_loop"]
        ctx = ssl.create_default_context(cafile=svc.access.certs.p["ca_crt"])
        s = http.client.HTTPSConnection("127.0.0.1", ui.tls_port, timeout=5, context=ctx)
        s.sock = ctx.wrap_socket(__import__("socket").create_connection(("127.0.0.1", ui.tls_port)), server_hostname="hifi-bridge.local")
        s.request("GET", "/trust", headers={"Host": "hifi-bridge.local"})
        assert s.getresponse().status == 200
        status = (tmp_path / "hifi-status.txt").read_text()
        assert svc.access.certs.ca_info()["sha256"] in status                # fingerprint for the out-of-band trust check
    finally:
        done.set(); t.join(10)
    assert not t.is_alive()


def test_main_reports_an_unavailable_source_with_exit_code_2(tmp_path, capsys):
    assert AP.main(["--data", str(tmp_path), "--source", "bluez", "--dbus", "unix:path=/nonexistent/bus"]) == 2
    assert "cannot start source" in capsys.readouterr().err


def test_build_source_variants():
    assert AP.build_source("none", "SYSTEM") is None
    assert AP.build_source("sim", "SYSTEM").__class__.__name__ == "SimulatedA2dpSource"
    assert AP.build_source("btkit.sim_a2dp:SimulatedA2dpSource", "SYSTEM").__class__.__name__ == "SimulatedA2dpSource"


def test_first_start_hint_points_to_where_the_setup_code_really_is(tmp_path):
    """Found by the demo: after provisioning the generated code file is gone, so the hint must point to the owner sheet instead."""
    from btbridge.config.store import ConfigStore
    from btbridge.webui.auth import Auth
    auth = Auth(ConfigStore(str(tmp_path)), iterations=1000)
    assert auth.code_file in AP.first_start_hint(auth)                         # development: generated code file
    auth.adopt_setup_code("ABCD-EFGH-JKLM")
    hint = AP.first_start_hint(auth)
    assert auth.code_file not in hint and "owner sheet" in hint                # provisioned by the flash tool
    auth.complete_setup("127.0.0.1", "ABCD-EFGH-JKLM", "a good long passphrase")
    assert auth.has_password() and AP.first_start_hint(auth) is None
