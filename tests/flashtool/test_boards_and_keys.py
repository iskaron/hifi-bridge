"""UNIT: board table and the trusted publisher key store (official keys shipped with the tool, custom keys only after explicit addition)."""
import os
import stat

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from hififlash import boards as B
from hififlash import keystore as K
from hififlash import manifest as M


def pem(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def test_boards_match_the_buildroot_defconfigs():
    assert [b.id for b in B.BOARDS] == ["rpizero2w", "rpi3", "rpi4"]
    root = os.path.join(os.path.dirname(__file__), "..", "..", "os", "br2-external", "configs")
    for b in B.BOARDS:
        cfg = open(os.path.join(root, f"hifi_bridge_{b.id}_64_defconfig")).read()
        assert f'BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="{b.compatible}"' in cfg
    assert B.by_id("rpi3").name == "Raspberry Pi 3 Model B+" and B.by_compatible("hifi-bridge-rpi4").id == "rpi4"
    assert B.by_id("nope") is None and B.by_compatible("x") is None


def test_official_and_custom_keys(tmp_path):
    official_dir, custom_dir = tmp_path / "official", tmp_path / "custom"
    official_dir.mkdir()
    off = ec.generate_private_key(ec.SECP256R1())
    (official_dir / "hifi-bridge-2026.pem").write_bytes(pem(off))
    (official_dir / "README.txt").write_text("not a key")
    store = K.KeyStore(str(custom_dir), official_dir=str(official_dir))
    keys = store.keys()
    assert [(k.name, k.kind) for k in keys] == [("hifi-bridge-2026", "official")]
    assert len(keys[0].fingerprint) == 64 + 31 and keys[0].fingerprint.count(":") == 31

    mine = ec.generate_private_key(ec.SECP256R1())
    added = store.add_custom("My build server", pem(mine))
    assert added.kind == "custom" and added.name == "My build server"
    assert stat.S_IMODE(os.stat(custom_dir).st_mode) == 0o700
    assert {k.kind for k in store.keys()} == {"official", "custom"}
    # signatures resolve to the key and its kind
    raw = b'{"x":1}'
    sig = mine.sign(raw, ec.ECDSA(hashes.SHA256()))
    who = store.verify(raw, sig)
    assert (who.name, who.kind) == ("My build server", "custom")
    sig_off = off.sign(raw, ec.ECDSA(hashes.SHA256()))
    assert store.verify(raw, sig_off).kind == "official"
    with pytest.raises(M.ManifestError, match="not signed by a trusted"):
        store.verify(raw, ec.generate_private_key(ec.SECP256R1()).sign(raw, ec.ECDSA(hashes.SHA256())))
    # the same key twice, a duplicate name, an official key as custom, wrong algorithms and garbage are refused
    with pytest.raises(K.KeyStoreError, match="already trusted"):
        store.add_custom("again", pem(mine))
    with pytest.raises(K.KeyStoreError, match="already trusted"):
        store.add_custom("official copy", pem(off))
    with pytest.raises(K.KeyStoreError, match="name"):
        store.add_custom("My build server", pem(ec.generate_private_key(ec.SECP256R1())))
    with pytest.raises(K.KeyStoreError, match="P-256"):
        store.add_custom("rsa", pem(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    with pytest.raises(K.KeyStoreError, match="P-256"):
        store.add_custom("p384", pem(ec.generate_private_key(ec.SECP384R1())))
    with pytest.raises(K.KeyStoreError, match="PEM"):
        store.add_custom("junk", b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n")
    for bad in ("", "x" * 65, "../evil", "a/b", "line\nbreak"):
        with pytest.raises(K.KeyStoreError, match="name"):
            store.add_custom(bad, pem(ec.generate_private_key(ec.SECP256R1())))
    # removing: only custom keys
    store.remove_custom("My build server")
    assert [k.kind for k in store.keys()] == ["official"]
    with pytest.raises(K.KeyStoreError, match="custom"):
        store.remove_custom("hifi-bridge-2026")


def test_a_manipulated_custom_key_file_is_ignored_not_trusted(tmp_path):
    store = K.KeyStore(str(tmp_path / "c"), official_dir=str(tmp_path / "missing"))
    assert store.keys() == []                                         # no official keys shipped yet: nothing is trusted
    store.add_custom("k", pem(ec.generate_private_key(ec.SECP256R1())))
    (tmp_path / "c" / "k.pem").write_bytes(b"garbage")
    (tmp_path / "c" / "notes.txt").write_text("x")
    assert store.keys() == [] and store.problems() == ["k.pem"]


def test_shipped_official_key_directory_exists_and_contains_only_valid_keys():
    keys = K.KeyStore("/nonexistent-custom").keys()
    assert all(k.kind == "official" for k in keys)
    assert os.path.isfile(os.path.join(K.OFFICIAL_DIR, "README.md"))
