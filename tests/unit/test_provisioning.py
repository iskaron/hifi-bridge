"""UNIT: provisioning file (P6) - strict validation without leaking values, idempotent apply under a journal, power-cut safety, scrubbing
in place, the hotspot rule, and the boot-partition step (bootfiles) that ties recovery, provisioning, CA and status together."""
import json
import os

import pytest

from btbridge import bootfiles, provisioning as P
from btbridge.config import ConfigStore
from btbridge.webui.auth import Auth, verify_password, normalise_code

GOOD = {"format": P.FORMAT, "created_by": "test", "setup_code": "ABCD-EFGH-JKMN", "device_name": "Wohnzimmer",
        "trust_key": P.encode_trust_key(bytes(range(32))), "country": "DE",
        "hotspot": {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"},
        "wifi": {"ssid": "Home", "psk": "wifi-secret-123"}}


def write(boot, doc_or_bytes):
    raw = doc_or_bytes if isinstance(doc_or_bytes, bytes) else P.render(doc_or_bytes)
    (boot / P.FILE_NAME).write_bytes(raw)


def dirs(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(parents=True); boot.mkdir(parents=True)
    (boot / P.FILE_NAME).write_bytes(P.placeholder())
    return data, boot


def test_placeholder_is_an_empty_slot_of_exact_size():
    ph = P.placeholder()
    assert len(ph) == P.SLOT_SIZE and P.is_empty(ph) and P.parse(ph) is None and json.loads(ph)["state"] == "empty"
    assert P.is_empty(b"\x00" * 100) and P.is_empty(b"   \n")


@pytest.mark.parametrize("change,problem", [
    ({"setup_code": "ABCD-EFGH-JKM0"}, "setup_code:invalid"),                 # 0 is not in the alphabet
    ({"hotspot": {"ssid": "x", "psk": "short-11ch"}}, "hotspot.psk:required_12_to_63_printable_ascii"),
    ({"hotspot": {"ssid": "x"}}, "hotspot.psk:required_12_to_63_printable_ascii"),       # no hotspot without an owner-known password
    ({"wifi": {"ssid": "", "psk": "12345678"}}, "wifi.ssid:invalid"),
    ({"country": "de"}, "country:required_iso_3166_alpha2"),
    ({"country": "00"}, "country:required_iso_3166_alpha2"),
    ({"trust_key": "AAAA"}, "trust_key:invalid"),
    ({"device_name": ""}, "device_name:invalid"),
    ({"admin_password": "x"}, "admin_password:unknown_field"),
    ({"format": "hifi-bridge-provision/2"}, "format:unsupported"),
])
def test_validation_reports_field_codes_never_values(change, problem):
    doc = {**GOOD, **change}
    problems = P.validate(doc)
    assert problem in problems
    assert not any(str(v) in " ".join(problems) for v in (GOOD["hotspot"]["psk"], GOOD["wifi"]["psk"], GOOD["setup_code"]))


def test_render_refuses_documents_that_do_not_fit_the_slot():
    with pytest.raises(P.ProvisionError):
        P.render({**GOOD, "created_by": "x" * 64}, size=200)


def test_apply_sets_owner_known_code_trust_key_and_network_and_scrubs_the_file(tmp_path):
    data, boot = dirs(tmp_path)
    write(boot, GOOD)
    r = P.consume(str(boot), str(data), iterations=1000)
    assert r["result"] == "applied"
    store = ConfigStore(str(data))
    assert verify_password(normalise_code("abcd efgh jkmn"), store.secret("setup_code_hash"))
    assert not (data / "setup-code.txt").exists()                           # the owner already knows the code: no plaintext copy
    assert store.secret("trust_key") == GOOD["trust_key"] and store.get("device_name") == "Wohnzimmer"
    net = json.loads((data / "network.json").read_text())
    assert net["country"] == "DE" and net["hotspot"]["psk"] == "correct horse battery" and oct(os.stat(data / "network.json").st_mode)[-3:] == "600"
    raw = (boot / P.FILE_NAME).read_bytes()
    assert len(raw) == P.SLOT_SIZE and P.is_empty(raw) and b"correct horse" not in raw and b"ABCD" not in raw
    assert P.consume(str(boot), str(data))["result"] == "none"              # once


def test_invalid_file_is_rejected_nothing_applied_and_still_scrubbed(tmp_path):
    data, boot = dirs(tmp_path)
    write(boot, json.dumps({**GOOD, "hotspot": {"ssid": "x", "psk": "short"}}).encode())
    r = P.consume(str(boot), str(data))
    assert r["result"] == "invalid" and "hotspot.psk:required_12_to_63_printable_ascii" in r["problems"]
    assert not (data / "network.json").exists() and ConfigStore(str(data)).secret("trust_key") is None
    assert b"short" not in (boot / P.FILE_NAME).read_bytes()
    write(boot, b"{not json")
    assert P.consume(str(boot), str(data))["problems"] == ["document:not_json"]


def test_power_cut_during_apply_repeats_it_completely(tmp_path, monkeypatch):
    data, boot = dirs(tmp_path)
    write(boot, GOOD)
    real = P.apply
    def crash(*a, **k):
        real(*a, **k); raise RuntimeError("power cut")
    monkeypatch.setattr(P, "apply", crash)
    with pytest.raises(RuntimeError):
        P.consume(str(boot), str(data), iterations=1000)
    assert not P.is_empty((boot / P.FILE_NAME).read_bytes()) and P._journal(str(data))["state"] == "applying"
    monkeypatch.setattr(P, "apply", real)
    assert P.consume(str(boot), str(data), iterations=1000)["result"] == "applied" and P.is_empty((boot / P.FILE_NAME).read_bytes())


def test_power_cut_during_scrub_finishes_the_scrub_without_reapplying(tmp_path, monkeypatch):
    data, boot = dirs(tmp_path)
    write(boot, GOOD)
    calls = []
    real_apply, real_scrub = P.apply, P.scrub
    monkeypatch.setattr(P, "apply", lambda *a, **k: calls.append(1) or real_apply(*a, **k))
    def torn(path):                                                          # half of the file overwritten, then the power is gone
        with open(path, "r+b") as fh:
            fh.write(P.placeholder(os.path.getsize(path))[:40])
        raise RuntimeError("power cut")
    monkeypatch.setattr(P, "scrub", torn)
    with pytest.raises(RuntimeError):
        P.consume(str(boot), str(data), iterations=1000)
    monkeypatch.setattr(P, "scrub", real_scrub)
    assert P.consume(str(boot), str(data), iterations=1000)["result"] == "none"
    assert calls == [1] and P.is_empty((boot / P.FILE_NAME).read_bytes()) and P._journal(str(data))["state"] == "done"


def test_a_device_with_an_owner_password_ignores_the_setup_code(tmp_path):
    data, boot = dirs(tmp_path)
    store = ConfigStore(str(data)); auth = Auth(store, iterations=1000)
    auth.complete_setup("c", open(auth.code_file).read(), "a good long passphrase")
    write(boot, GOOD)
    r = P.consume(str(boot), str(data), iterations=1000)
    assert any("already set" in n for n in r["notes"]) and ConfigStore(str(data)).secret("trust_key") is None


def test_setting_the_owner_password_ends_the_trust_key(tmp_path):
    data, boot = dirs(tmp_path)
    write(boot, GOOD)
    P.consume(str(boot), str(data), iterations=1000)
    store = ConfigStore(str(data)); auth = Auth(store, iterations=1000)
    auth.complete_setup("c", "ABCDEFGHJKMN", "a good long passphrase")
    assert store.secret("trust_key") is None and store.secret("setup_code_hash") is None


def test_without_network_entries_the_device_never_opens_a_hotspot(tmp_path):
    data, boot = dirs(tmp_path)
    write(boot, {"format": P.FORMAT, "setup_code": "ABCD-EFGH-JKMN"})
    r = P.consume(str(boot), str(data), iterations=1000)
    assert "Ethernet only" in " ".join(r["notes"]) and "hotspot" not in json.loads((data / "network.json").read_text())


def test_bootfiles_status_shows_fingerprint_and_a_setup_code_only_when_the_device_generated_it(tmp_path):
    data, boot = dirs(tmp_path)
    r = bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)
    status = (boot / "hifi-status.txt").read_text()
    assert r["provisioning"]["result"] == "none" and r["ca_sha256"] in status and "One-time setup code:" in status
    code = (data / "setup-code.txt").read_text().strip()
    assert code in status
    data2, boot2 = dirs(tmp_path / "b")
    write(boot2, GOOD)
    bootfiles.run(str(data2), str(boot2), iterations=1000, log=lambda m: None)
    status2 = (boot2 / "hifi-status.txt").read_text()
    assert "Provisioning applied" in status2 and "setup code" not in status2.split("Provisioning")[0].lower()
    for secret in ("ABCD-EFGH-JKMN", "ABCDEFGHJKMN", "correct horse", "wifi-secret", GOOD["trust_key"], "PRIVATE"):
        assert secret not in status2
    ca_key = data2 / "certs" / "ca.key"
    assert ca_key.exists() and oct(os.stat(ca_key).st_mode)[-3:] == "600"


def test_cli_check_and_placeholder(capsys, monkeypatch, tmp_path):
    import io, sys
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(P.render(GOOD))))
    assert P.main(["check"]) == 0 and "valid" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"format":"x"}')))
    assert P.main(["check"]) == 1
