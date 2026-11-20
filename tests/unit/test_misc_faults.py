"""UNIT (real files): small but safety-relevant edges - a trust-proof answer that is not even well-formed, a /proc that is incomplete, the
network agent's private config file when the disk refuses the final step. Coverage package 7b-3."""
import base64
import os
import secrets

import pytest

from btbridge import trustproof as T
from btbridge.net import netconfig as NC
from btbridge.system import SystemMonitor


@pytest.mark.parametrize("mutate", [
    lambda d: {k: v for k, v in d.items() if k != "ca_der"},                      # no certificate at all
    lambda d: {k: v for k, v in d.items() if k != "mac"},                         # no proof
    lambda d: {**d, "ca_der": "!!! not base64 !!!"},
    lambda d: {**d, "ca_der": ["a", "list"]},
    lambda d: {**d, "format": "something-else"},
])
def test_a_trust_proof_that_is_not_well_formed_never_yields_a_certificate(mutate):
    key, nonce = secrets.token_bytes(32), secrets.token_hex(16)
    doc = T.answer(key, nonce, b"\x30\x03\x02\x01\x01")
    with pytest.raises(T.TrustProofError):
        T.verify(key, nonce, mutate(doc))
    with pytest.raises(T.TrustProofError):
        T.verify(key, nonce, "not even a dictionary")
    assert base64.b64decode(doc["ca_der"]) == b"\x30\x03\x02\x01\x01"             # (the unmodified answer is fine)


def test_an_incomplete_proc_gives_missing_values_not_an_exception(tmp_path):
    proc = tmp_path / "proc"; (proc / "net").mkdir(parents=True); (proc / "self").mkdir()
    (proc / "meminfo").write_text("Buffers: 100 kB\nnot a pair\nMemFree: 5 kB\n")   # a kernel without MemTotal (container quirks)
    (proc / "net" / "dev").write_text("h\nh\nno colon on this line\n  eth0: 1 2 3\n")         # too few columns
    (proc / "net" / "wireless").write_text("h\nh\nno colon here\n wlan0: 0000 70. -40.\n")
    (proc / "stat").write_text("cpu  1 0 1 8 0 0 0 0 0 0\n")
    thermal = tmp_path / "temp"; thermal.write_text("not a number")
    s = SystemMonitor(proc=str(proc), thermal=str(thermal), data_dir=str(tmp_path)).sample()
    assert s["memory"] is None and s["temperature_c"] is None and s["network"] == {"wlan0": {"wifi_signal_dbm": -40.0}}
    assert s["load"] is None and s["uptime_s"] is None


def test_writing_the_agents_private_config_cleans_up_when_the_disk_refuses_the_last_step(tmp_path, monkeypatch):
    cfg = NC.validate({}) if hasattr(NC, "validate") else {}
    path = str(tmp_path / "network.json")
    NC.write_private(path, cfg)
    before = open(path, "rb").read()

    def refuse(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(NC.os, "replace", refuse)
    with pytest.raises(OSError):
        NC.write_private(path, dict(cfg))
    monkeypatch.undo()
    assert open(path, "rb").read() == before                                       # the previous file is intact
    assert [f for f in os.listdir(tmp_path) if ".tmp-" in f] == []                  # and no temporary file is left behind
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
