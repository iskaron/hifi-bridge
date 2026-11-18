"""UNIT (in-process FAKE RAUC client, local fake release server, temporary data directories): the update state machine, the pre-update
snapshot, boot reconciliation and fault injection (failed / interrupted installations, RAUC restarts, power cuts between steps)."""
import json
import os
import time

import pytest
from fakes_ota import FakeRaucClient, ReleaseServer

from btbridge.config.store import SCHEMA_VERSION
from btbridge.ota import manager as M
from btbridge.ota.manager import RebootRequest, StateFile, UpdateError, UpdateManager, prepare_data, reconcile
from btbridge.ota.source import UpdateSource

C = "hifi-bridge-rpi3"
TRUST = {"mode": "own", "certificates": [{"role": "own", "subject": "CN=dev", "sha256": "AA:BB"}], "official_source": None}


def wait(pred, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    raise AssertionError("timeout")


class Boot:
    def __init__(self):
        self.id = "boot-1"

    def __call__(self):
        return self.id


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "device_name": "Wohnzimmer"}))
    (data / "secrets.json").write_text('{"stream_token": "t"}')
    (data / "certs").mkdir()
    (data / "certs" / "ca.crt").write_text("CA")
    rauc, boot, reboots = FakeRaucClient(), Boot(), []

    def make(**kw):
        opts = dict(trust=TRUST, version="0.3.0", reboot=lambda: reboots.append(1), current_boot=boot, log=lambda m: None,
                    monitor_s=0.01, free=lambda p: 10 ** 12)
        opts.update(kw)
        return UpdateManager(str(data), rauc, **opts)
    return type("E", (), dict(data=data, rauc=rauc, boot=boot, reboots=reboots, make=make, m=make()))


def upload(m, rauc, version="0.4.0", compatible=C, data=b"bundle-bytes"):
    p = m.begin_upload(len(data))
    open(p, "wb").write(data)
    rauc.bundles[m.bundle_path] = {"compatible": compatible, "version": version}
    return m.finish_upload("hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb")


def install(env, version="0.4.0"):
    upload(env.m, env.rauc, version)
    env.m.install()
    wait(lambda: env.m.info()["phase"] != "installing")
    return env.m.info()


# ---------------------------------------------------------------- read side
def test_fresh_device_info(env):
    i = env.m.info()
    assert i["phase"] == "idle" and i["version"] == "0.3.0" and not i["reboot_required"] and not i["can_rollback"]
    assert i["rauc"]["available"] and i["rauc"]["booted"] == "A" and i["rauc"]["compatible"] == C
    assert [(s["bootname"], s["booted"], s["primary"], s["boot_status"], s["version"]) for s in i["rauc"]["slots"]] == \
        [("A", True, True, "good", "0.3.0"), ("B", False, False, "bad", None)]
    assert i["trust"]["mode"] == "own" and i["trust"]["official_source"] is None and not i["check_available"]
    env.rauc.down = True
    i = env.m.info()
    assert not i["rauc"]["available"] and i["other_slot"] is None and not i["can_rollback"]


# ---------------------------------------------------------------- upload + verification
def test_upload_is_verified_by_rauc_and_staged(env):
    staged = upload(env.m, env.rauc)
    assert staged["version"] == "0.4.0" and staged["origin"] == "upload" and staged["size"] == 12
    i = env.m.info()
    assert i["phase"] == "staged" and i["staged"]["name"].endswith(".raucb") and os.path.exists(env.m.bundle_path)


@pytest.mark.parametrize("bundle,code", [({"compatible": "hifi-bridge-rpi4", "version": "0.4.0"}, "wrong_compatible"),
                                         ("signature verification failed", "signature_invalid")])
def test_refused_bundles_are_deleted_and_reported(env, bundle, code):
    p = env.m.begin_upload(5)
    open(p, "wb").write(b"12345")
    env.rauc.bundles[env.m.bundle_path] = bundle
    with pytest.raises(UpdateError) as e:
        env.m.finish_upload("x.raucb")
    assert e.value.code == code
    i = env.m.info()
    assert i["phase"] == "idle" and i["error"]["code"] == code and not os.path.exists(env.m.bundle_path)


