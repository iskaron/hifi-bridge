"""UNIT (real ConfigStore in a temporary directory): configuration backup export/import, explicit secrets handling, tamper detection,
format versioning and migration."""
import base64
import hashlib
import hmac
import json

import pytest

from btbridge.config import ConfigStore
from btbridge.ota import backup as B

PASS = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(str(tmp_path / "a"))
    s.update({"device_name": "Wohnzimmer", "lms_host": "10.0.0.5", "mqtt_enabled": True, "mqtt_host": "broker.lan"})
    s.set_secret("admin_password_hash", "pbkdf2$hash")
    s.set_secret("mqtt_password", "mqtt-secret")
    s.set_secret("trust_key", "never-exported")
    return s


@pytest.fixture
def target(tmp_path):
    return ConfigStore(str(tmp_path / "b"))


def test_default_export_contains_no_secret_at_all(store):
    raw = B.export(store, version="0.4.0", clock=lambda: 1000)
    doc = json.loads(raw)
    assert doc["format"] == B.FORMAT and doc["format_version"] == 1 and doc["app_version"] == "0.4.0" and doc["created"] == 1000
    assert doc["secrets"] is None and doc["settings"]["device_name"] == "Wohnzimmer"
    assert "instance_id" not in doc["settings"] and "setup_complete" not in doc["settings"]
    text = raw.decode()
    for secret in ("pbkdf2$hash", "mqtt-secret", "never-exported", store.secret("stream_token"), store.secret("session_key")):
        assert secret not in text


def test_encrypted_export_hides_the_secrets_and_restores_them(store, target):
    raw = B.export(store, include_secrets=True, passphrase=PASS)
    for secret in ("pbkdf2$hash", "mqtt-secret", store.secret("stream_token")):
        assert secret not in raw.decode()
    assert "never-exported" not in raw.decode() and store.secret("session_key") not in raw.decode()
    p = B.plan(B.parse(raw), PASS)
    assert p["secrets"] == {"admin_password_hash": "pbkdf2$hash", "mqtt_password": "mqtt-secret", "stream_token": store.secret("stream_token")}
    res = B.apply(target, p)
    assert target.get("device_name") == "Wohnzimmer" and target.get("mqtt_host") == "broker.lan"
    assert target.secret("mqtt_password") == "mqtt-secret" and target.secret("admin_password_hash") == "pbkdf2$hash"
    assert res["secrets"] == ["admin_password_hash", "mqtt_password", "stream_token"] and "device_name" in res["changed"]
    assert target.secret("trust_key") is None


def test_secret_backup_needs_the_right_passphrase(store):
    doc = B.parse(B.export(store, include_secrets=True, passphrase=PASS))
    with pytest.raises(B.BackupError, match="passphrase_required"):
        B.plan(doc, "")
    with pytest.raises(B.BackupError, match="wrong_passphrase"):
        B.plan(doc, PASS + "x")
    with pytest.raises(B.BackupError, match="weak_passphrase"):
        B.export(store, include_secrets=True, passphrase="short")


@pytest.mark.parametrize("field", ["settings", "ciphertext", "created", "kdf_n"])
def test_tampering_with_any_part_is_detected(store, field):
    doc = json.loads(B.export(store, include_secrets=True, passphrase=PASS))
    if field == "settings":
        doc["settings"]["lms_host"] = "evil.example"
    elif field == "ciphertext":
        ct = bytearray(base64.b64decode(doc["secrets"]["ciphertext"])); ct[0] ^= 1
        doc["secrets"]["ciphertext"] = base64.b64encode(bytes(ct)).decode()
    elif field == "created":
        doc["created"] += 1
    else:
        doc["secrets"]["n"] = 2 ** 15
    with pytest.raises(B.BackupError, match="wrong_passphrase"):
        B.plan(B.parse(json.dumps(doc).encode()), PASS)


