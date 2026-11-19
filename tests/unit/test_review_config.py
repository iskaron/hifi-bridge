"""UNIT (real files): review M8 - the config store never resets a device because of an I/O error, keeps what a newer version
wrote (OTA rollback), and the recovery CLI never leaves root-owned files behind (reports/REVIEW_PHASE2B_EXISTING_CODE.md)."""
import builtins
import json
import os
import subprocess
import sys

import pytest

from btbridge import recovery
from btbridge.config import ConfigStore
from btbridge.config.store import ConfigUnreadable


def _deny(monkeypatch, victim):
    real = builtins.open

    def fake_open(path, *a, **k):
        if os.path.basename(str(path)) == victim:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)


@pytest.mark.parametrize("victim", ["secrets.json", "settings.json"])
def test_an_unreadable_file_is_an_error_not_a_factory_reset(tmp_path, monkeypatch, victim):
    c = ConfigStore(str(tmp_path)); c.set_secret("admin_password_hash", "pbkdf2_sha256$1$x$y"); c.update({"device_name": "Küche"})
    before = (tmp_path / victim).read_bytes()
    _deny(monkeypatch, victim)
    with pytest.raises(ConfigUnreadable):
        ConfigStore(str(tmp_path))                            # was: moved aside -> owner password, stream token, trust key gone
    monkeypatch.undo()
    assert (tmp_path / victim).read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    c2 = ConfigStore(str(tmp_path))
    assert c2.secret("admin_password_hash") and c2.get("device_name") == "Küche"


def test_undecodable_files_are_still_moved_aside(tmp_path):
    (tmp_path / "secrets.json").write_bytes(b"\xff\xfe not utf-8")
    c = ConfigStore(str(tmp_path))
    assert c.secret("stream_token") and [p for p in tmp_path.iterdir() if p.name.startswith("secrets.json.corrupt-")]


def test_a_newer_versions_settings_survive_load_and_save(tmp_path):
    """OTA rollback: the older version must keep what the newer one wrote, so the upgrade again finds it."""
    (tmp_path / "settings.json").write_text(json.dumps({"schema_version": 2, "future": 1, "device_name": "Bad", "stream_format": "opus"}))
    c = ConfigStore(str(tmp_path))
    assert c.get("stream_format") == "wav" and c.get("device_name") == "Bad"   # unusable value: default at runtime
    c.update({"device_name": "Küche"})
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data["schema_version"] == 2 and data["future"] == 1 and data["stream_format"] == "opus" and data["device_name"] == "Küche"
    c.update({"stream_format": "flac"})                                         # an explicit change replaces the kept value
    assert json.loads((tmp_path / "settings.json").read_text())["stream_format"] == "flac"


def test_unknown_secrets_survive(tmp_path):
    (tmp_path / "secrets.json").write_text(json.dumps({"stream_token": "t", "session_key": "k", "future_secret": "s"}))
    c = ConfigStore(str(tmp_path)); c.set_secret("mqtt_password", "p")
    data = json.loads((tmp_path / "secrets.json").read_text())
    assert data["future_secret"] == "s" and data["mqtt_password"] == "p"
    with pytest.raises(KeyError):
        c.secret("future_secret")                                               # still not readable through the API


@pytest.mark.parametrize("ver", ["1.0", None, True, [1], 1.5])
def test_a_malformed_schema_version_does_not_crash_or_drop_settings(tmp_path, ver):
    (tmp_path / "settings.json").write_text(json.dumps({"schema_version": ver, "device_name": "Küche", "future": 1}))
    c = ConfigStore(str(tmp_path))                                              # was: int("1.0") -> ValueError at start
    assert c.get("device_name") == "Küche"
    assert json.loads((tmp_path / "settings.json").read_text())["future"] == 1


# ---------------------------------------------------------------- recovery CLI runs as the data owner
def test_recovery_refuses_a_foreign_non_root_user(tmp_path, monkeypatch, capsys):
    ConfigStore(str(tmp_path))
    owner = os.stat(tmp_path).st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1 if owner + 1 != 0 else owner + 2)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert recovery.main(["--data", str(tmp_path), "reset-owner-password"]) == 4
    assert "owner" in capsys.readouterr().err and sorted(p.name for p in tmp_path.iterdir()) == before


def test_recovery_as_root_drops_to_the_data_owner_first(tmp_path, monkeypatch):
    ConfigStore(str(tmp_path))
    if os.geteuid() == 0:
        os.chown(tmp_path, 1000, 1000)                        # the data dir belongs to the service user, not root
    st = os.stat(tmp_path)
    dropped = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(recovery, "_drop_to", lambda uid, gid: dropped.append((uid, gid)))
    assert recovery.main(["--data", str(tmp_path), "reset-owner-password"]) == 0
    assert dropped == [(st.st_uid, st.st_gid)]


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to create a foreign-owned data dir (runs in the root lab runner)")
def test_recovery_run_by_root_leaves_files_the_service_user_can_read():
    import pathlib
    import shutil
    import tempfile
    top = pathlib.Path(tempfile.mkdtemp(prefix="hifi-recovery-"))
    top.chmod(0o755)                                          # like /var/lib: traversable for the service user
    d = top / "data"; d.mkdir(mode=0o700)
    try:
        _root_recovery(d)
    finally:
        shutil.rmtree(top, ignore_errors=True)


def _root_recovery(d):
    ConfigStore(str(d)).set_secret("admin_password_hash", "x")
    for root, dirs, files in os.walk(d):
        for n in [root] + [os.path.join(root, f) for f in files]:
            os.chown(n, 1000, 1000)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    r = subprocess.run([sys.executable, "-m", "btbridge.recovery", "--data", str(d), "reset-owner-password"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert {os.stat(d / n).st_uid for n in os.listdir(d)} == {1000}
    code = "import sys; from btbridge.config import ConfigStore; s = ConfigStore(sys.argv[1]); print(bool(s.secret('setup_code_hash')))"
    r = subprocess.run([sys.executable, "-c", code, str(d)], capture_output=True, text=True, env=env, timeout=60,
                       user=1000, group=1000, extra_groups=[])
    assert r.returncode == 0 and r.stdout.strip() == "True", r.stderr
