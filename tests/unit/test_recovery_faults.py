"""UNIT (real files, a forked child for the privilege drop): the recovery and provisioning paths around power cuts and bad input - no old
secret survives a factory reset, a request that disappeared can be placed again, a provisioning file with wrong field types is refused
with field codes, a root console tool acts as the data owner and nobody else. Coverage package 7b-3."""
import json
import os

import pytest
from test_provisioning import GOOD, dirs, write
from test_recovery import setup_device

from btbridge import provisioning as P
from btbridge import recovery as R


# ---------------------------------------------------------------- recovery
def test_a_factory_reset_also_removes_the_corrupt_copies_that_still_hold_old_secrets(tmp_path):
    setup_device(tmp_path)
    (tmp_path / "settings.json.corrupt-1700000000").write_text('{"device_name": "old"}')
    (tmp_path / "secrets.json.corrupt-1700000001").write_text('{"admin_password_hash": "OLD-HASH"}')
    removed = R.factory_reset(str(tmp_path))
    assert "secrets.json.corrupt-1700000001" in removed and "settings.json.corrupt-1700000000" in removed
    assert not [f for f in os.listdir(tmp_path) if ".corrupt-" in f] and (tmp_path / "unrelated.txt").exists()


def test_a_journal_without_its_request_is_forgotten_so_the_same_request_works_again(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    setup_device(data)
    # a power cut after the request file was removed but before the journal was cleared leaves exactly this state
    R._set_journal(str(data), {"state": "done", "digest": "abc", "action": "reset-owner-password", "at": "t"})
    assert R._read_journal(str(data))
    assert R.boot_request(str(data), str(boot)) == "none" and R._read_journal(str(data)) == {}
    (boot / "hifi-recovery").write_text("reset-owner-password")
    assert R.boot_request(str(data), str(boot)) == "reset-owner-password"     # the same request can be placed again


def test_the_boot_journal_helpers_survive_a_missing_directory_and_a_missing_file(tmp_path):
    R._fsync_dir(str(tmp_path / "nowhere"))                                  # best effort durability: no exception
    R._set_journal(str(tmp_path), None)                                      # nothing to remove: no exception


@pytest.mark.skipif(os.geteuid() != 0, reason="the privilege drop needs root (the lab containers run the tests as root)")
def test_the_console_tool_run_as_root_acts_as_the_data_owner_and_a_stranger_is_refused():
    import shutil
    import tempfile
    tmp_path = tempfile.mkdtemp(prefix="hifi-owner-", dir="/tmp")             # reachable for any user (pytest's own tmp dir is 0700)
    os.chmod(tmp_path, 0o755)
    try:
        _drop_and_refuse(tmp_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _drop_and_refuse(tmp_path):
    os.chown(tmp_path, 4242, 4242)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                                              # child: never returns into pytest
        try:
            err = R.run_as_data_owner(tmp_path)
            os.write(w, json.dumps({"err": err, "uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups()}).encode())
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
    os.close(w)
    got = json.loads(os.read(r, 1000))
    assert got == {"err": None, "uid": 4242, "gid": 4242, "groups": []}
    pid = os.fork()
    if pid == 0:
        try:
            os.setuid(1000)                                                  # an unrelated user
            err = R.run_as_data_owner(tmp_path)
            os._exit(0 if err and "refusing" in err else 1)
        except BaseException:
            os._exit(2)
    assert os.waitpid(pid, 0)[1] == 0


# ---------------------------------------------------------------- provisioning
@pytest.mark.parametrize("doc,problem", [
    (["not", "an", "object"], "document:not_an_object"),
    ({**GOOD, "created_by": "c" * 65}, "created_by:invalid"),
    ({**GOOD, "created_by": 7}, "created_by:invalid"),
    ({**GOOD, "wifi": "Home"}, "wifi:not_an_object"),
    ({**GOOD, "hotspot": ["x"]}, "hotspot:not_an_object"),
    ({**GOOD, "wifi": {"ssid": "Home", "psk": "short"}}, "wifi.psk:invalid"),
    ({**GOOD, "wifi": {"ssid": "Home", "psk": 12345678}}, "wifi.psk:invalid"),
])
def test_more_provisioning_documents_that_are_refused_with_field_codes_only(doc, problem):
    assert problem in P.validate(doc)
    with pytest.raises(P.ProvisionError) as e:
        P.parse(json.dumps(doc).encode())
    assert problem in e.value.problems and "wifi-secret-123" not in str(e.value.problems)           # codes only, never the values


def test_a_valid_document_with_a_64_digit_hex_wifi_key_is_accepted():
    assert P.validate({**GOOD, "wifi": {"ssid": "Home", "psk": "a" * 64}}) == []                    # a pre-computed WPA key


def test_a_power_cut_after_applying_but_before_scrubbing_does_not_apply_twice_and_reuses_the_notes(tmp_path):
    data, boot = dirs(tmp_path)
    write(boot, GOOD)
    first = P.consume(str(boot), str(data), iterations=1000)
    assert first["result"] == "applied"
    # simulate: the journal says "applied" for this exact file, but the scrub never happened (power cut)
    journal = json.load(open(data / P.JOURNAL))
    write(boot, GOOD)
    json.dump({**journal, "state": "applied"}, open(data / P.JOURNAL, "w"))
    again = P.consume(str(boot), str(data), iterations=1000)
    assert again["result"] == "applied" and again["notes"] == journal["notes"]                       # notes come from the journal
    assert P.is_empty((boot / P.FILE_NAME).read_bytes())                                              # and the secrets are scrubbed now


def test_an_empty_slot_after_a_power_cut_between_scrub_and_the_last_journal_entry_finishes_the_journal(tmp_path):
    data, boot = dirs(tmp_path)
    json.dump({"state": "applied", "digest": "x", "notes": []}, open(data / P.JOURNAL, "w"))
    assert P.consume(str(boot), str(data)) == {"result": "none"}
    assert json.load(open(data / P.JOURNAL))["state"] == "done"


def test_the_placeholder_command_writes_an_empty_slot_of_the_requested_size_to_stdout(capfdbinary):
    assert P.main(["placeholder", "--size", "256"]) == 0
    out = capfdbinary.readouterr().out
    assert len(out) == 256 and P.is_empty(out)