def test_upload_limits_and_busy(env):
    with pytest.raises(UpdateError, match="too_large"):
        env.m.begin_upload(M.MAX_BUNDLE + 1)
    small = env.make(free=lambda p: 1000)
    with pytest.raises(UpdateError, match="no_space"):
        small.begin_upload(10_000)
    env.m.begin_upload(10)
    assert env.m.info()["phase"] == "uploading"
    with pytest.raises(UpdateError, match="busy"):
        env.m.begin_upload(10)
    env.m.upload_progress(5)
    assert env.m.info()["progress"]["done"] == 5
    with pytest.raises(UpdateError, match="no_file"):
        env.m.finish_upload("x")                                       # nothing was written
    assert env.m.info()["error"]["code"] == "no_file"
    env.m.begin_upload(10)
    open(env.m.bundle_path + ".upload", "wb").write(b"x")
    env.m.abort_upload("truncated", "client went away")
    assert not os.path.exists(env.m.bundle_path + ".upload") and env.m.info()["error"]["code"] == "truncated"
    env.m.begin_upload(10)
    env.m.abort_upload(None)
    assert env.m.info()["phase"] == "idle"


def test_rauc_down_during_verification(env):
    p = env.m.begin_upload(3)
    open(p, "wb").write(b"abc")
    env.rauc.down = True
    with pytest.raises(UpdateError, match="rauc_unavailable"):
        env.m.finish_upload("x")
    env.rauc.down = False
    assert env.m.info()["error"]["code"] == "rauc_unavailable" and not os.path.exists(env.m.bundle_path)


def test_discard(env):
    upload(env.m, env.rauc)
    env.m.discard()
    i = env.m.info()
    assert i["phase"] == "idle" and i["staged"] is None and not os.path.exists(env.m.bundle_path)


# ---------------------------------------------------------------- install
def test_successful_install_snapshots_data_and_waits_for_the_owners_reboot(env):
    i = install(env)
    assert i["phase"] == "installed" and i["reboot_required"] and i["install"]["to_slot"] == "B" and i["install"]["from_version"] == "0.3.0"
    assert env.rauc.installs == [env.m.bundle_path] and not os.path.exists(env.m.bundle_path)       # space freed after install
    snap = env.data / "ota" / "snapshots" / "A"
    meta = json.loads((snap / "meta.json").read_text())
    assert meta["version"] == "0.3.0" and meta["slot"] == "A" and meta["schema_version"] == SCHEMA_VERSION
    assert (snap / "settings.json").read_text() == (env.data / "settings.json").read_text() and (snap / "certs" / "ca.crt").exists()
    assert env.m.info()["snapshot"]["version"] == "0.3.0"
    assert env.reboots == []                                            # never restarts on its own
    env.m.request_reboot()
    assert env.reboots == [1] and env.m.info()["phase"] == "rebooting"


def test_install_preconditions(env):
    with pytest.raises(UpdateError, match="busy"):
        env.m.install()                                                 # nothing staged
    upload(env.m, env.rauc)
    os.unlink(env.m.bundle_path)
    with pytest.raises(UpdateError, match="no_file"):
        env.m.install()
    upload(env.m, env.rauc)
    env.rauc.operation = "installing"                                   # someone else is using RAUC
    with pytest.raises(UpdateError, match="busy"):
        env.m.install()
    env.rauc.operation = "idle"
    env.rauc.down = True
    with pytest.raises(UpdateError, match="rauc_unavailable"):
        env.m.install()


def test_failed_install_keeps_the_bundle_for_a_retry(env):
    env.rauc.plan = {"result": 1, "error": "Failed mounting bundle: Failed to open /dev/loop-control"}
    i = install(env)
    assert i["phase"] == "staged" and i["error"]["code"] == "install_failed" and "loop-control" in i["error"]["detail"]
    assert os.path.exists(env.m.bundle_path) and i["install"] is None and env.rauc.primary_slot == "rootfs.0"


