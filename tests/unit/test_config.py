"""UNIT tests: persistent configuration store and schema."""
import json
import os
import stat

import pytest

from btbridge.config import ConfigStore, SettingError, validate
from btbridge.config.schema import SETTINGS


def test_defaults_follow_the_decision_register(tmp_path):
    c = ConfigStore(str(tmp_path))
    assert c.get("pause_strategy") == "drop_restart" and c.get("icy_metadata") is False
    assert c.get("override_lms") is True and c.get("pairing_seconds") == 300 and c.get("setup_complete") is False


def test_update_persists_and_survives_reload(tmp_path):
    c = ConfigStore(str(tmp_path))
    changed = c.update({"device_name": "  Wohnzimmer   Bridge ", "lms_host": "LMS.local", "lms_port": "9002", "audio_allowed": "off"})
    assert changed == {"device_name": "Wohnzimmer Bridge", "lms_host": "lms.local", "lms_port": 9002, "audio_allowed": False}
    c2 = ConfigStore(str(tmp_path))
    assert c2.get("device_name") == "Wohnzimmer Bridge" and c2.get("lms_port") == 9002 and c2.get("audio_allowed") is False


def test_update_is_all_or_nothing(tmp_path):
    c = ConfigStore(str(tmp_path))
    with pytest.raises(SettingError) as e:
        c.update({"device_name": "ok", "lms_port": 70000})
    assert e.value.key == "lms_port" and e.value.code == "too_large"
    assert c.get("device_name") == "Hi-Fi Bridge"


@pytest.mark.parametrize("key,value,code", [
    ("unknown", 1, "unknown_setting"), ("pause_strategy", "stop_everything", "invalid_choice"),
    ("lms_host", "bad host!", "invalid_host"), ("device_name", "", "invalid_name"), ("device_name", "x" * 33, "invalid_name"),
    ("lms_port", "abc", "invalid_int"), ("audio_allowed", "maybe", "invalid_bool"), ("target_player", "a b", "invalid_player"),
    ("mqtt_base_topic", "a/+/b", "invalid_topic"), ("device_labels", {"not-a-mac": "x"}, "invalid_address"),
    ("pairing_seconds", 10, "too_small"), ("lms_port", True, "invalid_int"),
])
def test_validation_rejects_bad_values(key, value, code):
    with pytest.raises(SettingError) as e:
        validate({key: value})
    assert e.value.code == code


def test_ip_hosts_and_player_ids_are_normalised():
    assert validate({"lms_host": "192.168.1.10"})["lms_host"] == "192.168.1.10"
    assert validate({"lms_host": "[fd00::1]"})["lms_host"] == "fd00::1"
    assert validate({"target_player": "02:00:00:00:00:AB"})["target_player"] == "02:00:00:00:00:ab"
    assert validate({"device_labels": {"aa:bb:cc:dd:ee:ff": "  Annas   Handy "}})["device_labels"] == {"AA:BB:CC:DD:EE:FF": "Annas Handy"}


def test_listeners_get_only_real_changes_and_errors_do_not_break_saving(tmp_path):
    c = ConfigStore(str(tmp_path)); seen = []
    c.on_change(lambda ch: seen.append(ch)); c.on_change(lambda ch: 1 / 0)
    c.update({"theme": "dark"}); c.update({"theme": "dark"})
    assert seen == [{"theme": "dark"}] and ConfigStore(str(tmp_path)).get("theme") == "dark"


def test_files_are_private_and_secrets_are_separate(tmp_path):
    c = ConfigStore(str(tmp_path)); c.set_secret("admin_password_hash", "pbkdf2$x")
    for f in ("settings.json", "secrets.json"):
        assert stat.S_IMODE(os.stat(tmp_path / f).st_mode) == 0o600
    assert "admin_password_hash" not in (tmp_path / "settings.json").read_text()
    assert json.loads((tmp_path / "secrets.json").read_text())["admin_password_hash"] == "pbkdf2$x"
    assert c.secret("stream_token") and c.secret("session_key")        # generated on first start
    red = c.export_redacted()
    assert "pbkdf2$x" not in json.dumps(red) and "admin_password_hash" in red["secrets_present"]


def test_corrupt_settings_file_is_moved_aside_and_defaults_used(tmp_path):
    (tmp_path / "settings.json").write_text("{ not json")
    logs = []
    c = ConfigStore(str(tmp_path), log=logs.append)
    assert c.get("device_name") == "Hi-Fi Bridge"
    assert any(p.name.startswith("settings.json.corrupt-") for p in tmp_path.iterdir()) and logs


def test_invalid_stored_value_falls_back_to_default_and_unversioned_file_is_migrated(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"lms_port": 99999, "device_name": "Old", "obsolete": 1}))
    c = ConfigStore(str(tmp_path))
    assert c.get("lms_port") == 9000 and c.get("device_name") == "Old"
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data["schema_version"] == 1 and "obsolete" not in data


def test_atomic_write_leaves_no_temp_files(tmp_path):
    c = ConfigStore(str(tmp_path))
    for i in range(20):
        c.update({"device_name": f"name {i}"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secrets.json", "settings.json"]


def test_every_setting_default_validates():
    for k, s in SETTINGS.items():
        validate({k: s.default})
