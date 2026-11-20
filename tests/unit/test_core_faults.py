"""UNIT (real files, sockets and the real flac binary of the lab image): the foundations when something underneath them breaks - a full or
unwritable data partition, a configuration file that is not JSON, a reader that falls behind the audio ring by more than its capacity, an
encoder that died, a systemd that is not listening. Each must degrade as documented, not raise into the audio path. Coverage 7b-3."""
import json
import os
import shutil
import signal
import socket
import threading
import time

import pytest

from btbridge.config import store as store_mod
from btbridge.config.schema import SETTINGS, SettingError, coerce
from btbridge.config.store import ConfigStore
from btbridge.core.bounded import BoundedList
from btbridge.core.ring import PcmRing
from btbridge.stream.encoders import FlacEncoder
from btbridge.supervision import StopSignal, SystemdNotifier


# ---------------------------------------------------------------- configuration files
def test_a_failed_write_leaves_the_old_settings_and_no_temporary_file(tmp_path, monkeypatch):
    s = ConfigStore(str(tmp_path))
    s.update({"device_name": "Küche"})

    def disk_full(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store_mod.os, "replace", disk_full)
    with pytest.raises(OSError):
        s.update({"device_name": "Bad"})
    monkeypatch.undo()
    assert json.load(open(tmp_path / "settings.json"))["device_name"] == "Küche"        # the old file is intact
    assert [f for f in os.listdir(tmp_path) if ".tmp-" in f] == []                         # and nothing is left behind


def test_a_settings_file_that_is_valid_json_but_not_an_object_is_set_aside_and_defaults_apply(tmp_path):
    (tmp_path / "settings.json").write_text('["not", "an", "object"]')
    logs = []
    s = ConfigStore(str(tmp_path), log=logs.append)
    assert s.get("device_name") == SETTINGS["device_name"].default
    assert any(f.startswith("settings.json.corrupt-") for f in os.listdir(tmp_path))
    assert any("unreadable (ValueError)" in m for m in logs)


def test_a_corrupt_settings_file_that_cannot_be_moved_still_starts_with_defaults(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{broken")
    real = os.replace

    def refuse(src, dst):
        if os.path.basename(str(dst)).startswith("settings.json.corrupt-"):
            raise PermissionError("read-only")
        return real(src, dst)
    monkeypatch.setattr(store_mod.os, "replace", refuse)
    s = ConfigStore(str(tmp_path))
    assert s.get("device_name") == SETTINGS["device_name"].default


# ---------------------------------------------------------------- setting validation: what the owner can type
@pytest.mark.parametrize("key,value,code", [
    ("local_hostname", "-bad-", "invalid_hostname"),
    ("local_hostname", "has space", "invalid_hostname"),
    ("extra_hostnames", "ok.example, bad host!", "invalid_host"),
    ("instance_id", "x", "invalid_instance_id"),
    ("device_labels", "not a dict", "invalid_labels"),
    ("device_name", ["a", "list"], "invalid_str"),
    ("device_name", "n" * 300, "too_long"),
    ("mqtt_ca_pem", "no markers at all", "invalid_pem"),
    ("mqtt_ca_pem", "P" * 17000, "too_long"),
])
def test_values_the_owner_cannot_use_are_refused_with_a_code_never_a_python_message(key, value, code):
    with pytest.raises(SettingError) as e:
        coerce(SETTINGS[key], value)
    assert e.value.code == code


def test_valid_choices_are_normalised():
    assert coerce(SETTINGS["local_hostname"], "  Hifi-Bridge ") == "hifi-bridge"
    assert coerce(SETTINGS["instance_id"], "") == ""
    assert coerce(SETTINGS["mqtt_enabled"], "yes") is True and coerce(SETTINGS["mqtt_enabled"], "off") is False
    assert coerce(SETTINGS["device_labels"], {"aa:bb:cc:dd:ee:01": "  Anna   Meier ", "AA:BB:CC:DD:EE:02": "   "}) == {"AA:BB:CC:DD:EE:01": "Anna Meier"}


# ---------------------------------------------------------------- audio ring, bounded audit lists
def test_a_burst_larger_than_the_ring_keeps_only_the_newest_audio_and_counts_the_loss():
    ring = PcmRing(capacity_frames=100, frame_bytes=4)
    data = b"".join(i.to_bytes(4, "little") for i in range(350))          # frame i carries the value i
    assert ring.write(data) == 100                                         # only the newest 100 of 350 frames were kept
    assert ring.head == 350 and ring.oldest == 250
    pos, out, skipped = ring.read(0, 1000, timeout=0)
    assert skipped == 250 and pos == 350
    assert [int.from_bytes(out[i:i + 4], "little") for i in range(0, len(out), 4)] == list(range(250, 350))   # newest, in order


def test_closing_the_ring_wakes_a_blocked_reader_at_once():
    ring = PcmRing(10)
    result = {}

    def reader():
        t0 = time.monotonic()
        result["got"] = ring.read(0, 5, timeout=10)
        result["waited"] = time.monotonic() - t0
    t = threading.Thread(target=reader); t.start()
    time.sleep(0.2)
    ring.close()
    t.join(3)
    assert not t.is_alive() and result["got"] == (0, b"", 0) and result["waited"] < 2 and ring.closed


def test_bounded_lists_trim_on_every_way_in():
    events = BoundedList(8)
    events.extend(range(20))
    assert len(events) <= 8 + 2 and events[-1] == 19                       # trimmed in chunks, newest kept
    events += list(range(100, 130))
    assert len(events) <= 8 + 2 and events[-1] == 129


# ---------------------------------------------------------------- encoder child, systemd, signals
@pytest.mark.skipif(not shutil.which("flac"), reason="flac not installed in this image (the lab image and the device have it)")
def test_an_encoder_whose_child_died_returns_silence_instead_of_raising():
    enc = FlacEncoder()
    enc.proc.kill(); enc.proc.wait()
    for _ in range(3):
        assert enc.encode(b"\0" * 65536) == b""                            # the pipe is broken: silence, no BrokenPipeError
    enc.close()
    enc.close()                                                            # closing twice (kill, wait, close pipes) is harmless


def test_systemd_that_does_not_listen_is_not_an_error(tmp_path):
    n = SystemdNotifier({"NOTIFY_SOCKET": str(tmp_path / "nobody-listens")})
    assert n.enabled and n.notify(ready=1) is False
    assert SystemdNotifier({}).notify(ready=1) is False and not SystemdNotifier({}).enabled
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); srv.bind(str(tmp_path / "notify")); srv.settimeout(2)
    try:
        assert SystemdNotifier({"NOTIFY_SOCKET": str(tmp_path / "notify")}).notify(ready=1, status="up") is True
        assert srv.recv(100) == b"READY=1\nSTATUS=up"
    finally:
        srv.close()


def test_stop_signal_is_a_flag_set_by_the_handler_and_polled_by_wait():
    old = signal.getsignal(signal.SIGUSR2)
    try:
        stop = StopSignal(signals=(signal.SIGUSR2,))
        threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGUSR2)).start()
        t0 = time.monotonic()
        stop.wait(poll=0.05)
        assert stop.requested and 0.2 < time.monotonic() - t0 < 3
    finally:
        signal.signal(signal.SIGUSR2, old)