def test_rauc_refusing_the_install_call(env):
    env.rauc.plan = {"refuse": "Already processing a different method"}
    upload(env.m, env.rauc)
    with pytest.raises(UpdateError, match="install_failed"):
        env.m.install()
    assert env.m.info()["phase"] == "staged"


def test_rauc_restart_during_install_is_reported_as_interrupted(env):
    env.rauc.plan = {"crash": True}
    i = install(env)
    assert i["phase"] == "staged" and i["error"]["code"] == "install_interrupted" and env.rauc.primary_slot == "rootfs.0"


def test_lost_completed_signal_but_slot_activated_counts_as_success(env):
    env.rauc.plan = {"result": 0, "no_signal": True}
    assert install(env)["phase"] == "installed"


def test_rauc_gone_for_too_long_during_install(env):
    env.m = env.make(rauc_gone_s=0.05)
    env.rauc.plan = {"hang": True}
    upload(env.m, env.rauc)
    env.m.install()
    env.rauc.down = True
    wait(lambda: env.m.state.read()["phase"] != "installing")
    env.rauc.down = False
    assert env.m.info()["error"]["code"] == "install_interrupted"


def test_monitor_survives_a_short_rauc_outage(env):
    env.rauc.plan = {"hang": True}
    upload(env.m, env.rauc)
    env.m.install()
    env.rauc.down = True
    time.sleep(0.05)
    env.rauc.down = False
    env.rauc.plan = {"result": 0}
    env.rauc._polls = 0
    wait(lambda: env.m.state.read()["phase"] == "installed")


def test_finish_after_phase_changed_is_ignored(env):
    env.m._finish(0, "")                                                # stale monitor: no installation in progress
    assert env.m.info()["phase"] == "idle"


# ---------------------------------------------------------------- after the install: cancel / reboot / confirm / rollback
def test_owner_can_decline_the_restart(env):
    install(env)
    env.m.cancel_installed()
    i = env.m.info()
    assert i["phase"] == "idle" and i["result"]["kind"] == "cancelled" and ("active", "rootfs.0") in env.rauc.marks
    assert env.rauc.primary_slot == "rootfs.0"
    with pytest.raises(UpdateError, match="busy"):
        env.m.request_reboot()


def test_cancel_with_rauc_down(env):
    install(env)
    env.rauc.down = True
    with pytest.raises(UpdateError, match="rauc_unavailable"):
        env.m.cancel_installed()


def reboot_into(env, bootname):
    env.boot.id = f"boot-{bootname}-{time.monotonic()}"
    env.rauc.booted = bootname
    env.m = env.make()
    env.m.resume()


def test_new_slot_boots_and_health_confirms_the_update(env):
    from btbridge.ota.health import run as health
    install(env)
    env.m.request_reboot()
    reboot_into(env, "B")
    assert env.m.info()["phase"] == "confirming"
    code = health(str(env.data), env.rauc, healthz="http://127.0.0.1:9/healthz", data_mount="", timeout=0, version="0.4.0",
                  current_boot=env.boot, log=lambda m: None)
    assert code == 1                                                    # bridge not reachable -> trial failed -> roll back
    reboot_into(env, "A")
    i = env.m.info()
    assert i["phase"] == "idle" and i["result"]["kind"] == "rolled_back" and i["result"]["reason"] == "health_check_failed"
    assert i["result"]["to_version"] == "0.3.0" and env.rauc.slot["rootfs.1"]["boot-status"] == "bad"


def test_boot_count_fallback_without_health_is_reported_as_rollback(env):
    install(env)
    env.m.request_reboot()
    reboot_into(env, "A")                                               # U-Boot gave up on B
    r = env.m.info()["result"]
    assert r["kind"] == "rolled_back" and r["reason"] == "boot_failed" and r["from_version"] == "0.4.0"


