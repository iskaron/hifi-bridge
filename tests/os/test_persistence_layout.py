"""Persistence rule (DECISIONS #121) and card layout (DECISIONS #120) - STATIC checks of the repository files and of an image tree assembled
from them (overlay + packaging units + bridge sources + post-build.sh), plus the data partition that post-image.sh/genimage really
creates. No board image is built and nothing is booted: whether the running device writes only these paths is HARDWARE-PENDING (G2 P4-5).

One source of numbers and allow-list: os/storage.json (docs/PERSISTENCE.md); os/tools/image_check.py applies the same checks to every
built image (ids persist.* and size.*)."""
import json
import os
import re
import shutil
import sys

import pytest

from oshelpers import BOARD, REPO, make_ca, sh
from test_image_layout import make_image, partitions
from test_update_trust import post_build

sys.path.insert(0, os.path.join(REPO, "os", "tools"))
import image_check  # noqa: E402
import size_plan  # noqa: E402

EXT = os.path.join(REPO, "os", "br2-external")
STORAGE = json.load(open(os.path.join(REPO, "os", "storage.json")))
LAY, BUD = STORAGE["layout"], STORAGE["data_budget"]
MIB = 1 << 20


def read(*p):
    return open(os.path.join(*p), encoding="utf-8").read()


# ---------------------------------------------------------------- DECISIONS #120: one set of numbers everywhere
def test_the_layout_numbers_agree_everywhere():
    assert (LAY["boot_mib"], LAY["slot_mib"]) == (64, 384)                       # owner decision #120
    want = f'BR2_TARGET_ROOTFS_EXT2_SIZE="{LAY["slot_mib"]}M"'
    assert want in read(EXT, "fragments", "common.fragment").splitlines()
    for b in ("rpi3_64", "rpi4_64", "rpizero2w_64"):
        assert want in read(EXT, "configs", f"hifi_bridge_{b}_defconfig").splitlines(), b
    cfg = read(BOARD, "genimage.cfg.in")
    assert "PLACEHOLDER" not in cfg.upper()
    boot = re.search(r"image boot\.vfat \{.*?\n\tsize = (\d+)M\n\}", cfg, re.S)
    data = re.search(r"image data\.ext4 \{.*?extraargs = \"([^\"]*)\".*?\n\tsize = (\d+)M\n\}", cfg, re.S)
    assert int(boot.group(1)) == LAY["boot_mib"] and int(data.group(2)) == LAY["data_initial_mib"]
    assert data.group(1).startswith(LAY["data_mkfs"] + " ")
    assert all(v % LAY["alignment_mib"] == 0 for v in (LAY["boot_mib"], LAY["slot_mib"], LAY["data_initial_mib"]))


def test_the_card_fits_a_nominal_2_gb_card_and_nothing_asks_for_16_gb():
    total = LAY["alignment_mib"] + LAY["boot_mib"] + 2 * LAY["slot_mib"] + LAY["data_initial_mib"]
    assert total * MIB <= LAY["card_target"]["usable_bytes"] < LAY["card_target"]["nominal_bytes"] == 2 * 10 ** 9
    from hififlash import disks
    assert disks.MIN_BYTES == LAY["card_target"]["usable_bytes"]                 # the flash tool refuses below the 2 GB target only
    for f in ("README.md", "docs/FLASH_TOOL.md", "docs/PROVISIONING.md", "docs/IMAGE_BUILD.md", "docs/HARDWARE_TEST_PROCEDURE.md",
              "docs/HARDWARE_GATE_CHECKLIST.md", "flashtool/hififlash/gui/i18n/en.json", "flashtool/hififlash/gui/i18n/de.json"):
        text = read(REPO, f)
        assert not re.search(r"(≥|>=|at least|mindestens|minimum)\s*16\s*GB", text, re.I), f    # recommendation only (DECISIONS #120)


def test_the_data_budget_matches_the_update_manager():
    from btbridge.ota import manager, transfer
    assert transfer.SPACE_MARGIN == BUD["space_margin_mib"] * MIB
    assert manager.MAX_BUNDLE >= BUD["largest_bundle_mib"] * MIB
    assert BUD["largest_bundle_mib"] * 2 == LAY["slot_mib"]                     # a full slot compressed 2:1 (docs/PERSISTENCE.md)


