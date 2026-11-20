"""U-Boot environment hardening (DECISIONS #123/#133, docs/OTA.md section 4.1). STATIC checks of the configuration and SIMULATED
corruption cases with the image's environment tools: libubootenv 0.3.7 compiled NATIVELY from the source Buildroot pins + our patches
(os/tools/build_libubootenv.sh), on scratch files laid out like the image's fw_env.config; U-Boot's choice at the next boot is a port of
its env_check_redund() (os/tools/uboot_env_sim.py). Nothing is booted; U-Boot on a Pi, a real FAT and real power cuts stay
HARDWARE-PENDING (gate P3-1/P3-5/P3-6)."""
import os
import shutil
import sys

import pytest

from oshelpers import BOARD, REPO, make_ca, sh

sys.path.insert(0, os.path.join(REPO, "os", "tools"))
import uboot_env_sim as sim  # noqa: E402

EXT = os.path.join(REPO, "os", "br2-external")
SIZE = 0x4000
# an excerpt of the real rpi_arm64 u-boot-initial-env (sorted, as `make u-boot-initial-env` writes it)
UBOOT_DEFAULT = "bootcmd=bootflow scan\nbootdelay=2\nfdt_addr_r=0x05600000\nkernel_addr_r=0x00080000\nscriptaddr=0x05400000\n"


def env_file(path):
    return {k: v for k, _, v in (ln.partition("=") for ln in open(path).read().splitlines()) if k and not k.startswith("#")}


@pytest.fixture(scope="module")
def tool(tmp_path_factory):
    """fw_printenv/fw_setenv of the image (same source + patches), compiled for this container."""
    out = tmp_path_factory.mktemp("libubootenv")
    r = sh(os.path.join(REPO, "os", "tools", "build_libubootenv.sh"), str(out), check=False)
    if r.returncode == 2 and "missing" in r.stderr:
        pytest.skip(r.stderr.strip())
    assert r.returncode == 0, r.stderr
    return str(out / "fw_printenv")


@pytest.fixture(scope="module")
def unpatched_tool(tmp_path_factory):
    """The same libubootenv WITHOUT our patch (shows what the patch changes)."""
    root = tmp_path_factory.mktemp("nopatch")
    tools = root / "os" / "tools"
    tools.mkdir(parents=True)
    shutil.copy(os.path.join(REPO, "os", "tools", "build_libubootenv.sh"), tools)
    (root / "os" / "br2-external" / "patches" / "libubootenv").mkdir(parents=True)
    out = root / "bin"
    r = sh(str(tools / "build_libubootenv.sh"), str(out), check=False)
    if r.returncode == 2 and "missing" in r.stderr:
        pytest.skip(r.stderr.strip())
    assert r.returncode == 0, r.stderr
    return str(out / "fw_printenv")


def post_build_env(tmp_path, initial_env=UBOOT_DEFAULT, runs=1):
    """post-build.sh on a fake target that holds U-Boot's default environment (BR2_TARGET_UBOOT_INITIAL_ENV); returns (result, path)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    dev = make_ca(tmp_path, "dev")
    target = tmp_path / "target"
    (target / "etc").mkdir(parents=True)
    if initial_env is not None:
        (target / "etc" / "u-boot-initial-env").write_text(initial_env)
    cfg = tmp_path / "config"
    cfg.write_text('BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"\nBR2_PACKAGE_HIFI_BRIDGE_UPDATE_TRUST_OWN=y\n'
                   f'BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA="{dev["ca"]}"\nBR2_TARGET_UBOOT=y\n')
    r = None
    for _ in range(runs):
        r = sh(f"{BOARD}/post-build.sh", str(target), env={"BR2_CONFIG": str(cfg)}, check=False)
    return r, target / "etc" / "u-boot-initial-env"


def shipped_copies():
    """The two copies post-image.sh ships: U-Boot's default + the fresh-card slot state, serials 1/0."""
    v = dict(env_file_text(UBOOT_DEFAULT))
    v.update(env_file(os.path.join(BOARD, "uboot", "uboot-env.txt")))
    return sim.env_bytes(v, 1, SIZE), sim.env_bytes(v, 0, SIZE)