def test_power_cut_before_the_owner_rebooted_still_ends_in_the_new_slot(env):
    install(env)                                                        # phase installed, no reboot requested
    reboot_into(env, "B")
    assert env.m.info()["phase"] == "confirming"


def test_manual_rollback_to_the_previous_release(env):
    env.rauc.slot["rootfs.1"].update({"boot-status": "good", "bundle.version": "0.2.0"})
    i = env.m.info()
    assert i["can_rollback"] and i["other_slot"]["version"] == "0.2.0"
    upload(env.m, env.rauc)                                             # a staged bundle is dropped by a rollback
    env.m.rollback()
    i = env.m.info()
    assert i["phase"] == "switching" and i["reboot_required"] and env.rauc.primary_slot == "rootfs.1" and i["staged"] is None
    env.m.request_reboot()
    reboot_into(env, "B")
    with StateFile(str(env.data / "ota")).edit() as st:
        M.confirm(st, "B", "0.2.0")
    r = env.m.info()["result"]
    assert r["kind"] == "switched" and r["to_version"] == "0.2.0" and r["from_version"] == "0.3.0"


def test_rollback_needs_a_good_slot_with_a_known_version(env):
    with pytest.raises(UpdateError, match="rollback_unavailable"):
        env.m.rollback()                                                # B is empty/bad
    env.rauc.slot["rootfs.1"]["boot-status"] = "good"
    with pytest.raises(UpdateError, match="rollback_unavailable"):
        env.m.rollback()                                                # good, but version unknown
    (env.data / "ota").mkdir(exist_ok=True)
    (env.data / "ota" / "slots.json").write_text('{"B": {"version": "0.2.9"}}')   # recorded by an earlier health check
    assert env.m.info()["other_slot"]["version"] == "0.2.9"
    env.rauc.down = True
    with pytest.raises(UpdateError, match="rollback_unavailable"):
        env.m.rollback()


def test_rollback_with_rauc_refusing(env, monkeypatch):
    env.rauc.slot["rootfs.1"].update({"boot-status": "good", "bundle.version": "0.2.0"})
    monkeypatch.setattr(env.rauc, "mark", lambda s, n: (_ for _ in ()).throw(M.RaucError("bootloader error")))
    with pytest.raises(UpdateError, match="rauc_unavailable"):
        env.m.rollback()


def test_bridge_restart_while_rauc_keeps_installing(env):
    env.rauc.plan = {"hang": True}
    upload(env.m, env.rauc)
    env.m.install()
    env.m = env.make()                                                  # bridge restarted (same boot)
    env.m.resume()
    assert env.m.info()["phase"] == "installing"
    env.rauc.plan = {"result": 0}
    wait(lambda: env.m.state.read()["phase"] == "installed")


def test_bridge_restart_after_rauc_finished_unseen(env):
    env.rauc.plan = {"hang": True}
    upload(env.m, env.rauc)
    env.m.install()
    time.sleep(0.05)
    env.rauc.operation, env.rauc.primary_slot = "idle", "rootfs.1"       # RAUC completed while the bridge was down
    env.m = env.make()
    env.m.resume()
    assert env.m.info()["phase"] == "installed"


def test_dismiss_clears_messages(env):
    env.rauc.plan = {"result": 1}
    install(env)
    env.m.dismiss()
    assert env.m.info()["error"] is None


def test_resume_with_rauc_down_changes_nothing(env):
    install(env)
    env.rauc.down = True
    env.boot.id = "other"
    env.m.resume()
    env.rauc.down = False
    assert env.m.state.read()["phase"] == "installed"


# ---------------------------------------------------------------- check + download (official source)
@pytest.fixture
def srv():
    s = ReleaseServer()
    yield s
    s.close()


def official(env, srv, **kw):
    trust = {"mode": "official", "certificates": [], "official_source": srv.source()}
    return env.make(trust=trust, source=UpdateSource(srv.source(), allow_http=True), download_opts={"allow_http": True}, **kw)


