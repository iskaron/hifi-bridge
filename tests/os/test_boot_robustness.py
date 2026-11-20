"""Boot robustness and boot-partition permissions (review reports/REVIEW_PHASE2B_NEW_CODE.md, package 7b-2). STATIC file checks and
post-build/post-image runs on fake trees in btbridge-os - nothing is built for a board and nothing is booted; every runtime statement
(U-Boot on a Pi, power cuts, vfat permissions on a device) stays HARDWARE-PENDING (gate items P3-1, P3-5, P3-6, P4-1).

  * N-6:  the U-Boot environment exists twice on the boot FAT (CONFIG_ENV_REDUNDANT, uboot.env + uboot-redund.env), both copies ship
          with the card (fw_setenv needs both from the first boot on), they hold U-Boot's complete default environment plus the slot
          state, and boot.cmd fails safe when it runs on U-Boot's built-in default environment (both copies lost);
  * N-13: the growdata unit has no guard that is evaluated on the empty mount point before the mount;
  * N-14: the boot FAT is root-owned; only the boot step (supplementary group hifi-boot) may write it;
  * N-8:  systemd-resolved runs without LLMNR and mDNS responders (Avahi owns mDNS)."""
import configparser
import glob
import os
import re
import sys
import zlib

from oshelpers import BOARD, REPO, make_ca, sh
from test_update_trust import post_build

sys.path.insert(0, os.path.join(REPO, "os", "tools"))
import image_check  # noqa: E402

EXT = os.path.join(REPO, "os", "br2-external")
UNITS = os.path.join(BOARD, "overlay", "usr", "lib", "systemd", "system")
BOOT_GROUP, BOOT_GID = "hifi-boot", 991


def read(*p):
    return open(os.path.join(*p), encoding="utf-8").read()


def unit_values(text, key):
    return [v for k, _, v in (ln.partition("=") for ln in text.splitlines()) if k.strip() == key]


# ---------------------------------------------------------------- N-6 redundant U-Boot environment
def test_uboot_is_configured_for_two_environment_copies_on_the_fat():
    frag = read(BOARD, "uboot", "uboot.fragment").splitlines()
    assert "CONFIG_ENV_REDUNDANT=y" in frag and 'CONFIG_ENV_FAT_FILE_REDUND="uboot-redund.env"' in frag
    common = read(EXT, "fragments", "common.fragment").splitlines()
    want = 'BR2_TARGET_UBOOT_CONFIG_FRAGMENT_FILES="$(BR2_EXTERNAL_HIFI_BRIDGE_PATH)/board/hifi-bridge/uboot/uboot.fragment"'
    assert want in common and "BR2_TARGET_UBOOT_INITIAL_ENV=y" in common     # u-boot-initial-env: the default env the card ships
    for b in ("rpi3_64", "rpi4_64", "rpizero2w_64"):
        d = read(EXT, "configs", f"hifi_bridge_{b}_defconfig").splitlines()
        assert want in d and "BR2_TARGET_UBOOT_INITIAL_ENV=y" in d, b