def env_file_text(text):
    return [(k, v) for k, _, v in (ln.partition("=") for ln in text.splitlines()) if k]


# ---------------------------------------------------------------- configuration (STATIC)
def test_the_image_uses_libubootenv_with_our_patch_and_no_target_uboot_tools():
    frag = open(os.path.join(EXT, "fragments", "common.fragment")).read().splitlines()
    patch_dir = 'BR2_GLOBAL_PATCH_DIR="board/raspberrypi/patches $(BR2_EXTERNAL_HIFI_BRIDGE_PATH)/patches"'
    for lines, where in [(frag, "common.fragment")] + [
            (open(os.path.join(EXT, "configs", f"hifi_bridge_{b}_defconfig")).read().splitlines(), b)
            for b in ("rpi3_64", "rpi4_64", "rpizero2w_64")]:
        assert "BR2_PACKAGE_LIBUBOOTENV=y" in lines, where
        assert "BR2_PACKAGE_UBOOT_TOOLS=y" not in lines and "BR2_PACKAGE_UBOOT_TOOLS_FWPRINTENV=y" not in lines, where
        assert patch_dir in lines, where                         # the upstream Raspberry Pi patches stay applied
        assert "BR2_PACKAGE_HOST_UBOOT_TOOLS=y" in lines, where   # mkimage (boot.scr) and mkenvimage (shipped copies) on the host
    assert "# BR2_PACKAGE_UBOOT_TOOLS is not set" in frag
    patches = sorted(os.listdir(os.path.join(EXT, "patches", "libubootenv")))
    assert patches and all(p.endswith(".patch") for p in patches)