def test_check_then_download_then_verify(env, srv):
    data = b"B" * 50_000
    name = "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
    srv.assets[name] = data
    srv.releases = [srv.release("v0.4.0", [(name, {})])]
    m = official(env, srv)
    with pytest.raises(UpdateError, match="no_update"):
        m.start_download()
    res = m.check()
    assert res["status"] == "update_available" and m.info()["check"]["latest"]["version"] == "0.4.0"
    env.rauc.bundles[m.bundle_path] = {"compatible": C, "version": "0.4.0"}
    m.start_download()
    wait(lambda: m.info()["phase"] == "staged")
    i = m.info()
    assert i["staged"]["origin"] == "download" and i["staged"]["name"] == name and open(m.bundle_path, "rb").read() == data


def test_download_errors_and_cancel(env, srv):
    name = "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
    srv.assets[name] = b"B" * 10
    srv.releases = [srv.release("v0.4.0", [(name, {"digest": "sha256:" + "00" * 32})])]
    m = official(env, srv)
    m.check()
    m.start_download()
    wait(lambda: m.info()["phase"] == "idle")
    assert m.info()["error"]["code"] == "digest_mismatch"
    srv.releases = [srv.release("v0.4.0", [(name, {})])]
    srv.assets[name] = b"B" * 2_000_000
    srv.releases[0]["assets"][0]["size"] = 2_000_000
    m.check()
    m.dismiss()
    m.start_download()
    with pytest.raises(UpdateError, match="busy"):
        m.start_download()
    m.cancel_download()
    wait(lambda: m.info()["phase"] == "idle")
    assert m.info()["error"] is None


def test_download_too_large_and_unexpected_failure(env, srv, monkeypatch):
    name = "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
    srv.assets[name] = b"B" * 100
    srv.releases = [srv.release("v0.4.0", [(name, {})])]
    m = official(env, srv, max_bundle=10)
    m.check()
    with pytest.raises(UpdateError, match="too_large"):
        m.start_download()
    m = official(env, srv)
    monkeypatch.setattr(M.Download, "run", lambda self: 1 / 0)
    m.start_download()
    wait(lambda: m.info()["phase"] == "idle" and m.info()["error"])
    assert m.info()["error"]["code"] == "download_failed"


def test_check_needs_a_source_and_rauc(env, srv):
    with pytest.raises(UpdateError, match="no_source"):
        env.m.check()
    m = official(env, srv)
    env.rauc.down = True
    with pytest.raises(UpdateError, match="rauc_unavailable"):
        m.check()


def test_download_paused_is_visible(env, srv):
    m = official(env, srv)
    os.makedirs(m.ota_dir, exist_ok=True)
    open(m.bundle_path + ".part", "wb").write(b"x")
    assert m.info()["download_paused"]


# ---------------------------------------------------------------- pure reconciliation
def st(phase, **inst):
    s = M.empty_state()
    s["phase"] = phase
    s["install"] = {"from_slot": "A", "to_slot": "B", "from_version": "0.3.0", "to_version": "0.4.0", "boot_id": "b1", **inst}
    return s


def test_reconcile_table(tmp_path):
    assert reconcile(st("installing"), None, "b2")["phase"] == "installing"                       # RAUC unknown: undecided
    assert reconcile(st("installing"), "A", "b1", rauc_busy=True)["phase"] == "installing"
    assert reconcile(st("installing"), "A", "b1", primary="B")["phase"] == "installed"
    r = reconcile(st("installing"), "A", "b1", primary="A", last_error="boom")
    assert r["phase"] == "idle" and r["error"]["code"] == "install_interrupted" and r["error"]["detail"] == "boom"
    assert reconcile(st("installing"), "B", "b2")["phase"] == "confirming"                        # power cut after RAUC finished
    s = st("installing")
    bundle = tmp_path / "b.raucb"
    bundle.write_bytes(b"x")
    s["staged"] = {"path": str(bundle)}
    r = reconcile(s, "A", "b2")
    assert r["phase"] == "staged" and r["error"]["code"] == "install_interrupted"                 # power cut during the install
    s = st("installing")
    s["staged"] = {"path": str(tmp_path / "gone")}
    assert reconcile(s, "A", "b2")["phase"] == "idle"
    assert reconcile(st("installed"), "A", "b1")["phase"] == "installed"                          # not rebooted yet
    assert reconcile(st("switching", kind="switch"), "B", "b2")["phase"] == "confirming"
    assert reconcile(st("idle"), "A", "b2")["phase"] == "idle"


