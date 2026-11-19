"""UNIT (real ConfigStore in a temporary directory, REAL openssl CLI of the lab image): configuration backup export/import, explicit
secrets handling, AES-256-CTR via `openssl enc` with the key material only on a pipe, encrypt-then-MAC tamper detection, format
versioning and migration."""
import base64
import json
import os
import stat

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


def secret_doc(store):
    return json.loads(B.export(store, include_secrets=True, passphrase=PASS))


def script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_default_export_contains_no_secret_at_all(store):
    raw = B.export(store, version="0.4.0", clock=lambda: 1000)
    doc = json.loads(raw)
    assert doc["format"] == B.FORMAT and doc["format_version"] == 2 and doc["app_version"] == "0.4.0" and doc["created"] == 1000
    assert doc["secrets"] is None and doc["settings"]["device_name"] == "Wohnzimmer"
    assert "instance_id" not in doc["settings"] and "setup_complete" not in doc["settings"]
    text = raw.decode()
    for secret in ("pbkdf2$hash", "mqtt-secret", "never-exported", store.secret("stream_token"), store.secret("session_key")):
        assert secret not in text


def test_round_trip_through_real_openssl(store, target):
    raw = B.export(store, include_secrets=True, passphrase=PASS)
    doc = json.loads(raw)
    s = doc["secrets"]
    assert s["cipher"] == "aes-256-ctr" and s["openssl"] == B.OPENSSL_MODE and s["kdf"] == "scrypt" and s["n"] == 2 ** 14
    assert "nonce" not in s and len(base64.b64decode(s["salt"])) == 16 and len(base64.b64decode(s["tag"])) == 32
    for secret in ("pbkdf2$hash", "mqtt-secret", store.secret("stream_token"), "never-exported", store.secret("session_key")):
        assert secret not in raw.decode()
    p = B.plan(B.parse(raw), PASS)
    assert p["secrets"] == {"admin_password_hash": "pbkdf2$hash", "mqtt_password": "mqtt-secret", "stream_token": store.secret("stream_token")}
    res = B.apply(target, p)
    assert target.get("device_name") == "Wohnzimmer" and target.get("mqtt_host") == "broker.lan"
    assert target.secret("mqtt_password") == "mqtt-secret" and target.secret("admin_password_hash") == "pbkdf2$hash"
    assert res["secrets"] == ["admin_password_hash", "mqtt_password", "stream_token"] and "device_name" in res["changed"]
    assert target.secret("trust_key") is None
    raw2 = B.export(store, include_secrets=True, passphrase=PASS)                 # fresh salt -> fresh keys, different ciphertext
    assert json.loads(raw2)["secrets"]["ciphertext"] != s["ciphertext"]


def test_the_ciphertext_is_standard_aes_256_ctr(store):
    """Independent check: decrypting with the openssl CLI's own command (key material derived here) gives the secrets back."""
    import subprocess
    s = secret_doc(store)["secrets"]
    enc, _ = B._keys(PASS, base64.b64decode(s["salt"]), s["n"], s["r"], s["p"])
    r, w = os.pipe()
    os.write(w, enc.hex().encode() + b"\n"); os.close(w)
    out = subprocess.run(["openssl", "enc", "-d", "-aes-256-ctr", "-pbkdf2", "-iter", "1", "-md", "sha256", "-nosalt", "-pass", f"fd:{r}"],
                         input=base64.b64decode(s["ciphertext"]), capture_output=True, pass_fds=(r,), check=True).stdout
    os.close(r)
    assert json.loads(out)["mqtt_password"] == "mqtt-secret"


def test_secret_backup_needs_the_right_passphrase(store):
    doc = B.parse(B.export(store, include_secrets=True, passphrase=PASS))
    with pytest.raises(B.BackupError, match="passphrase_required"):
        B.plan(doc, "")
    with pytest.raises(B.BackupError, match="wrong_passphrase"):
        B.plan(doc, PASS + "x")
    with pytest.raises(B.BackupError, match="weak_passphrase"):
        B.export(store, include_secrets=True, passphrase="short")


@pytest.mark.parametrize("field", ["settings", "created", "format_version_header", "kdf_n", "salt", "ciphertext", "tag", "truncated"])
def test_tampering_with_header_ciphertext_or_tag_is_detected_before_decryption(store, field, monkeypatch):
    doc = secret_doc(store)
    s = doc["secrets"]
    flip = lambda b64, i=0: base64.b64encode(bytes(x ^ (1 if j == i else 0) for j, x in enumerate(base64.b64decode(b64)))).decode()
    if field == "settings":
        doc["settings"]["lms_host"] = "evil.example"
    elif field == "created":
        doc["created"] += 1
    elif field == "format_version_header":
        doc["app_version"] = "9.9.9"
    elif field == "kdf_n":
        s["n"] = 2 ** 15
    elif field == "salt":
        s["salt"] = flip(s["salt"])
    elif field == "ciphertext":
        s["ciphertext"] = flip(s["ciphertext"], 3)
    elif field == "tag":
        s["tag"] = flip(s["tag"], 31)
    else:
        s["ciphertext"] = base64.b64encode(base64.b64decode(s["ciphertext"])[:-4]).decode()
    calls = []
    monkeypatch.setattr(B, "_openssl", lambda *a: calls.append(a))
    with pytest.raises(B.BackupError, match="wrong_passphrase"):
        B.plan(B.parse(json.dumps(doc).encode()), PASS)
    assert calls == []                                                      # openssl never saw the modified ciphertext


