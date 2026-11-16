"""P5 update trust, verified with the REAL RAUC 1.13 host tool (signature + keyring only; installing needs a device):
  * official images accept only release-signed bundles, own images only their own, "official+own" both - built by post-build.sh;
  * a development-signed bundle is re-signed with the release key in a separate step (the release key is never needed to BUILD);
  * a certificate without the codeSigning purpose is refused (check-purpose=codesign)."""
import json
import os
import shutil

import pytest

from oshelpers import BOARD, make_ca, sh


def bundle(tmp, signer, name):
    d = tmp / f"in-{name}"; d.mkdir()
    (d / "rootfs.ext4").write_bytes(os.urandom(300_000))
    (d / "manifest.raucm").write_text("[update]\ncompatible=hifi-bridge-rpi3\nversion=0.3.0-dev\n\n[bundle]\nformat=verity\n\n"
                                      "[image.rootfs]\nfilename=rootfs.ext4\n")
    conf = sysconf(tmp, signer["ca"], f"mk-{name}")
    out = tmp / f"{name}.raucb"
    sh("rauc", f"--conf={conf}", "bundle", f"--cert={signer['cert']}", f"--key={signer['key']}", str(d), str(out))
    return out


def sysconf(tmp, keyring, name):
    p = tmp / f"{name}.conf"
    template = open(f"{BOARD}/rauc/system.conf.in").read().replace("@COMPATIBLE@", "hifi-bridge-rpi3")
    p.write_text(template.replace("bootloader=uboot", "bootloader=noop").replace("/etc/rauc/keyring.pem", str(keyring))
                 .replace("data-directory=/data/rauc", f"data-directory={tmp}/rauc-data"))
    return p


def accepts(tmp, keyring, b, name):
    return sh("rauc", f"--conf={sysconf(tmp, keyring, name)}", "info", str(b), check=False).returncode == 0


def post_build(tmp, mode, official=None, own=None):
    target = tmp / f"target-{mode}"; target.mkdir()
    cfg = tmp / f"config-{mode}"
    lines = ['BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"']
    if mode != "UNSET":
        lines.append(f"BR2_PACKAGE_HIFI_BRIDGE_UPDATE_TRUST_{mode}=y")
    if official:
        lines.append(f'BR2_PACKAGE_HIFI_BRIDGE_OFFICIAL_UPDATE_CA="{official}"')
    if own:
        lines.append(f'BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA="{own}"')
    cfg.write_text("\n".join(lines) + "\n")
    r = sh(f"{BOARD}/post-build.sh", str(target), env={"BR2_CONFIG": str(cfg)}, check=False)
    return r, target


def test_trust_matrix_with_real_rauc(tmp_path, tools):
    rel, dev = make_ca(tmp_path, "release"), make_ca(tmp_path, "dev")
    dev_bundle = bundle(tmp_path, dev, "dev")
    rel_bundle = tmp_path / "rel.raucb"                        # offline release step: re-sign the CI/dev-signed bundle
    sh("rauc", f"--conf={sysconf(tmp_path, dev['ca'], 'resign')}", "resign", f"--cert={rel['cert']}", f"--key={rel['key']}",
       f"--signing-keyring={rel['ca']}", str(dev_bundle), str(rel_bundle))
    keyrings = {}
    for mode, kw in (("OFFICIAL", {"official": rel["ca"]}), ("OWN", {"own": dev["ca"]}),
                     ("OFFICIAL_AND_OWN", {"official": rel["ca"], "own": dev["ca"]})):
        r, target = post_build(tmp_path, mode, **kw)
        assert r.returncode == 0, r.stderr
        keyrings[mode] = target / "etc/rauc/keyring.pem"
        info = json.loads((target / "etc/hifi-bridge/update-trust.json").read_text())
        assert info["mode"] == mode.lower().replace("_", "-") and len(info["certificates"]) == (2 if mode == "OFFICIAL_AND_OWN" else 1)
        assert "PRIVATE" not in keyrings[mode].read_text()
    result = {m: (accepts(tmp_path, k, rel_bundle, f"{m}-rel"), accepts(tmp_path, k, dev_bundle, f"{m}-dev")) for m, k in keyrings.items()}
    assert result == {"OFFICIAL": (True, False), "OWN": (False, True), "OFFICIAL_AND_OWN": (True, True)}, result


def test_signing_certificate_without_codesigning_purpose_is_refused(tmp_path, tools):
    mail = make_ca(tmp_path, "mail", purpose="emailProtection")
    dev = make_ca(tmp_path, "dev")
    b = bundle(tmp_path, dev, "ok")
    bad = tmp_path / "bad.raucb"
    r = sh("rauc", f"--conf={sysconf(tmp_path, dev['ca'], 'x')}", "resign", f"--cert={mail['cert']}", f"--key={mail['key']}",
           f"--signing-keyring={mail['ca']}", str(b), str(bad), check=False)
    assert r.returncode != 0 or not accepts(tmp_path, mail["ca"], bad, "mail")


@pytest.mark.parametrize("case,expect", [
    ("unset", "update trust is not configured"),
    ("missing", "file not found"),
    ("private_key", "contains a private key"),
    ("not_ca", "is not a CA certificate"),
    ("two_certs", "exactly one certificate"),
])
def test_post_build_refuses_unclear_trust(tmp_path, tools, case, expect):
    rel = make_ca(tmp_path, "release")
    leaf = make_ca(tmp_path, "leaf", ca=False)
    f = tmp_path / "ca-input.pem"
    if case == "unset":
        r, _ = post_build(tmp_path, "UNSET")
    else:
        content = {"missing": None, "private_key": open(rel["ca"]).read() + open(rel["ca_key"]).read(),
                   "not_ca": open(leaf["ca"]).read(), "two_certs": open(rel["ca"]).read() * 2}[case]
        if content is not None:
            f.write_text(content)
        r, _ = post_build(tmp_path, "OFFICIAL", official=str(f) if content is not None else str(tmp_path / "nope.pem"))
    assert r.returncode != 0 and expect in r.stderr, r.stderr


def test_post_build_writes_system_conf_fstab_and_units_idempotently(tmp_path, tools):
    rel = make_ca(tmp_path, "release")
    r, target = post_build(tmp_path, "OFFICIAL", official=rel["ca"])
    assert r.returncode == 0
    conf = (target / "etc/rauc/system.conf").read_text()
    assert "compatible=hifi-bridge-rpi3" in conf and "check-purpose=codesign" in conf and "bundle-formats=-plain" in conf
    cfg = tmp_path / "config-OFFICIAL"
    sh(f"{BOARD}/post-build.sh", str(target), env={"BR2_CONFIG": str(cfg)})          # second run: no duplicates
    fstab = (target / "etc/fstab").read_text().splitlines()
    assert len(fstab) == len(set(fstab)) == 4 and any(" /boot vfat " in x and "uid=990" in x and "fmask=0177" in x for x in fstab)
    assert (target / "etc/systemd/system/multi-user.target.wants/hifi-bridge-boot.service").is_symlink()
    assert (target / "etc/systemd/system/data.mount.wants/hifi-bridge-growdata.service").is_symlink()
    shutil.rmtree(target)