def test_state_file_tolerates_garbage(tmp_path):
    sf = StateFile(str(tmp_path / "ota"))
    assert sf.read()["phase"] == "idle"
    open(sf.path, "w").write("{broken")
    assert sf.read()["phase"] == "idle"
    open(sf.path, "w").write(json.dumps({"format": 99, "phase": "installing"}))
    assert sf.read()["phase"] == "idle"
    open(sf.path, "w").write(json.dumps({"format": 1, "phase": "exploded"}))
    assert sf.read()["phase"] == "idle"


# ---------------------------------------------------------------- owner-requested snapshot restore (never automatic)
def downgrade_scenario(env):
    """0.3.0 on A installs 0.4.0 into B; 0.4.0 changes the data; the device is back on A (0.3.0)."""
    install(env)
    env.m.request_reboot()
    reboot_into(env, "A")
    env.reboots.clear()
    newer = {"schema_version": SCHEMA_VERSION + 1, "device_name": "Changed", "brand_new_setting": 1}
    (env.data / "settings.json").write_text(json.dumps(newer))
    (env.data / "network.json").write_text("{}")                       # created by the newer version
    import shutil
    shutil.rmtree(env.data / "certs")
    return newer


def test_newer_data_is_never_replaced_automatically(env):
    newer = downgrade_scenario(env)
    assert prepare_data(str(env.data), version="0.3.0", bootname="A", log=lambda m: None) == "none"
    assert json.loads((env.data / "settings.json").read_text()) == newer       # ConfigStore keeps unknown keys (review-fix stream)


def test_owner_requested_restore_is_applied_once_at_the_next_start(env):
    downgrade_scenario(env)
    m = env.make()
    assert m.info()["snapshot"]["version"] == "0.3.0" and not m.info()["restore_requested"]
    m.request_snapshot_restore()
    assert env.reboots == [1] and m.info()["restore_requested"]
    logs = []
    assert prepare_data(str(env.data), version="0.3.0", bootname="A", log=logs.append) == "restored"
    assert json.loads((env.data / "settings.json").read_text())["device_name"] == "Wohnzimmer"
    assert not (env.data / "network.json").exists() and (env.data / "certs" / "ca.crt").read_text() == "CA"
    assert StateFile(str(env.data / "ota")).read()["restored"]["version"] == "0.3.0" and "restored" in logs[0]
    assert prepare_data(str(env.data), version="0.3.0", bootname="A", log=logs.append) == "none"           # exactly once


@pytest.mark.parametrize("version,slot,req", [("0.4.0", "A", None), ("0.3.0", "B", None), ("0.3.0", "A", "stale"),
                                              ("0.3.0", "A", "garbage")])
def test_restore_request_must_match_version_slot_and_snapshot(env, version, slot, req):
    downgrade_scenario(env)
    env.make().request_snapshot_restore()
    p = env.data / "ota" / M.RESTORE_REQUEST
    if req == "stale":                                                   # a newer snapshot replaced the one the owner chose
        p.write_text(json.dumps({**json.loads(p.read_text()), "created": 1.0}))
    elif req == "garbage":
        p.write_text("{oops")
    assert prepare_data(str(env.data), version=version, bootname=slot, log=lambda m: None) == "mismatch"
    assert not p.exists() and json.loads((env.data / "settings.json").read_text())["device_name"] == "Changed"


def test_restore_needs_a_snapshot_of_this_version(env):
    with pytest.raises(UpdateError, match="no_snapshot"):
        env.m.request_snapshot_restore()
    install(env)
    with pytest.raises(UpdateError, match="busy"):
        env.m.request_snapshot_restore()                                 # an installed update waits for its restart