def test_linux_default_slot_state_fails_safe_and_is_a_subset_of_the_shipped_keys():
    linux = env_file(os.path.join(BOARD, "uboot", "linux-default-env.txt"))
    shipped = env_file(os.path.join(BOARD, "uboot", "uboot-env.txt"))
    assert linux == {"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "3"}      # boot.cmd's own fail-safe attempts
    assert "hifi_env" not in linux and set(linux) <= set(shipped)   # post-image strips uboot-env.txt's keys to get U-Boot's defaults


def test_post_build_writes_the_complete_linux_default_environment(tmp_path, tools):
    r, init = post_build_env(tmp_path, runs=2)                     # an incremental build runs post-build again on its own output
    assert r.returncode == 0, r.stderr
    got = env_file(init)
    want = dict(env_file_text(UBOOT_DEFAULT))
    want.update({"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "3"})
    assert got == want and "hifi_env" not in got
    assert init.read_text().count("BOOT_ORDER=") == 1


def test_post_build_replaces_slot_variables_a_default_environment_might_carry(tmp_path, tools):
    r, init = post_build_env(tmp_path, UBOOT_DEFAULT + "BOOT_ORDER=B A\nhifi_env=1\nBOOT_B_LEFT=0\n")
    assert r.returncode == 0, r.stderr
    got = env_file(init)
    assert got["BOOT_ORDER"] == "A B" and got["BOOT_B_LEFT"] == "3" and "hifi_env" not in got


def test_post_build_refuses_a_uboot_build_without_its_default_environment(tmp_path, tools):
    r, _ = post_build_env(tmp_path, initial_env=None)
    assert r.returncode != 0 and "u-boot-initial-env" in r.stderr
    r, _ = post_build_env(tmp_path / "x", initial_env="foo=bar\n")
    assert r.returncode != 0 and "bootcmd" in r.stderr


def test_rauc_uses_the_uboot_backend_through_fw_setenv_only():
    """The only Linux writer of the environment is RAUC's U-Boot backend (fw_setenv); the bridge talks to RAUC over D-Bus and never runs
    fw_setenv or writes uboot.env itself (one safe path)."""
    conf = open(os.path.join(BOARD, "rauc", "system.conf.in")).read()
    assert "bootloader=uboot" in conf
    offenders = []
    for base in ("bridge", "packaging", os.path.join("os", "br2-external", "board", "hifi-bridge", "overlay")):
        for dp, _, fns in os.walk(os.path.join(REPO, base)):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    text = open(p, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if any(w in text for w in ("fw_setenv", "uboot.env", "uboot-redund.env", "saveenv")):
                    offenders.append(os.path.relpath(p, REPO))
    assert offenders == []


# ---------------------------------------------------------------- U-Boot's selection rule (port of env_check_redund)
@pytest.mark.parametrize("f1,f2,want", [(1, 0, 0), (0, 1, 1), (5, 5, 0), (255, 0, 1), (0, 255, 0), (254, 255, 1)])
def test_uboot_choice_follows_env_check_redund(f1, f2, want):
    a, b = ({"crc_ok": True, "flags": f} for f in (f1, f2))
    assert sim.uboot_choice(a, b) == want
    assert sim.uboot_choice({"crc_ok": False, "flags": f1}, b) == 1
    assert sim.uboot_choice(a, {"crc_ok": False, "flags": f2}) == 0
    assert sim.uboot_choice({"crc_ok": False, "flags": 0}, {"crc_ok": False, "flags": 0}) is None


def test_a_short_copy_never_has_a_valid_crc():
    a, _ = shipped_copies()
    assert sim.parse(a, SIZE)["crc_ok"] and not sim.parse(a[:SIZE // 2], SIZE)["crc_ok"] and not sim.parse(b"", SIZE)["crc_ok"]
    assert sim.parse(None, SIZE)["read"] is False


# ---------------------------------------------------------------- corruption matrix with the image's tools (SIMULATED)
def test_every_corruption_case_keeps_a_valid_bootable_environment(tmp_path, tools, tool):
    r, init = post_build_env(tmp_path)
    assert r.returncode == 0, r.stderr
    a, b = shipped_copies()
    res = sim.run_matrix(tool, a, b, str(init), SIZE)
    assert res["tool"] == "libubootenv 0.3.7"
    exp = sim.expectations(res, sim.parse(a, SIZE)["vars"], str(init))
    assert [c for c, ok, _ in exp if not ok] == [], sim.table_md(res, exp)
    by = {x["case"]: x for x in res["results"]}
    assert len(by) == 13
    # both copies bad: Linux reads OUR complete default (never uboot-tools' bootcmd=bootp), stores it, U-Boot then fails safe
    bb = by["both-crc-bad"]
    assert bb["printenv"]["vars"]["bootcmd"] == "bootflow scan" and bb["printenv"]["n_vars"] == 8
    assert bb["uboot_next"]["vars"]["bootcmd"] == "bootflow scan" and bb["uboot_next"]["boot_cmd"].startswith("fail-safe")
    # a copy cut short by a power cut: the OTHER copy is used and survives, the short one is rewritten to full size
    for case, kept in (("newest-truncated", "uboot-redund.env"), ("newest-empty", "uboot-redund.env"),
                       ("older-truncated", "uboot.env")):
        x = by[case]
        assert x["printenv"]["vars"]["hifi_env"] == "1", case
        assert [c["file"] for c in x["copies_after"] if c["unchanged"]] == [kept], case
        assert x["uboot_next"]["vars"]["BOOT_B_LEFT"] == "3" and x["uboot_next"]["vars"]["hifi_env"] == "1", case
    # a missing file: the tools refuse (RAUC reports an error), nothing is written; U-Boot recreates it at its next save
    for case in ("redundant-missing", "primary-missing"):
        assert by[case]["setenv"]["written"] == [] and by[case]["uboot_next"]["vars"]["hifi_env"] == "1"


def test_without_our_patch_a_short_copy_replaces_the_valid_one(tmp_path, tools, unpatched_tool):
    """Why the patch exists: stock libubootenv 0.3.7 aborts the read at a short copy, falls back to the default file and stores it as
    the NEWEST copy - the valid slot state of the other copy is lost (bootable thanks to our default, but the slot choice is gone)."""
    r, init = post_build_env(tmp_path)
    a, b = shipped_copies()
    res = sim.run_matrix(unpatched_tool, a, b, str(init), SIZE)
    x = next(c for c in res["results"] if c["case"] == "newest-truncated")
    assert x["printenv"]["vars"]["hifi_env"] is None                 # it did not read the valid copy
    assert x["uboot_next"]["vars"]["hifi_env"] is None               # next boot: slot state lost (fail-safe instead)
    assert not any(c["unchanged"] and c["crc_ok"] for c in x["copies_after"])   # the valid copy was overwritten
