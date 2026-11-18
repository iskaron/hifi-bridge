"""os/tools/secret_scan.py (UNIT, generated fixtures only - no key or token is committed): every rule fires on a planted secret in a file
tree or anywhere in a raw image (also across a chunk border and in unallocated space); the harmless look-alikes that a real rootfs
contains (PEM header constants inside binaries, CA certificates, locked accounts) are not findings; values are never printed."""
import json
import os
import subprocess
import sys

import pytest

from oshelpers import REPO, sh

TOOL = os.path.join(REPO, "os", "tools", "secret_scan.py")
sys.path.insert(0, os.path.dirname(TOOL))
import secret_scan as S  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, TOOL, *map(str, args)], capture_output=True, text=True)


def pem_key(tmp):
    k = tmp / "gen.key"
    sh("openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(k))
    data = k.read_bytes()
    k.unlink()
    return data


def cert(tmp):
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(tmp / "c.k"),
       "-out", str(tmp / "c.pem"), "-days", "2", "-subj", "/CN=test")
    (tmp / "c.k").unlink()
    return (tmp / "c.pem").read_bytes()


def clean_tree(tmp):
    r = tmp / "root"
    for d in ("etc/ssl/certs", "usr/lib", "usr/bin", "etc/rauc"):
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / "etc/shadow").write_text("root:*:::::::\nhifi-bridge:!:::::::\ndaemon:*:10933:0:99999:7:::\n")
    (r / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\nhifi-bridge:x:990:990::/var/lib/hifi-bridge:/bin/false\n")
    (r / "etc/rauc/keyring.pem").write_bytes(cert(tmp))
    # libcrypto/Python carry the PEM header as a format constant followed by binary data, not by key material
    (r / "usr/lib/libcrypto.so.3").write_bytes(b"\x7fELF\0\0" + b"-----BEGIN PRIVATE KEY-----\0-----END PRIVATE KEY-----\0" * 3)
    (r / "usr/bin/tool").write_bytes(b"\0\1usage: psk=<passphrase>\0")
    (r / "etc/wpa_supplicant.conf").write_text("ctrl_interface=/run/wpa_supplicant\n# psk=\n")
    return r


def findings(tmp):
    return json.loads((tmp / "rep.json").read_text())["findings"]


def test_clean_tree_has_no_findings(tmp_path, tools):
    r = run("--tree", clean_tree(tmp_path), "--json", tmp_path / "rep.json")
    assert r.returncode == 0, r.stdout
    rep = json.loads((tmp_path / "rep.json").read_text())
    assert rep["findings"] == [] and rep["scanned"][0]["files"] == 6


@pytest.mark.parametrize("rel,content,rule", [
    ("etc/x/device.pem", "KEY", "private-key-pem"),
    ("usr/share/blob.bin", b"\0\0junk" + b"KEYBYTES" + b"\0", "private-key-pem"),
    ("etc/hifi-bridge/token", "ghp_" + "A1b2C3d4" * 4 + "Zz9Y", "github-token"),
    ("etc/hifi-bridge/t2", "x=github_pat_" + "11ABCDEFG0" * 7 + "\n", "github-token"),
    ("etc/ci", "glpat-" + "x" * 20, "gitlab-token"),
    ("etc/aws", "key=AKIA" + "ABCDEFGHIJKLMNOP", "aws-access-key"),
    ("etc/curlrc", "header = \"Authorization: Bearer " + "abc.DEF-123_" * 3 + "\"", "bearer-token"),
    ("etc/wpa_supplicant/wpa_supplicant-wlan0.conf", "network={\n ssid=\"home\"\n psk=\"correct-horse-battery\"\n}\n", "wifi-psk"),
    ("etc/hostapd.conf", "interface=wlan0\nwpa_passphrase=SetupPass1234\n", "wifi-psk"),
])
def test_planted_secret_is_found(tmp_path, tools, rel, content, rule):
    r = clean_tree(tmp_path)
    key = pem_key(tmp_path)
    if content == "KEY":
        content = key
    elif isinstance(content, bytes):
        content = content.replace(b"KEYBYTES", key)
    data = content if isinstance(content, bytes) else content.encode()
    p = r / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    res = run("--tree", r, "--json", tmp_path / "rep.json")
    assert res.returncode == 1
    assert any(f["rule"] == rule and f["path"] == "/" + rel for f in findings(tmp_path)), findings(tmp_path)
    tail = data.replace(b"\0", b"").strip()[-12:].decode("latin-1")
    assert tail not in res.stdout                                  # the value itself is never printed


@pytest.mark.parametrize("shadow,rule", [
    ("root:$6$salt$" + "h" * 86 + ":19000:0:99999:7:::\n", "login-password-hash"),
    ("root::19000:0:99999:7:::\n", "login-empty-password"),
])
def test_login_credentials_in_shadow(tmp_path, tools, shadow, rule):
    r = clean_tree(tmp_path)
    (r / "etc/shadow").write_text(shadow)
    res = run("--tree", r, "--json", tmp_path / "rep.json")
    assert res.returncode == 1 and [f["rule"] for f in findings(tmp_path)] == [rule]


def test_password_field_in_passwd_and_suspicious_names(tmp_path, tools):
    r = clean_tree(tmp_path)
    (r / "etc/passwd").write_text("root:abc123hash:0:0::/root:/bin/sh\n")
    for f in ("etc/ssh/ssh_host_ed25519_key", "root/.ssh/authorized_keys", "home/u/.netrc", "opt/x/server.key"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("not a key\n")
    run("--tree", r, "--json", tmp_path / "rep.json")
    assert sorted(f["rule"] for f in findings(tmp_path)) == [
        "credential-file", "key-file", "login-password-in-passwd", "ssh-authorized-keys", "ssh-host-key"]


def test_allowance_is_reported_not_hidden(tmp_path, tools):
    r = clean_tree(tmp_path)
    (r / "opt/x").mkdir(parents=True)
    (r / "opt/x/public.key").write_text("public\n")
    res = run("--tree", r, "--allow", "key-file:/opt/x/*.key", "--json", tmp_path / "rep.json")
    rep = json.loads((tmp_path / "rep.json").read_text())
    assert res.returncode == 0 and rep["findings"] == [] and rep["allowed"][0]["path"] == "/opt/x/public.key"


def test_raw_image_finds_residue_in_unallocated_space_and_across_chunk_borders(tmp_path, tools):
    key = pem_key(tmp_path)
    img = tmp_path / "card.img"
    border = S.CHUNK - 100                         # the key straddles the first chunk border
    with open(img, "wb") as fh:
        fh.truncate(3 * S.CHUNK)
        fh.seek(border)
        fh.write(key)
        fh.seek(2 * S.CHUNK + 12345)
        fh.write(b"ghp_" + b"Q" * 36)
    res = run("--raw", img, "--json", tmp_path / "rep.json")
    assert res.returncode == 1
    assert sorted((f["rule"], f["where"].split()[1]) for f in findings(tmp_path)) == [
        ("github-token", str(2 * S.CHUNK + 12345)), ("private-key-pem", str(border))]


def test_raw_scan_of_a_real_ext4_image_finds_deleted_residue(tmp_path, tools):
    """A key written to an ext4 image and then DELETED is still found by the raw scan, but not by a tree scan."""
    root = tmp_path / "fs"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "k.pem").write_bytes(pem_key(tmp_path))
    img = tmp_path / "fs.ext4"
    sh("mke2fs", "-q", "-t", "ext4", "-d", str(root), str(img), "8M")
    sh("debugfs", "-w", "-R", "rm /etc/k.pem", str(img))
    (root / "etc" / "k.pem").unlink()
    assert run("--tree", root).returncode == 0
    res = run("--raw", img)
    assert res.returncode == 1 and "private-key-pem" in res.stdout
