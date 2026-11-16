"""UNIT: local recovery (password reset, factory reset, boot-partition request) - only own files, request runs exactly once, power-cut safe."""
import os

import pytest

from btbridge import recovery as R
from btbridge.config import ConfigStore
from btbridge.webui.auth import Auth


def setup_device(d):
    store = ConfigStore(str(d)); auth = Auth(store, iterations=1000)
    auth.complete_setup("c", open(auth.code_file).read(), "a good long passphrase")
    store.update({"device_name": "Wohnzimmer"})
    (d / "certs").mkdir(exist_ok=True); (d / "certs" / "ca.key").write_text("k"); (d / "certs" / "ca.crt").write_text("c")
    (d / "session.json").write_text("{}"); (d / "faults.jsonl").write_text("{}\n")
    (d / "unrelated.txt").write_text("keep me")
    return store


def test_reset_owner_password_issues_a_new_code_and_keeps_settings(tmp_path):
    setup_device(tmp_path)
    code = R.reset_owner_password(str(tmp_path))
    store = ConfigStore(str(tmp_path))
    assert store.secret("admin_password_hash") is None and store.get("device_name") == "Wohnzimmer"
    assert (tmp_path / "setup-code.txt").read_text().strip() == code


def test_factory_reset_removes_only_own_known_files(tmp_path):
    setup_device(tmp_path)
    removed = R.factory_reset(str(tmp_path))
    assert {"settings.json", "secrets.json", "session.json", "faults.jsonl", "certs/ca.key", "certs/ca.crt"} <= set(removed)
    assert sorted(os.listdir(tmp_path)) == ["unrelated.txt"] and (tmp_path / "unrelated.txt").read_text() == "keep me"
    assert ConfigStore(str(tmp_path)).get("device_name") == "Hi-Fi Bridge"                   # defaults again


def test_boot_request_runs_once_and_reports_on_the_boot_partition(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    setup_device(data)
    assert R.boot_request(str(data), str(boot)) == "none"
    (boot / "hifi-recovery").write_text("reset-owner-password\n")
    assert R.boot_request(str(data), str(boot)) == "reset-owner-password"
    assert not (boot / "hifi-recovery").exists() and "New one-time setup code:" in (boot / "hifi-status.txt").read_text()
    assert R.boot_request(str(data), str(boot)) == "none"                                     # at most once
    (boot / "hifi-recovery").write_text("rm -rf /")
    assert R.boot_request(str(data), str(boot)) == "unknown" and "ignored" in (boot / "hifi-status.txt").read_text()
    assert (data / "settings.json").exists()                                                  # nothing happened
    (boot / "hifi-recovery").write_text("factory-reset")
    assert R.boot_request(str(data), str(boot)) == "factory-reset" and not (data / "settings.json").exists()


def test_cli_requires_confirmation_for_factory_reset(tmp_path, capsys):
    setup_device(tmp_path)
    assert R.main(["--data", str(tmp_path), "factory-reset"]) == 3 and (tmp_path / "settings.json").exists()
    assert R.main(["--data", str(tmp_path), "factory-reset", "--yes"]) == 0 and not (tmp_path / "settings.json").exists()
    assert R.main(["--data", str(tmp_path / "nope"), "reset-owner-password"]) == 2


def test_recovery_is_not_reachable_from_the_webui():
    import btbridge.webui.views as V
    assert "recovery" not in open(V.__file__).read() and not any("reset" in p for (_, p) in V.routes(None))


def test_boot_status_contains_no_secrets(tmp_path):
    R.write_boot_status(str(tmp_path), ["Hi-Fi Bridge hifi-bridge.local", "CA fingerprint (SHA-256): AA:BB"])
    text = (tmp_path / "hifi-status.txt").read_text()
    assert "AA:BB" in text and "PRIVATE KEY" not in text and len(text.splitlines()) == 2


def test_appliance_processes_a_boot_request_before_starting(tmp_path, monkeypatch):
    import btbridge.appliance as AP
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    store = ConfigStore(str(data)); auth = Auth(store, iterations=1000)
    auth.complete_setup("c", open(auth.code_file).read(), "a good long passphrase")
    (boot / "hifi-recovery").write_text("reset-owner-password")
    class Stop(Exception): pass
    def stop_here(*a, **k): raise Stop()
    monkeypatch.setattr(AP, "ApplianceService", stop_here)          # stop right after the recovery step
    a = AP.parser().parse_args(["--data", str(data), "--boot-dir", str(boot), "--source", "none"])
    with pytest.raises(Stop):
        AP.run(a, None)
    status = (boot / "hifi-status.txt").read_text()
    assert not (boot / "hifi-recovery").exists() and "One-time setup code:" in status and "Device CA fingerprint" in status



class PowerCut(Exception):
    pass


def cut():
    raise PowerCut()


@pytest.mark.parametrize("step", ["after_running", "after_action"])
def test_power_cut_before_done_repeats_the_action_on_the_next_boot(tmp_path, step):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    setup_device(data)
    (boot / "hifi-recovery").write_text("factory-reset\n")
    with pytest.raises(PowerCut):
        R.boot_request(str(data), str(boot), hooks={step: cut})
    assert (boot / "hifi-recovery").exists() and R._read_journal(str(data))["state"] == "running"
    assert R.boot_request(str(data), str(boot)) == "factory-reset"                          # repeated (idempotent) and finished
    assert not (boot / "hifi-recovery").exists() and not (data / "settings.json").exists() and R._read_journal(str(data)) == {}


def test_power_cut_after_done_never_repeats_a_password_reset(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    setup_device(data)
    (boot / "hifi-recovery").write_text("reset-owner-password")
    with pytest.raises(PowerCut):
        R.boot_request(str(data), str(boot), hooks={"after_done": cut})
    code = (data / "setup-code.txt").read_text()
    assert R.boot_request(str(data), str(boot)) == "already-done"
    assert (data / "setup-code.txt").read_text() == code and not (boot / "hifi-recovery").exists()   # no second reset


def test_the_same_request_can_be_placed_again_later_and_the_journal_survives_a_factory_reset(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    setup_device(data)
    for _ in range(2):
        (boot / "hifi-recovery").write_text("factory-reset")
        assert R.boot_request(str(data), str(boot)) == "factory-reset"
    assert "recovery-journal.json" not in R.OWN_FILES
