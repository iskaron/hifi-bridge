"""UNIT: manifest signature (signed with the openssl CLI, verified with python-cryptography - two implementations), manifest validation,
and the FAT slot locator on REAL FAT12/16/32 filesystems made by mkfs.vfat + mtools (incl. long names and a fragmented file)."""
import json
import os
import struct
import subprocess

import pytest

from btbridge import provisioning as P
from oshelpers import sh
from hififlash import manifest as M
from hififlash.fatslot import SlotError, file_reader, find_file

MAN = {"format": M.FORMAT, "compatible": "hifi-bridge-rpi3", "version": "0.3.0", "image": "x.img", "size": 100 * 1024 ** 2,
       "sha256": "a" * 64, "slot": {"file": P.FILE_NAME, "offset": 4 * 1024 ** 2, "size": P.SLOT_SIZE}}


def keypair(tmp, name, kind="ec"):
    key, pub = tmp / f"{name}.key", tmp / f"{name}.pub"
    args = ["-pkeyopt", "ec_paramgen_curve:P-256"] if kind == "ec" else ["-pkeyopt", "rsa_keygen_bits:2048"]
    sh("openssl", "genpkey", "-algorithm", "EC" if kind == "ec" else "RSA", *args, "-out", str(key))
    sh("openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub))
    return key, pub


def sign(tmp, key, raw):
    f = tmp / "m.json"; f.write_bytes(raw)
    sh("openssl", "dgst", "-sha256", "-sign", str(key), "-out", str(tmp / "m.sig"), str(f))
    return (tmp / "m.sig").read_bytes()


def test_signature_by_a_trusted_key_only(tmp_path):
    official, _ = keypair(tmp_path, "official")
    other, _ = keypair(tmp_path, "other")
    raw = M.dumps(MAN)
    trusted = {"official": (tmp_path / "official.pub").read_bytes()}
    assert M.verify_signature(raw, sign(tmp_path, official, raw), trusted) == "official"
    with pytest.raises(M.ManifestError, match="not signed by a trusted"):
        M.verify_signature(raw, sign(tmp_path, other, raw), trusted)
    with pytest.raises(M.ManifestError):
        M.verify_signature(raw.replace(b"0.3.0", b"9.9.9"), sign(tmp_path, official, raw), trusted)   # modified after signing
    _, rsa_pub = keypair(tmp_path, "rsa", "rsa")
    with pytest.raises(M.ManifestError, match="only ECDSA P-256"):
        M.verify_signature(raw, b"x", {"rsa": rsa_pub.read_bytes()})


@pytest.mark.parametrize("change", [{"format": "x"}, {"size": "1"}, {"sha256": "ab"}, {"slot": {"offset": 0, "size": P.SLOT_SIZE}},
                                    {"slot": {"offset": 10, "size": 100}}, {"slot": {"offset": 100 * 1024 ** 2, "size": P.SLOT_SIZE}}])
def test_manifest_validation(change):
    with pytest.raises(M.ManifestError):
        M.load(json.dumps({**MAN, **change}).encode())
    assert M.load(M.dumps(MAN)) == MAN
    with pytest.raises(M.ManifestError):
        M.load(b"not json")
    for not_an_object in (b"null", b"[1]", b'"x"'):                    # valid JSON, but not a manifest (was an AttributeError)
        with pytest.raises(M.ManifestError, match="not a JSON object"):
            M.load(not_an_object)


def fat_image(tmp, fat_bits, size_mb):
    img = tmp / f"fat{fat_bits}.img"
    with open(img, "wb") as fh:
        fh.truncate((size_mb + 1) * 1024 ** 2)
    subprocess.run(["sfdisk", "-q", str(img)], input="label: dos\n2048,,c\n", text=True, check=True)
    part = tmp / "p.img"
    sh("mkfs.vfat", "-F", str(fat_bits), "-C", str(part), str(size_mb * 1024 - 1024))
    for n in ("config.txt", "start.elf"):
        (tmp / n).write_bytes(os.urandom(3000))
        sh("mcopy", "-i", str(part), str(tmp / n), f"::{n}")
    (tmp / P.FILE_NAME).write_bytes(P.placeholder())
    sh("mcopy", "-i", str(part), str(tmp / P.FILE_NAME), f"::{P.FILE_NAME}")
    with open(img, "r+b") as fh, open(part, "rb") as src:
        fh.seek(2048 * 512); fh.write(src.read())
    return img


@pytest.mark.parametrize("bits,mb", [(12, 8), (16, 64), (32, 300)])
def test_slot_locator_on_real_fat(tmp_path, bits, mb):
    img = fat_image(tmp_path, bits, mb)
    off, size = find_file(file_reader(str(img)), P.FILE_NAME)
    assert size == P.SLOT_SIZE
    with open(img, "rb") as fh:
        fh.seek(off); assert P.is_empty(fh.read(size))
    with open(img, "r+b") as fh:                                   # write in place, read back through mtools (the FAT view)
        fh.seek(off); fh.write(P.render({"format": P.FORMAT, "setup_code": "ABCD-EFGH-JKMN"}))
    got = sh("mtype", "-i", f"{img}@@{2048 * 512}", f"::{P.FILE_NAME}").stdout.encode()
    assert P.parse(got)["setup_code"] == "ABCD-EFGH-JKMN"
    assert find_file(file_reader(str(img)), "START.ELF")[1] == 3000            # 8.3 name lookup works too
    with pytest.raises(SlotError, match="not found"):
        find_file(file_reader(str(img)), "nope.txt")


def test_fragmented_or_missing_mbr_is_refused(tmp_path):
    img = fat_image(tmp_path, 16, 64)
    read = file_reader(str(img))
    off, _ = find_file(read, P.FILE_NAME)
    bs = read(2048 * 512, 512)
    bps, spc, reserved = struct.unpack_from("<H", bs, 11)[0], bs[13], struct.unpack_from("<H", bs, 14)[0]
    fatsz, root_entries, nfats = struct.unpack_from("<H", bs, 22)[0], struct.unpack_from("<H", bs, 17)[0], bs[16]
    data_start = reserved + nfats * fatsz + (root_entries * 32 + bps - 1) // bps
    first = (off - 2048 * 512) // bps
    first = (first - data_start) // spc + 2
    with open(img, "r+b") as fh:                                   # point the chain of a multi-cluster slot elsewhere
        fh.seek(2048 * 512 + reserved * bps + 2 * first); fh.write(struct.pack("<H", first + 7))
    if P.SLOT_SIZE > spc * bps:
        with pytest.raises(SlotError, match="fragmented"):
            find_file(file_reader(str(img)), P.FILE_NAME)
    blank = tmp_path / "blank.img"; blank.write_bytes(bytes(1024))
    with pytest.raises(SlotError, match="MBR"):
        find_file(file_reader(str(blank)), P.FILE_NAME)


def test_build_manifest_from_an_image_and_the_cli(tmp_path, capsys):
    img = fat_image(tmp_path, 16, 64)
    m = M.build(str(img), "hifi-bridge-rpi3", "0.3.0")
    assert m["slot"]["size"] == P.SLOT_SIZE and m["size"] == os.path.getsize(img) and M.load(M.dumps(m)) == m
    out = tmp_path / "m.json"
    assert M.main(["build", "--image", str(img), "--compatible", "c", "--version", "1", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["compatible"] == "c"
    small = tmp_path / "small"; small.mkdir()
    img2 = fat_image(small, 16, 64)
    sh("mcopy", "-o", "-i", f"{img2}@@{2048 * 512}", str(tmp_path / "config.txt"), f"::{P.FILE_NAME}")   # wrong slot size
    with pytest.raises(M.ManifestError, match="slot has"):
        M.build(str(img2), "c", "1")
    assert M.main(["build", "--image", str(img2), "--compatible", "c", "--version", "1", "--out", str(out)]) == 1
