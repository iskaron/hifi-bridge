"""UNIT: the appliance entry point with ``--updates rauc`` against the SIMULATED RAUC D-Bus service on a private bus: the update manager is
attached and reconciled, the image's trust record is read, and an owner-requested snapshot restore is applied before the configuration
loads. Without ``--updates`` nothing changes (no update service)."""
import json
import socket
import threading
import time

from btbridge import __version__
from btbridge import appliance as AP
from btbridge.config.store import SCHEMA_VERSION
from btbridge.ota import manager as M
from btkit.fake_bluez import PrivateBus
from btkit.fake_rauc import FakeRauc
from btkit.sim_a2dp import SimulatedA2dpSource


def free():
    with socket.socket() as x:
        x.bind(("127.0.0.1", 0))
        return str(x.getsockname()[1])


def start(a):
    done, info = threading.Event(), {}
    t = threading.Thread(target=AP.run, args=(a, SimulatedA2dpSource()), kwargs={"ready": lambda svc, ui: info.update(svc=svc), "done": done},
                         daemon=True)
    t.start()
    t0 = time.time()
    while "svc" not in info and time.time() - t0 < 15:
        time.sleep(0.05)
    return info["svc"], done, t


def args(tmp_path, *extra):
    return AP.parser().parse_args(["--data", str(tmp_path / "data"), "--http-port", free(), "--https-port", "0", "--stream-port", "0",
                                   "--advertise-host", "127.0.0.1", "--source", "none", "--thermal-path", "", "--no-mdns", "--no-mqtt",
                                   "--net-agent", str(tmp_path / "none.sock"), *extra])


def test_updates_rauc_attaches_the_manager_and_applies_a_requested_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "device_name": "Before update"}))
    M.take_snapshot(str(data), str(data / "ota"), "A", __version__, SCHEMA_VERSION)
    meta = M.snapshot_meta(str(data / "ota"), "A")
    (data / "settings.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "device_name": "Changed later"}))
    M.atomic_json(str(data / "ota" / M.RESTORE_REQUEST), {"slot": "A", "created": meta["created"]})
    monkeypatch.setattr(M, "booted_slot_from_cmdline", lambda path="/proc/cmdline": "A")
    trust = tmp_path / "trust.json"
    trust.write_text('{"mode": "own", "certificates": [], "official_source": null}')
    bus = PrivateBus()
    rauc = FakeRauc(bus.address)
    rauc.start()
    try:
        svc, done, t = start(args(tmp_path, "--updates", "rauc", "--dbus", bus.address, "--update-trust", str(trust)))
        try:
            assert svc.store.get("device_name") == "Before update"                 # restored BEFORE the configuration loaded
            i = svc.ota.info()
            assert i["rauc"]["available"] and i["rauc"]["booted"] == "A" and i["trust"]["mode"] == "own"
            assert not (data / "ota" / M.RESTORE_REQUEST).exists()
        finally:
            done.set(); t.join(10)
    finally:
        rauc.stop(); bus.close()


def test_a_crashing_restore_step_never_keeps_the_webui_down(tmp_path, monkeypatch, capsys):
    """Review N-4: an exception in prepare_data aborted appliance.run before the WebUI started; with the request file still there,
    every restart repeated it until systemd's start limit left the device without a WebUI."""
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    monkeypatch.setattr(M, "prepare_data", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected")))
    trust = tmp_path / "trust.json"
    trust.write_text('{"mode": "own", "certificates": [], "official_source": null}')
    bus = PrivateBus()
    rauc = FakeRauc(bus.address)
    rauc.start()
    try:
        svc, done, t = start(args(tmp_path, "--updates", "rauc", "--dbus", bus.address, "--update-trust", str(trust)))
        try:
            assert svc.ota is not None and "snapshot restore step failed (RuntimeError: unexpected)" in capsys.readouterr().err
        finally:
            done.set(); t.join(10)
    finally:
        rauc.stop(); bus.close()


def test_without_updates_there_is_no_update_service(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    svc, done, t = start(args(tmp_path))
    try:
        assert svc.ota is None
    finally:
        done.set(); t.join(10)