def test_a_settings_only_backup_restores_without_touching_secrets(store, target):
    before = target.secret("stream_token")
    res = B.apply(target, B.plan(B.parse(B.export(store)), PASS))
    assert target.secret("stream_token") == before and res["secrets"] == [] and not res["had_secrets"]


def test_newer_backup_settings_are_skipped_and_reported(store, target):
    doc = json.loads(B.export(store))
    doc["settings"]["future_option"] = 1
    doc["settings"]["instance_id"] = "abcdef12"
    p = B.plan(B.parse(json.dumps(doc).encode()))
    assert p["skipped"] == ["future_option"] and "instance_id" not in p["settings"]


def test_invalid_settings_refuse_the_whole_restore(store, target):
    doc = json.loads(B.export(store))
    doc["settings"]["lms_port"] = 99999
    doc["settings"]["theme"] = "neon"
    with pytest.raises(B.BackupError) as e:
        B.plan(B.parse(json.dumps(doc).encode()))
    assert e.value.code == "invalid_settings" and e.value.detail == "lms_port, theme"
    assert target.get("device_name") == "Hi-Fi Bridge"


@pytest.mark.parametrize("raw,code", [
    (b"\xff\xfe", "invalid_file"), (b"[1]", "wrong_format"), (b'{"format": "other"}', "wrong_format"),
    (b'{"format": "hifi-bridge-backup", "format_version": "1"}', "wrong_format"),
    (b'{"format": "hifi-bridge-backup", "format_version": 0}', "wrong_format"),
    (b'{"format": "hifi-bridge-backup", "format_version": 2}', "too_new"),
    (b'{"format": "hifi-bridge-backup", "format_version": 1, "settings": []}', "invalid_file"),
    (b'{"format": "hifi-bridge-backup", "format_version": 1, "settings": {}, "secrets": "x"}', "invalid_file"),
    (b" " * (B.MAX_BACKUP + 1), "invalid_file"),
])
def test_malformed_files(raw, code):
    with pytest.raises(B.BackupError) as e:
        B.parse(raw)
    assert e.value.code == code


@pytest.mark.parametrize("change", [{"kdf": "pbkdf2"}, {"n": "x"}, {"n": 3}, {"n": 2 ** 20}, {"r": 99}, {"salt": "***"}, {"p": None}])
def test_unusable_secret_parameters(store, change):
    doc = json.loads(B.export(store, include_secrets=True, passphrase=PASS))
    doc["secrets"].update(change)
    with pytest.raises(B.BackupError, match="invalid_file"):
        B.decrypt(doc, PASS)


def test_correctly_tagged_garbage_is_refused(store):
    doc = json.loads(B.export(store, include_secrets=True, passphrase=PASS))
    s = doc["secrets"]
    salt, nonce = base64.b64decode(s["salt"]), base64.b64decode(s["nonce"])
    enc, mac = B._keys(PASS, salt, s["n"], s["r"], s["p"])
    ct = B._stream(enc, nonce, b"not json")
    s["ciphertext"] = base64.b64encode(ct).decode()
    s["tag"] = base64.b64encode(hmac.new(mac, B._header(doc) + nonce + ct, hashlib.sha256).digest()).decode()
    with pytest.raises(B.BackupError, match="invalid_file"):
        B.decrypt(doc, PASS)


def test_older_format_versions_are_migrated(store, monkeypatch):
    doc = json.loads(B.export(store))
    doc["format_version"] = 1
    doc["settings"]["old_name"] = doc["settings"].pop("device_name")

    def v1_to_v2(d):
        d["settings"]["device_name"] = d["settings"].pop("old_name")
        return {**d, "format_version": 2}
    monkeypatch.setattr(B, "FORMAT_VERSION", 2)
    monkeypatch.setitem(B.MIGRATIONS, 1, v1_to_v2)
    p = B.plan(B.parse(json.dumps(doc).encode()))
    assert p["settings"]["device_name"] == "Wohnzimmer" and p["skipped"] == []
