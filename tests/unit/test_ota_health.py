"""UNIT (FAKE RAUC client, local /healthz server, temporary data directory): the post-boot health check that marks the slot good or
rolls back. The real U-Boot/RAUC boot-count path is a hardware-gate item (G2 P3-3/P3-4)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakes_ota import FakeRaucClient

from btbridge.ota import health as H
from btbridge.ota.manager import StateFile


class Healthz:
    def __init__(self):
        self.status, self.body = 200, {"status": "ok"}
        srv = self

        class Hd(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                b = srv.body if isinstance(srv.body, bytes) else json.dumps(srv.body).encode()
                self.send_response(srv.status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/healthz"

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


@pytest.fixture
def hz():
    h = Healthz()
    yield h
    h.close()


def run(tmp_path, rauc, hz, **kw):
    opts = dict(healthz=hz.url, data_mount="/", timeout=0, interval=0, version="0.4.0", current_boot=lambda: "boot-2",
                log=lambda m: None)
    opts.update(kw)
    return H.run(str(tmp_path), rauc, **opts)


def trial_state(tmp_path, to="B"):
    with StateFile(str(tmp_path / "ota")).edit() as st:
        st["phase"] = "rebooting"
        st["install"] = {"kind": "update", "from_slot": "A", "to_slot": to, "from_version": "0.3.0", "to_version": "0.4.0",
                         "boot_id": "boot-1"}


def test_healthy_boot_marks_good_and_records_the_slot_version(tmp_path, hz):
    rauc = FakeRaucClient()
    assert run(tmp_path, rauc, hz) == 0
    assert rauc.marks == [("good", "rootfs.0")]
    rec = json.loads((tmp_path / "ota" / "health.json").read_text())
    assert rec["ok"] and rec["slot"] == "A" and not rec["trial"] and rec["marked"] == "good:rootfs.0"
    assert all(rec["checks"].values()) and set(rec["checks"]) == {"data_mounted", "data_writable", "bridge", "rauc"}
    assert json.loads((tmp_path / "ota" / "slots.json").read_text())["A"]["version"] == "0.4.0"


def test_degraded_bridge_is_still_healthy(tmp_path, hz):
    hz.body = {"status": "degraded", "degraded": ["lyrion_unreachable"]}
    assert run(tmp_path, FakeRaucClient(), hz) == 0


def test_first_boot_of_the_new_slot_confirms_the_update(tmp_path, hz):
    trial_state(tmp_path)
    rauc = FakeRaucClient(booted="B")
    assert run(tmp_path, rauc, hz) == 0
    st = StateFile(str(tmp_path / "ota")).read()
    assert st["phase"] == "idle" and st["result"]["kind"] == "updated" and st["result"]["from_version"] == "0.3.0"
    assert rauc.marks == [("good", "rootfs.1")]


@pytest.mark.parametrize("problem", ["failed", "down", "garbage", "data"])
def test_unhealthy_first_boot_marks_bad_and_asks_for_the_reboot(tmp_path, hz, problem):
    trial_state(tmp_path)
    rauc = FakeRaucClient(booted="B")
    kw = {}
    if problem == "failed":
        hz.status, hz.body = 503, {"status": "failed"}
    elif problem == "down":
        kw["healthz"] = "http://127.0.0.1:9/healthz"
    elif problem == "garbage":
        hz.body = b"<html>"
    else:
        kw["data_mount"] = str(tmp_path)                               # not a mount point
    assert run(tmp_path, rauc, hz, **kw) == 1
    st = StateFile(str(tmp_path / "ota")).read()
    assert st["phase"] == "rebooting" and st["install"]["failure"] == "health_check_failed" and rauc.marks == [("bad", "rootfs.1")]
    rec = json.loads((tmp_path / "ota" / "health.json").read_text())
    assert not rec["ok"] and rec["trial"] and rec["marked"] == "bad:rootfs.1"


def test_unhealthy_established_slot_is_not_marked_and_not_rebooted(tmp_path, hz):
    hz.status, hz.body = 503, {"status": "failed"}
    rauc = FakeRaucClient()
    assert run(tmp_path, rauc, hz) == 0 and rauc.marks == []
    assert not json.loads((tmp_path / "ota" / "health.json").read_text())["ok"]


def test_rauc_unreachable(tmp_path, hz):
    trial_state(tmp_path)
    rauc = FakeRaucClient(booted="B")
    rauc.down = True
    assert run(tmp_path, rauc, hz) == 0                                 # nothing known: not a trial, nothing marked
    rec = json.loads((tmp_path / "ota" / "health.json").read_text())
    assert rec["slot"] is None and not rec["checks"]["rauc"] and rec["marked"] is None


def test_mark_errors_are_recorded(tmp_path, hz, monkeypatch):
    rauc = FakeRaucClient()
    monkeypatch.setattr(rauc, "mark", lambda s, n: (_ for _ in ()).throw(H.RaucError("fw_setenv failed")))
    assert run(tmp_path, rauc, hz) == 0
    assert json.loads((tmp_path / "ota" / "health.json").read_text())["marked"] == "error:fw_setenv failed"
    trial_state(tmp_path)
    rauc2 = FakeRaucClient(booted="B")
    monkeypatch.setattr(rauc2, "mark", lambda s, n: (_ for _ in ()).throw(H.RaucUnavailable("gone")))
    hz.status, hz.body = 503, {"status": "failed"}
    assert run(tmp_path, rauc2, hz) == 1


def test_waits_until_the_bridge_is_up(tmp_path, hz):
    hz.status, hz.body = 503, {"status": "failed"}
    clock = iter(range(1000))
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            hz.status, hz.body = 200, {"status": "ok"}
    assert run(tmp_path, FakeRaucClient(), hz, timeout=100, interval=2, clock=lambda: next(clock), sleep=sleep) == 0
    assert sleeps == [2, 2, 2]


def test_installation_of_this_boot_is_left_to_the_bridge(tmp_path, hz):
    with StateFile(str(tmp_path / "ota")).edit() as st:
        st["phase"] = "installing"
        st["install"] = {"from_slot": "A", "to_slot": "B", "boot_id": "boot-2"}
    run(tmp_path, FakeRaucClient(), hz)
    assert StateFile(str(tmp_path / "ota")).read()["phase"] == "installing"


def test_data_dir_not_writable(tmp_path, hz):
    (tmp_path / "ota").mkdir()
    (tmp_path / "ota" / ".health-probe").mkdir()                       # cannot be opened as a file
    res = H.check_once(str(tmp_path), "", hz.url, FakeRaucClient())
    assert res == {"data_mounted": True, "data_writable": False, "bridge": True, "rauc": True}


def test_main_with_no_bus(tmp_path, capsys):
    assert H.main(["--data", str(tmp_path), "--healthz", "http://127.0.0.1:9/x", "--data-mount", "", "--timeout", "0",
                   "--dbus", "unix:path=/nonexistent"]) == 0
    assert "FAILED" in capsys.readouterr().out