def test_truncated_file_is_not_a_backup(store):
    raw = B.export(store, include_secrets=True, passphrase=PASS)
    with pytest.raises(B.BackupError, match="invalid_file"):
        B.parse(raw[:-40])


def test_key_material_never_on_the_command_line_or_in_the_environment(store, tmp_path, monkeypatch):
    """Process-list check: a wrapper records what the openssl process itself sees (/proc/self/cmdline, /proc/self/environ)."""
    log = tmp_path / "proc.log"
    wrapper = script(tmp_path, "openssl", f'tr "\\0" " " < /proc/$$/cmdline >> {log}; echo >> {log}; tr "\\0" "\\n" < /proc/$$/environ >> {log}\n'
                                          f'ls /proc/$$/fd >> {log}\nexec openssl "$@"\n')
    monkeypatch.setattr(B, "OPENSSL", wrapper)
    doc = secret_doc(store)
    B.plan(B.parse(json.dumps(doc).encode()), PASS)
    seen = log.read_text()
    s = doc["secrets"]
    enc, mac = B._keys(PASS, base64.b64decode(s["salt"]), s["n"], s["r"], s["p"])
    assert "-pass fd:" in seen and "aes-256-ctr" in seen
    for secret in (PASS, enc.hex(), mac.hex(), enc.hex().upper()):
        assert secret not in seen
    assert not any(n.startswith("tmp") for n in os.listdir(tmp_path) if n not in ("openssl", "proc.log"))   # no key file left behind


def test_openssl_missing_failing_or_hanging(store, tmp_path, monkeypatch):
    monkeypatch.setattr(B.shutil, "which", lambda name: None)
    with pytest.raises(B.BackupError, match="crypto_unavailable"):
        B.export(store, include_secrets=True, passphrase=PASS)
    monkeypatch.setattr(B, "OPENSSL", str(tmp_path / "no-such-openssl"))
    with pytest.raises(B.BackupError, match="crypto_unavailable"):
        B.export(store, include_secrets=True, passphrase=PASS)
    monkeypatch.setattr(B, "OPENSSL", script(tmp_path, "failing", "exit 3\n"))
    with pytest.raises(B.BackupError, match="crypto_failed"):
        B.export(store, include_secrets=True, passphrase=PASS)
    monkeypatch.setattr(B, "OPENSSL", script(tmp_path, "hanging", "exec sleep 5\n"))
    monkeypatch.setattr(B, "OPENSSL_TIMEOUT", 0.3)
    with pytest.raises(B.BackupError, match="crypto_failed: timeout"):
        B.export(store, include_secrets=True, passphrase=PASS)


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
    (b'{"format": "hifi-bridge-backup", "format_version": "2"}', "wrong_format"),
    (b'{"format": "hifi-bridge-backup", "format_version": 1, "settings": {}, "secrets": null}', "wrong_format"),   # pre-release format
    (b'{"format": "hifi-bridge-backup", "format_version": 3}', "too_new"),
    (b'{"format": "hifi-bridge-backup", "format_version": 2, "settings": []}', "invalid_file"),
    (b'{"format": "hifi-bridge-backup", "format_version": 2, "settings": {}, "secrets": "x"}', "invalid_file"),
    (b" " * (B.MAX_BACKUP + 1), "invalid_file"),
])
def test_malformed_files(raw, code):
    with pytest.raises(B.BackupError) as e:
        B.parse(raw)
    assert e.value.code == code


@pytest.mark.parametrize("change", [{"kdf": "pbkdf2"}, {"cipher": "aes-128-cbc"}, {"openssl": "enc-other"}, {"n": "x"}, {"n": 3},
                                    {"n": 2 ** 20}, {"r": 99}, {"salt": "***"}, {"p": None}])
def test_unusable_secret_parameters(store, change):
    doc = secret_doc(store)
    doc["secrets"].update(change)
    with pytest.raises(B.BackupError, match="invalid_file"):
        B.decrypt(doc, PASS)


@pytest.mark.parametrize("payload", [b"not json", b"[1, 2]"])
def test_correctly_tagged_foreign_payload_is_refused(store, payload):
    doc = secret_doc(store)
    s = doc["secrets"]
    enc, mac = B._keys(PASS, base64.b64decode(s["salt"]), s["n"], s["r"], s["p"])
    ct = B._openssl(False, enc, payload)
    s["ciphertext"] = base64.b64encode(ct).decode()
    s["tag"] = base64.b64encode(B._tag(mac, doc, ct)).decode()
    with pytest.raises(B.BackupError, match="invalid_file"):
        B.decrypt(doc, PASS)


def test_older_format_versions_are_migrated(store, monkeypatch):
    doc = json.loads(B.export(store))
    doc["settings"]["old_name"] = doc["settings"].pop("device_name")

    def v2_to_v3(d):
        d["settings"]["device_name"] = d["settings"].pop("old_name")
        return {**d, "format_version": 3}
    monkeypatch.setattr(B, "FORMAT_VERSION", 3)
    monkeypatch.setitem(B.MIGRATIONS, 2, v2_to_v3)
    p = B.plan(B.parse(json.dumps(doc).encode()))
    assert p["settings"]["device_name"] == "Wohnzimmer" and p["skipped"] == []