def test_fw_env_config_lists_both_copies_like_uboot(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, target = post_build(tmp_path, "OWN", own=dev["ca"])
    assert r.returncode == 0, r.stderr
    lines = [ln.split() for ln in (target / "etc/fw_env.config").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines == [["/boot/uboot.env", "0x0000", "0x4000"], ["/boot/uboot-redund.env", "0x0000", "0x4000"]]


def _env_image(text: str, flags: int) -> bytes:
    data = text.encode().replace(b"\n", b"\0") + b"\0\0"                 # pairs end with an empty one, then 0xff padding
    data += b"\xff" * (0x4000 - 5 - len(data))
    return zlib.crc32(data).to_bytes(4, "little") + bytes([flags]) + data


def test_image_check_reads_a_redundant_environment_and_rejects_a_torn_one(tmp_path):
    p = tmp_path / "uboot.env"
    p.write_bytes(_env_image("BOOT_ORDER=A B\nhifi_env=1", 7))
    env = image_check.read_env_image(str(p), 0x4000, redundant=True)
    assert env == {"flags": 7, "vars": {"BOOT_ORDER": "A B", "hifi_env": "1"}, "crc_ok": True}
    raw = bytearray(p.read_bytes()); raw[20] ^= 0xFF; p.write_bytes(bytes(raw))     # a torn write: CRC no longer matches
    assert image_check.read_env_image(str(p), 0x4000, redundant=True)["crc_ok"] is False


def test_both_environment_copies_ship_on_the_card_with_the_default_environment(tmp_path, tools):
    from test_image_layout import make_image
    img, _, b = make_image(tmp_path)
    boot = tmp_path / "bootfat"
    boot.mkdir()
    off = image_check_partitions(img)[0]["start"] * 512
    sh("mcopy", "-n", "-i", f"{img}@@{off}", "::uboot.env", "::uboot-redund.env", str(boot))
    a = image_check.read_env_image(str(boot / "uboot.env"), 0x4000, redundant=True)
    r = image_check.read_env_image(str(boot / "uboot-redund.env"), 0x4000, redundant=True)
    assert a["crc_ok"] and r["crc_ok"] and a["vars"] == r["vars"]
    assert (a["flags"], r["flags"]) == (1, 0)                       # copy 1 is the newer one: U-Boot and fw_env both start from it
    assert os.path.getsize(boot / "uboot.env") == os.path.getsize(boot / "uboot-redund.env") == 0x4000
    v = a["vars"]
    # U-Boot imports a valid copy INSTEAD of its built-in defaults: the copy must carry them (bootcmd!) or the card would not boot
    assert v["bootcmd"] == "bootflow scan" and v["kernel_addr_r"] == "0x00080000"
    assert {k: v[k] for k in ("BOOT_ORDER", "BOOT_A_LEFT", "BOOT_B_LEFT", "hifi_env")} == \
        {"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "0", "hifi_env": "1"}


def image_check_partitions(img):
    import json
    return json.loads(sh("sfdisk", "--json", str(img)).stdout)["partitiontable"]["partitions"]


def test_post_image_refuses_a_build_without_the_uboot_default_environment(tmp_path, tools):
    from test_image_layout import make_image
    r = make_image(tmp_path, initial_env=None, check=False)
    assert r.returncode != 0 and "u-boot-initial-env" in r.stderr


def test_boot_script_fails_safe_on_the_builtin_default_environment():
    """Both copies unreadable -> U-Boot runs on its built-in default environment, which has no hifi_env and no slot state. The script
    must then give BOTH slots attempts (A first; B too, because A may be the slot RAUC was writing), save that, and tell Linux on the
    kernel command line instead of silently behaving like a fresh card (B never tried)."""
    src = read(BOARD, "uboot", "boot.cmd")
    code = [ln.split("#")[0].rstrip() for ln in src.splitlines() if ln.split("#")[0].strip()]
    i = code.index('if test "${hifi_env}" != "1"; then')
    block = code[i:code.index("fi", i)]
    for need in ('setenv BOOT_ORDER "A B"', "setenv BOOT_A_LEFT 3", "setenv BOOT_B_LEFT 3", "setenv hifi_env 1",
                 "setenv hifi_bootenv hifi.bootenv=reset"):
        assert any(ln.strip() == need for ln in block), need
    assert code.index("setenv hifi_bootenv") < i                    # cleared on every boot: only the boot after the loss is marked
    assert i < next(n for n, ln in enumerate(code) if "for BOOT_SLOT in" in ln)
    bootargs = next(ln for ln in code if ln.strip().startswith('setenv bootargs "'))
    assert "${hifi_bootenv}" in bootargs and re.search(r"\bpanic=\d+\b", bootargs) and " ro " in bootargs
    # a fresh card still never tries the empty slot B (its shipped environment sets BOOT_B_LEFT=0)
    assert "BOOT_B_LEFT=0" in read(BOARD, "uboot", "uboot-env.txt").splitlines()


# ---------------------------------------------------------------- N-13 growdata guard
def _mount_unit_path(name):
    return "/" + name[:-len(".mount")].replace("-", "/").replace("\\x2d", "-")


def test_no_own_unit_checks_a_path_below_a_mount_it_runs_before():
    """A Condition/Assert on a path below a mount point is evaluated on the EMPTY directory of the read-only root when the unit runs
    before that mount (growdata's /data/.grown guard was never effective)."""
    bad = []
    for f in sorted(glob.glob(os.path.join(UNITS, "*.service"))):
        text = read(f)
        before = [_mount_unit_path(u) for v in unit_values(text, "Before") for u in v.split() if u.endswith(".mount")]
        for k, _, v in (ln.partition("=") for ln in text.splitlines()):
            if k.strip().startswith(("Condition", "Assert")) and "Path" in k:
                path = v.strip().lstrip("!|")
                bad += [f"{os.path.basename(f)}: {k}={v}" for m in before if path == m or path.startswith(m.rstrip("/") + "/")]
    assert not bad, bad
    grow = read(UNITS, "hifi-bridge-growdata.service")
    assert "/data/.grown" not in grow and "Before=data.mount" in grow
    assert ".grown" not in read(UNITS, "hifi-bridge-data.service")          # nothing reads the old marker any more


# ---------------------------------------------------------------- N-14 boot FAT permissions
def _boot_fstab_options(target):
    line = next(ln.split() for ln in (target / "etc/fstab").read_text().splitlines() if " /boot " in f" {ln} ")
    return dict(o.partition("=")[::2] for o in line[3].split(","))


def test_boot_fat_is_root_owned_and_writable_only_through_the_boot_group(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, target = post_build(tmp_path, "OWN", own=dev["ca"])
    assert r.returncode == 0, r.stderr
    o = _boot_fstab_options(target)
    assert o["uid"] == "0" and o["gid"] == str(BOOT_GID)
    fmask, dmask = int(o["fmask"], 8), int(o["dmask"], 8)
    assert fmask & 0o007 == 0o007 and dmask & 0o007 == 0o007       # others: nothing (the provisioning file holds secrets)
    assert fmask & 0o220 == 0 and dmask & 0o220 == 0                # owner root and the boot group may write
    users = read(EXT, "package", "hifi-bridge", "hifi-bridge.mk")
    table = users.split("define HIFI_BRIDGE_USERS")[1].split("endef")[0].strip().splitlines()
    rows = [ln.split(None, 8) for ln in table]
    assert ["-", "-", BOOT_GROUP, str(BOOT_GID), "*", "-", "-", "-"] == rows[1][:8]   # group only, no member
    hb = next(r for r in rows if r[0] == "hifi-bridge")
    assert BOOT_GROUP not in hb[7].split(",")                       # the service user itself is NOT in the group


def _units_with_dropins():
    """{unit: merged text} of every unit we ship (packaging + overlay), drop-ins appended."""
    out = {}
    for f in glob.glob(os.path.join(REPO, "packaging", "systemd", "*.service")) + glob.glob(os.path.join(UNITS, "*.service")):
        out[os.path.basename(f)] = read(f)
    for f in glob.glob(os.path.join(UNITS, "*.service.d", "*.conf")):
        out[os.path.basename(os.path.dirname(f))[:-2]] = out.get(os.path.basename(os.path.dirname(f))[:-2], "") + "\n" + read(f)
    return out


def test_only_the_boot_step_may_write_the_boot_partition():
    units = _units_with_dropins()
    members = sorted(u for u, t in units.items() if any(BOOT_GROUP in v.split() for v in unit_values(t, "SupplementaryGroups")))
    assert members == ["hifi-bridge-boot.service"]
    boot = units["hifi-bridge-boot.service"]
    assert "User=hifi-bridge" in boot and "ReadWritePaths=/boot" in boot and "CapabilityBoundingSet=\n" in boot
    for u, t in units.items():                                      # every other hifi-bridge unit sees /boot read-only at most
        if u != "hifi-bridge-boot.service" and "User=hifi-bridge" in t:
            assert "ProtectSystem=strict" in t, u
            assert not any("/boot" in v for v in unit_values(t, "ReadWritePaths")), u


# ---------------------------------------------------------------- N-8 resolved responders
def test_resolved_runs_without_llmnr_and_mdns_responders():
    c = configparser.ConfigParser()
    p = os.path.join(BOARD, "overlay", "etc", "systemd", "resolved.conf.d", "hifi-bridge.conf")
    assert c.read(p), p
    assert c.get("Resolve", "LLMNR") == "no" and c.get("Resolve", "MulticastDNS") == "no"