def test_a_fresh_data_partition_holds_one_update_of_a_full_slot(tmp_path, tools):
    """The data partition genimage really makes (post-image.sh) keeps room for the largest bundle + the update manager's free-space
    margin + state for the bridge (non-root: the reserved blocks do not count) even if the first-boot growth never happens."""
    img, _, b = make_image(tmp_path)
    p4 = partitions(img)["partitions"][3]
    assert p4["size"] * 512 == LAY["data_initial_mib"] * MIB
    with open(img, "rb") as fh, open(b / "p4", "wb") as out:
        fh.seek(p4["start"] * 512)
        left = p4["size"] * 512
        while left:
            chunk = fh.read(min(left, 4 * MIB))
            out.write(chunk)
            left -= len(chunk)
    e = image_check.ext4_numbers(str(b / "p4"))
    need = (BUD["largest_bundle_mib"] + BUD["space_margin_mib"] + BUD["state_mib"]) * MIB
    assert e["avail_bytes"] >= need and e["block_size"] == 4096 and e["inodes"] <= e["bytes"] // 65536
    r = image_check.Report()
    r.facts["partitions"] = [{"start": 0, "size": LAY["boot_mib"] * MIB}, {"start": 0, "size": LAY["slot_mib"] * MIB},
                             {"start": 0, "size": LAY["slot_mib"] * MIB}, {"start": 0, "size": LAY["data_initial_mib"] * MIB}]
    image_check.check_layout(r, str(img), str(b / "p4"), STORAGE)
    got = {c["id"]: c["status"] for c in r.checks}
    assert got["size.data-holds-update"] == got["size.data-ext4-layout"] == got["persist.data-partition-empty"] == "PASS", r.checks


def test_the_size_plan_shows_the_arithmetic_and_fails_when_a_measurement_no_longer_fits():
    row = {"board": "rpi3_64", "rootfs_used": 185.1, "slot": 384.0, "data_part": 256.0, "boot_part": 64.0, "data_avail": 225.0,
           "bundle": 73.6}
    p = size_plan.plan([row], STORAGE)
    assert all(p["checks"].values()) and p["need"] == 216 and p["card"] == 1092
    assert round(p["full_slot_bundle"]) == 153                                   # 384 x 73.6 / 185.1
    assert not size_plan.plan([{**row, "rootfs_used": 320.0}], STORAGE)["checks"]["slot_headroom"]
    assert not size_plan.plan([{**row, "data_avail": 200.0}], STORAGE)["checks"]["data_holds_update"]
    assert not size_plan.plan([{**row, "bundle": 185.1}], STORAGE)["checks"]["bundle_budget"]      # incompressible content


