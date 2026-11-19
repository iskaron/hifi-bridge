"""Build identity written by post-build.sh (UNIT, fake target tree; the real builds are checked by scripts/os_check_image.sh):
development images are marked as such in os-release and build-info.json; a 'release' variant is refused unless the trust is
official-only; unknown variants are refused."""
import json

from oshelpers import BOARD, make_ca, sh


def run(tmp, variant, mode="OWN", **ca):
    target = tmp / f"target-{variant}-{mode}"
    (target / "usr" / "lib").mkdir(parents=True)
    (target / "etc").mkdir()
    (target / "usr" / "lib" / "os-release").write_text('NAME=Buildroot\nVERSION=2026.02.3\nID=buildroot\n')
    lines = ['BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"', f"BR2_PACKAGE_HIFI_BRIDGE_UPDATE_TRUST_{mode}=y"]
    if variant is not None:
        lines.append(f'BR2_PACKAGE_HIFI_BRIDGE_BUILD_VARIANT="{variant}"')
    for role, path in ca.items():
        lines.append(f'BR2_PACKAGE_HIFI_BRIDGE_{role.upper()}_UPDATE_CA="{path}"')
    cfg = tmp / f"config-{variant}-{mode}"
    cfg.write_text("\n".join(lines) + "\n")
    r = sh(f"{BOARD}/post-build.sh", str(target), env={"BR2_CONFIG": str(cfg), "SOURCE_DATE_EPOCH": "1770000000"}, check=False)
    return r, target


def osrel(target):
    return dict(line.split("=", 1) for line in (target / "usr" / "lib" / "os-release").read_text().splitlines() if "=" in line)


def test_development_build_is_marked_everywhere(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, t = run(tmp_path, "development", own=dev["ca"])
    assert r.returncode == 0, r.stderr
    o = osrel(t)
    assert o["VARIANT_ID"] == "development" and "not hardware-validated" in o["PRETTY_NAME"]
    assert o["ID"] == "hifi-bridge" and o["IMAGE_ID"] == "hifi-bridge-rpi3" and o["BUILDROOT_VERSION"] == "2026.02.3"
    assert (t / "etc" / "os-release").is_symlink()
    info = json.loads((t / "etc" / "hifi-bridge" / "build-info.json").read_text())
    assert info["variant"] == "development" and info["hardware_validated"] is False and info["update_trust"] == "own"
    assert info["source_date_epoch"] == 1770000000 and info["compatible"] == "hifi-bridge-rpi3"
    # stock units the product does not use are masked (found by the first real image check)
    for u in ("wpa_supplicant.service", "avahi-dnsconfd.service", "rauc-mark-good.service"):
        assert (t / "etc/systemd/system" / u).is_symlink() and str((t / "etc/systemd/system" / u).readlink()) == "/dev/null"


def test_missing_variant_defaults_to_development(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, t = run(tmp_path, None, own=dev["ca"])
    assert r.returncode == 0, r.stderr
    assert osrel(t)["VARIANT_ID"] == "development"


def test_release_variant_requires_official_only_trust(tmp_path, tools):
    dev, rel = make_ca(tmp_path, "dev"), make_ca(tmp_path, "release")
    r, _ = run(tmp_path, "release", own=dev["ca"])
    assert r.returncode != 0 and "requires update trust 'official releases only'" in r.stderr
    r, _ = run(tmp_path, "release", "OFFICIAL_AND_OWN", official=rel["ca"], own=dev["ca"])
    assert r.returncode != 0
    r, t = run(tmp_path, "release", "OFFICIAL", official=rel["ca"])
    assert r.returncode == 0, r.stderr
    assert osrel(t)["VARIANT_ID"] == "release" and "development" not in osrel(t)["PRETTY_NAME"]


def test_unknown_variant_is_refused(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, _ = run(tmp_path, "nightly", own=dev["ca"])
    assert r.returncode != 0 and "must be 'development' or 'release'" in r.stderr


def test_update_trust_json_stays_valid_with_commas_and_quotes_in_the_ca_subject(tmp_path, tools):
    """Found by the first real image check: RFC 2253 renders a comma in a name as a backslash escape, which broke the JSON."""
    d = tmp_path / "ca"
    d.mkdir()
    key, pem = d / "ca.key", d / "ca.pem"
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(key), "-out", str(pem),
       "-days", "2", "-subj", '/O=Dev "Lab"/OU=temporary, not for release/CN=dev CA', "-addext", "basicConstraints=critical,CA:TRUE")
    r, t = run(tmp_path, "development", own=str(pem))
    assert r.returncode == 0, r.stderr
    trust = json.loads((t / "etc" / "hifi-bridge" / "update-trust.json").read_text())
    subj = trust["certificates"][0]["subject"]
    assert "OU=temporary\\, not for release" in subj and 'O=Dev \\"Lab\\"' in subj, subj