def test_snapshot_replaces_an_older_one(env, tmp_path):
    ota = str(env.data / "ota")
    M.take_snapshot(str(env.data), ota, "A", "0.1.0", 1)
    M.take_snapshot(str(env.data), ota, "A", "0.3.0", 1)
    assert M.snapshot_meta(ota, "A")["version"] == "0.3.0" and M.snapshot_meta(ota, "B") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    M.take_snapshot(str(empty), ota, "B", "0.3.0", 1)
    assert M.snapshot_meta(ota, "B")["files"] == []


# ---------------------------------------------------------------- small helpers
def test_boot_id_and_cmdline(tmp_path):
    p = tmp_path / "x"
    assert M.boot_id(str(p)) == "unknown" and M.booted_slot_from_cmdline(str(p)) is None
    p.write_text("abc\n")
    assert M.boot_id(str(p)) == "abc" and M.booted_slot_from_cmdline(str(p)) is None
    p.write_text("console=tty1 root=/dev/mmcblk0p3 rauc.slot=B rootwait")
    assert M.booted_slot_from_cmdline(str(p)) == "B"


def test_reboot_request_file(tmp_path):
    RebootRequest(str(tmp_path / "req"))()
    assert (tmp_path / "req").exists() and (tmp_path / "req").stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------- remaining edge cases
def test_prepare_data_with_a_minimal_snapshot(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION}))
    M.take_snapshot(str(data), str(data / "ota"), "A", "0.3.0", SCHEMA_VERSION)        # no secrets, network or certificates
    meta = M.snapshot_meta(str(data / "ota"), "A")
    M.atomic_json(str(data / "ota" / M.RESTORE_REQUEST), {"slot": "A", "created": meta["created"]})
    assert prepare_data(str(data), version="0.3.0", bootname="A", log=lambda m: None) == "restored"
    assert not (data / "secrets.json").exists() and not (data / "certs").exists()


def test_download_with_the_real_free_space_check(env, srv):
    name = "hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
    srv.assets[name] = b"B" * 100
    srv.releases = [srv.release("v0.4.0", [(name, {})])]
    m = official(env, srv, free=None)
    m.check()
    env.rauc.bundles[m.bundle_path] = {"compatible": C, "version": "0.4.0"}
    m.start_download()
    wait(lambda: m.info()["phase"] == "staged")


def test_a_new_upload_replaces_the_staged_bundle(env):
    upload(env.m, env.rauc, "0.4.0", data=b"first")
    assert upload(env.m, env.rauc, "0.4.1", data=b"second!")["version"] == "0.4.1"
    assert open(env.m.bundle_path, "rb").read() == b"second!"


def test_stale_completed_signals_are_dropped_before_installing(env, monkeypatch):
    env.rauc._completed = [1, 1]                                           # left over from an installation started elsewhere
    assert install(env)["phase"] == "installed"


def test_signal_drain_tolerates_a_rauc_blip(env, monkeypatch):
    upload(env.m, env.rauc)
    real = env.rauc.completed
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise M.RaucUnavailable("blip")
        return real()
    monkeypatch.setattr(env.rauc, "completed", flaky)
    env.m.install()
    wait(lambda: env.m.state.read()["phase"] != "installing")
    assert env.m.info()["phase"] == "installed"


def test_monitor_tolerates_errors_while_deciding(env, monkeypatch):
    env.rauc.plan = {"crash": True}
    upload(env.m, env.rauc)
    real = env.rauc.completed

    def completed():
        if env.rauc.installs and env.rauc.operation == "idle":              # the late-signal check after RAUC went idle
            raise M.RaucUnavailable("gone again")
        return real()
    monkeypatch.setattr(env.rauc, "completed", completed)
    monkeypatch.setattr(env.rauc, "primary", lambda: (_ for _ in ()).throw(M.RaucError("no bootloader")))
    env.m.install()
    wait(lambda: env.m.state.read()["phase"] != "installing")
    assert env.m.info()["error"]["code"] == "install_interrupted"