# ---------------------------------------------------------------- DECISIONS #121 over the image tree our files produce
@pytest.fixture
def tree(tmp_path, tools):
    """overlay + packaging units + bridge sources + post-build.sh, as Buildroot assembles them (stock packages are not part of it;
    the image check runs the same checks over the complete built tree)."""
    dev = make_ca(tmp_path, "dev")
    r, target = post_build(tmp_path, "OWN", own=dev["ca"])
    assert r.returncode == 0, r.stderr
    shutil.copytree(os.path.join(BOARD, "overlay"), target, dirs_exist_ok=True, symlinks=True)
    for u in ("hifi-bridge.service", "hifi-bridge-boot.service"):
        shutil.copy(os.path.join(REPO, "packaging", "systemd", u), target / "usr/lib/systemd/system" / u)
    shutil.copytree(os.path.join(REPO, "bridge", "btbridge"), target / "usr/lib/hifi-bridge/btbridge",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return target


def persistence(target):
    r = image_check.Report()
    image_check.check_persistence(r, str(target), STORAGE)
    return {c["id"]: (c["status"], c["detail"]) for c in r.checks}


def test_the_image_tree_follows_the_persistence_rule(tree):
    got = persistence(tree)
    assert {k: v[0] for k, v in got.items()} == {k: "PASS" for k in got}, got
    assert set(got) == {"persist.journald-volatile", "persist.no-persistent-journal-dir", "persist.coredumps-off",
                        "persist.python-no-bytecode-writes", "persist.data-allowlist", "persist.unit-write-paths",
                        "persist.tmpfiles-no-persistent-paths", "persist.bridge-faults-in-ram", "persist.ota-staging-cleanup",
                        "persist.timesync-save-interval"}
    lim = STORAGE["ram_limits"]
    j = image_check.conf_values(str(tree), None, ["etc/systemd/journald.conf.d"], "Journal")
    assert (j["RuntimeMaxUse"], j["RuntimeMaxFileSize"]) == (lim["journald_runtime_max_use"], lim["journald_runtime_max_file_size"])


@pytest.mark.parametrize("change, check", [
    (("etc/systemd/journald.conf.d/zz-local.conf", "[Journal]\nStorage=persistent\n"), "persist.journald-volatile"),
    (("etc/systemd/journald.conf.d/zz-local.conf", "[Journal]\nRuntimeMaxUse=200M\n"), "persist.journald-volatile"),
    (("var/log/journal/.keep", ""), "persist.no-persistent-journal-dir"),
    (("usr/lib/sysctl.d/99-local.conf", "kernel.core_pattern=core\n"), "persist.coredumps-off"),
    (("usr/lib/tmpfiles.d/local.conf", "d /data/cache 0755 root root -\n"), "persist.tmpfiles-no-persistent-paths"),
    (("usr/lib/tmpfiles.d/local.conf", "f /var/lib/bluetooth/extra 0644 root root -\n"), "persist.tmpfiles-no-persistent-paths"),
    (("usr/lib/systemd/system/logger.service", "[Service]\nExecStart=/bin/true\nReadWritePaths=/data/logs\n"), "persist.unit-write-paths"),
    (("usr/lib/systemd/system/cache.service", "[Service]\nExecStart=/bin/true\nStateDirectory=hifi-bridge\n"), "persist.unit-write-paths"),
    (("usr/lib/systemd/system/tool.service", "[Service]\nExecStart=/usr/bin/python3 -m tool\n"), "persist.python-no-bytecode-writes"),
    (("etc/systemd/timesyncd.conf.d/zz-local.conf", "[Time]\nSaveIntervalSec=60\n"), "persist.timesync-save-interval"),
])
def test_each_persistence_check_catches_a_violation(tree, change, check):
    path, text = change
    (tree / path).parent.mkdir(parents=True, exist_ok=True)
    (tree / path).write_text(text)
    assert persistence(tree)[check][0] == "FAIL"


def test_a_persistent_path_outside_the_allow_list_fails(tree):
    with open(tree / "etc/fstab", "a") as fh:
        fh.write("/data/logs /var/log none bind 0 0\n")
    assert persistence(tree)["persist.data-allowlist"][0] == "FAIL"


def test_the_fault_history_and_the_cleanup_are_wired_into_the_units(tree):
    unit = (tree / "usr/lib/systemd/system/hifi-bridge.service").read_text()
    (tree / "usr/lib/systemd/system/hifi-bridge.service").write_text(unit.replace(" --run-dir /run/hifi-bridge", ""))
    (tree / "usr/lib/hifi-bridge/btbridge/bootfiles.py").write_text("# no cleanup\n")
    got = persistence(tree)
    assert got["persist.bridge-faults-in-ram"][0] == got["persist.ota-staging-cleanup"][0] == "FAIL"


def test_every_persistent_path_is_documented():
    doc = read(REPO, "docs", "PERSISTENCE.md")
    for p in STORAGE["persistent"] + STORAGE["boot_fat_writes"]:
        assert f"`{p['path']}`" in doc, p["path"]
    for pattern, _, _ in STORAGE["bridge_files"]["patterns"]:
        if not any(c in pattern for c in "*[") and pattern not in ("certs", "ota", "ota/snapshots"):
            assert pattern.split("/")[-1] in doc, pattern
    assert "docs/PERSISTENCE.md" in read(REPO, "docs", "IMAGE_BUILD.md")
