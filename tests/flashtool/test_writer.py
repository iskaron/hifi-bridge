"""UNIT: write/verify/provision against SIMULATED cards (regular files) - cancel, I/O error, fake-capacity card, tampered image, swapped
card; every started-but-failed write leaves the card unbootable. FileTarget refuses device nodes outright."""
import gzip
import hashlib
import io
import lzma
import os
import threading

import pytest

from btbridge import provisioning as P
from hififlash import provision as FP, writer as W

MB = 1 << 20


def image_and_manifest(tmp_path, size=10 * MB, slot_off=2 * MB):
    data = bytearray(os.urandom(size))
    data[slot_off:slot_off + P.SLOT_SIZE] = P.placeholder()
    img = tmp_path / "x.img"; img.write_bytes(bytes(data))
    man = {"size": size, "sha256": hashlib.sha256(data).hexdigest(), "slot": {"file": P.FILE_NAME, "offset": slot_off, "size": P.SLOT_SIZE}}
    return img, man, bytes(data)


def card(tmp_path, size=32 * MB, **kw):
    p = tmp_path / "card.bin"
    with open(p, "wb") as fh:
        fh.write(b"\x55" * size)                               # previous content
    return W.FileTarget(str(p), **kw), p


def doc():
    return P.render(FP.new_document("DE")[0])


def test_full_write_verify_and_provision(tmp_path):
    img, man, data = image_and_manifest(tmp_path)
    t, p = card(tmp_path)
    progress = []
    prov = doc()
    r = W.write_card(open(img, "rb"), man, t, t.identity(), prov, progress=lambda s, a, b: progress.append(s))
    t.close()
    raw = p.read_bytes()
    assert r == {"result": "ok", "bytes": man["size"], "sha256": man["sha256"], "provisioned": True}
    assert raw[man["slot"]["offset"]:man["slot"]["offset"] + P.SLOT_SIZE] == prov
    assert raw[:man["slot"]["offset"]] == data[:man["slot"]["offset"]] and {"write", "verify"} <= set(progress)


@pytest.mark.parametrize("compress", [lzma.compress, gzip.compress])
def test_compressed_images_stream(tmp_path, compress):
    img, man, data = image_and_manifest(tmp_path)
    c = tmp_path / "x.img.z"; c.write_bytes(compress(data))
    t, _ = card(tmp_path)
    assert W.write_card(W.open_image(str(c)), man, t, t.identity(), None)["provisioned"] is False


def test_cancel_makes_the_card_unbootable(tmp_path):
    img, man, _ = image_and_manifest(tmp_path)
    t, p = card(tmp_path)
    cancel = threading.Event()
    with pytest.raises(W.Cancelled, match="made unbootable"):
        W.write_card(open(img, "rb"), man, t, t.identity(), doc(), cancel=cancel,
                     progress=lambda s, a, b: cancel.set() if a >= 4 * MB else None)
    t.close()
    assert p.read_bytes()[:W.INVALIDATE_BYTES] == bytes(W.INVALIDATE_BYTES)


@pytest.mark.parametrize("case,message", [("io", "simulated I/O error"), ("fake", "read-back verification failed"),
                                          ("tampered", "does not match its signed manifest")])
def test_failures_invalidate_the_card(tmp_path, case, message):
    img, man, _ = image_and_manifest(tmp_path)
    kw = {"io": {"fail_after": 5 * MB}, "fake": {"lie_above": 6 * MB}, "tampered": {}}[case]
    if case == "tampered":
        man = {**man, "sha256": "0" * 64}
    t, p = card(tmp_path, **kw)
    with pytest.raises(W.WriteError, match=message):
        W.write_card(open(img, "rb"), man, t, t.identity(), doc())
    t.close()
    assert p.read_bytes()[:W.INVALIDATE_BYTES] == bytes(W.INVALIDATE_BYTES)


def test_refusals_before_writing_leave_the_card_untouched(tmp_path):
    img, man, _ = image_and_manifest(tmp_path)
    t, p = card(tmp_path)
    before = p.read_bytes()
    with pytest.raises(W.WriteError, match="no longer the same device"):
        W.write_card(open(img, "rb"), man, t, ("/dev/sdb", 1, "other"), doc())
    with pytest.raises(W.WriteError, match="larger than the card"):
        W.write_card(open(img, "rb"), {**man, "size": 64 * MB}, t, t.identity(), doc())
    with pytest.raises(W.WriteError, match="slot size"):
        W.write_card(open(img, "rb"), man, t, t.identity(), b"short")
    assert p.read_bytes() == before                                       # nothing was written by any refusal above
    with pytest.raises(W.WriteError, match="longer than the manifest"):
        W.write_card(io.BytesIO(open(img, "rb").read() + b"x" * W.CHUNK), man, t, t.identity(), None)
    t.close()


def test_a_card_that_cannot_even_be_invalidated_is_reported_as_unusable(tmp_path):
    img, man, _ = image_and_manifest(tmp_path)
    t, _ = card(tmp_path, fail_after=5 * MB, fail_always=True)
    with pytest.raises(W.WriteError, match="could NOT be made unbootable"):
        W.write_card(open(img, "rb"), man, t, t.identity(), doc())


def test_file_target_refuses_device_nodes_and_missing_files(tmp_path):
    for path in ("/dev/sda", "/dev/mmcblk0", "/proc/self/mem", "\\\\.\\PhysicalDrive0", str(tmp_path / "missing")):
        with pytest.raises(W.WriteError):
            W.FileTarget(path)
    os.symlink("/dev/null", tmp_path / "link")
    with pytest.raises(W.WriteError):
        W.FileTarget(str(tmp_path / "link"))                    # a symlink to a device is resolved and refused


def test_cancel_during_verification_and_the_target_interface(tmp_path):
    img, man, _ = image_and_manifest(tmp_path)
    t, p = card(tmp_path)
    cancel = threading.Event()
    with pytest.raises(W.Cancelled, match="while verifying"):
        W.write_card(open(img, "rb"), man, t, t.identity(), doc(), cancel=cancel,
                     progress=lambda s, a, b: cancel.set() if s == "verify" else None)
    t.close()
    assert p.read_bytes()[:W.INVALIDATE_BYTES] == bytes(W.INVALIDATE_BYTES)
    base = W.Target()
    for call in (base.identity, lambda: base.write_at(0, b""), lambda: base.read_at(0, 1), base.flush):
        with pytest.raises(NotImplementedError):
            call()
    base.close()
    t2, _ = card(tmp_path)
    with pytest.raises(W.WriteError, match="beyond the end"):
        t2.write_at(t2.size - 1, b"xx")
