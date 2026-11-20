"""P4/P6/P9 card layout end to end with REAL host tools and DUMMY board content (no Buildroot build, nothing booted):
post-image.sh -> genimage -> MBR with boot(FAT)/rootfs-A/rootfs-B/data -> manifest with the provisioning slot -> the flash tool writes a
SIMULATED card (a file) and fills the slot -> mtools reads the provisioning file back through the FAT filesystem -> growdata.sh grows the
data partition on the bigger card. Also compiles the U-Boot A/B script with mkimage."""
import json
import os
import shutil
import subprocess

import pytest

from btbridge import provisioning as P
from oshelpers import BOARD, REPO, sh
from hififlash import manifest as M, provision as FP, writer as W
from hififlash.fatslot import file_reader, find_file


def boot_scr(out):
    sh("mkimage", "-A", "arm64", "-O", "linux", "-T", "script", "-C", "none", "-n", "hifi-bridge", "-d", f"{BOARD}/uboot/boot.cmd", str(out))


# U-Boot's default environment as `make u-boot-initial-env` writes it (BR2_TARGET_UBOOT_INITIAL_ENV installs it into the target);
# an excerpt of the real rpi_arm64 one
INITIAL_ENV = "bootcmd=bootflow scan\nbootdelay=2\nfdt_addr_r=0x05600000\nkernel_addr_r=0x00080000\nscriptaddr=0x05400000\n"


@pytest.fixture
def built_image(tmp_path, tools):
    return make_image(tmp_path)


def make_image(tmp_path, initial_env=INITIAL_ENV, check=True):
    """post-image.sh over dummy board content. Returns (image, manifest, BINARIES_DIR), or the post-image result if check is False."""
    b = tmp_path / "images"; fw = b / "rpi-firmware"; (fw / "overlays").mkdir(parents=True)
    for f in ("bootcode.bin", "start.elf", "fixup.dat"):
        (fw / f).write_bytes(os.urandom(2048))
    shutil.copy(f"{BOARD}/firmware/config_3_64bit.txt", fw / "config.txt")
    shutil.copy(f"{BOARD}/firmware/cmdline.txt", fw / "cmdline.txt")
    (fw / "overlays" / "dummy.dtbo").write_bytes(b"dtbo")
    (b / "bcm2710-rpi-3-b-plus.dtb").write_bytes(b"dtb")
    (b / "u-boot.bin").write_bytes(os.urandom(4096))
    boot_scr(b / "boot.scr")
    root = tmp_path / "rootdir"; (root / "boot").mkdir(parents=True); (root / "boot" / "Image").write_bytes(os.urandom(8192))
    sh("mke2fs", "-q", "-t", "ext4", "-L", "rootfs", "-d", str(root), str(b / "rootfs.ext4"), "16M")
    target = tmp_path / "target"; (target / "etc").mkdir(parents=True)
    if initial_env is not None:
        (target / "etc" / "u-boot-initial-env").write_text(initial_env)
    cfg = tmp_path / ".config"; cfg.write_text('BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"\n')
    build = tmp_path / "build"; build.mkdir()
    r = sh(f"{BOARD}/post-image.sh", env={"BINARIES_DIR": str(b), "BUILD_DIR": str(build), "BR2_CONFIG": str(cfg),
                                          "TARGET_DIR": str(target), "PYTHONPATH": f"{REPO}/bridge:{REPO}/flashtool"}, check=check)
    if not check:
        return r
    from btbridge import __version__ as version
    img = b / f"hifi-bridge-hifi-bridge-rpi3-{version}.img"
    assert img.exists(), r.stdout + r.stderr
    return img, json.loads(open(f"{img}.manifest.json").read()), b


def partitions(img):
    return json.loads(sh("sfdisk", "--json", str(img)).stdout)["partitiontable"]


def test_layout_boot_a_b_data_and_slot(built_image):
    img, man, b = built_image
    pt = partitions(img)
    assert pt["label"] == "dos" and [p["type"] for p in pt["partitions"]] == ["c", "83", "83", "83"]
    a, bslot = pt["partitions"][1], pt["partitions"][2]
    assert a["size"] == bslot["size"] and pt["partitions"][0].get("bootable") is True
    assert all(p["start"] * 512 % (4 << 20) == 0 for p in pt["partitions"])      # SD erase-block alignment
    boot_off = pt["partitions"][0]["start"] * 512
    listing = sh("mdir", "-b", "-i", f"{img}@@{boot_off}", "::").stdout
    for f in ("boot.scr", "u-boot.bin", "config.txt", "hifi-provision.json", "bcm2710-rpi-3-b-plus.dtb", "overlays", "licenses",
              "uboot.env", "uboot-redund.env"):
        assert f in listing, listing
    assert "AGPL-3.0-only" in sh("mtype", "-i", f"{img}@@{boot_off}", "::licenses/NOTICE.txt").stdout
    cfgtxt = [x for x in sh("mtype", "-i", f"{img}@@{boot_off}", "::config.txt").stdout.splitlines() if x and not x.startswith("#")]
    assert "kernel=u-boot.bin" in cfgtxt and not any("miniuart-bt" in x for x in cfgtxt)
    off, size = man["slot"]["offset"], man["slot"]["size"]
    assert size == P.SLOT_SIZE and find_file(file_reader(str(img)), P.FILE_NAME) == (off, size)
    with open(img, "rb") as fh:
        fh.seek(off)
        assert P.is_empty(fh.read(size))
    assert M.load(M.dumps(man))["compatible"] == "hifi-bridge-rpi3" and man["size"] == os.path.getsize(img)
    data = pt["partitions"][3]
    with open(img, "rb") as fh, open(b / "p4", "wb") as out:
        fh.seek(data["start"] * 512); out.write(fh.read(data["size"] * 512))
    assert "hifi-data" in sh("dumpe2fs", "-h", str(b / "p4")).stdout


def test_flash_provision_and_read_back_through_fat_then_grow_data(built_image, tmp_path):
    img, man, _ = built_image
    card = tmp_path / "card.bin"
    with open(card, "wb") as fh:
        fh.truncate(1200 * 1000 ** 2)                          # a 1.2 GB SIMULATED card (sparse file)
    doc, key = FP.new_document("DE", device_name="Wohnzimmer")
    t = W.FileTarget(str(card), serial="SIM-1")
    with open(img, "rb") as src:
        res = W.write_card(src, man, t, t.identity(), P.render(doc))
    t.close()
    assert res["result"] == "ok" and res["provisioned"]
    boot_off = partitions(card)["partitions"][0]["start"] * 512
    via_fat = sh("mtype", "-i", f"{card}@@{boot_off}", "::hifi-provision.json").stdout.encode()
    assert P.parse(via_fat) == doc and P.decode_trust_key(doc["trust_key"]) == key
    grow = f"{BOARD}/overlay/usr/libexec/hifi-bridge/growdata.sh"
    before = partitions(card)["partitions"][3]["size"]
    assert "grown" in sh(grow, str(card), "4").stdout
    p4 = partitions(card)["partitions"][3]
    assert p4["size"] > before and (p4["start"] + p4["size"]) * 512 > 1200 * 1000 ** 2 - 2 * 1024 ** 2
    assert "already fills" in sh(grow, str(card), "4").stdout
    assert [p["type"] for p in partitions(card)["partitions"]] == ["c", "83", "83", "83"]


def test_uboot_script_compiles_and_never_tries_an_empty_slot_b(tmp_path, tools):
    boot_scr(tmp_path / "boot.scr")
    assert "Script" in sh("mkimage", "-l", str(tmp_path / "boot.scr")).stdout
    src = open(f"{BOARD}/uboot/boot.cmd").read()
    assert 'setenv BOOT_B_LEFT 0' in src and "rauc.slot=${rauc_slot}" in src and "|| reset" in src


def test_growdata_refuses_a_missing_partition(tmp_path, tools):
    disk = tmp_path / "d.img"
    with open(disk, "wb") as fh:
        fh.truncate(64 * 1024 ** 2)
    subprocess.run(["sfdisk", "-q", str(disk)], input="label: dos\n,32M,83\n", text=True, check=True)
    r = sh(f"{BOARD}/overlay/usr/libexec/hifi-bridge/growdata.sh", str(disk), "4", check=False)
    assert r.returncode != 0 and "not found" in r.stderr


@pytest.mark.parametrize("compat,synaptics_bt,synaptics_wifi", [("hifi-bridge-rpi3", False, False), ("hifi-bridge-rpi4", False, True),
                                                                  ("hifi-bridge-rpizero2w", True, True)])
def test_post_image_refuses_an_image_without_the_firmware_licences(tmp_path, tools, compat, synaptics_bt, synaptics_wifi):
    """The firmware licences require their text with every copy: if the radio firmware is enabled but its licence file is missing from
    the build, post-image fails instead of producing an image without the notice. The Synaptics End-User Software Tool License ships
    only with the board whose firmware list keeps Synaptics Bluetooth files (Zero 2 W, DECISIONS #134)."""
    b = tmp_path / "images"
    b.mkdir()
    build = tmp_path / "build"
    build.mkdir()
    cfg = tmp_path / ".config"
    cfg.write_text(f'BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="{compat}"\nBR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI=y\n')
    env = {"BINARIES_DIR": str(b), "BUILD_DIR": str(build), "BR2_CONFIG": str(cfg), "PYTHONPATH": f"{REPO}/bridge:{REPO}/flashtool"}
    r = sh(f"{BOARD}/post-image.sh", env=env, check=False)
    assert r.returncode != 0 and "licence file brcmfmac_sdio-firmware-rpi-*/LICENSE not found" in r.stderr
    (build / "brcmfmac_sdio-firmware-rpi-123").mkdir()
    (build / "brcmfmac_sdio-firmware-rpi-123" / "LICENSE").write_text("Broadcom licence text\n")
    r = sh(f"{BOARD}/post-image.sh", env=env, check=False)
    assert "licence file" not in r.stderr                  # passes the licence step (then stops: no firmware/kernel in this test)
    assert (b / "licenses" / "SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt").exists() == synaptics_bt
    assert (b / "licenses" / "brcmfmac_sdio-firmware-rpi.LICENSE").read_text() == "Broadcom licence text\n"
    notice = (b / "licenses" / "NOTICE.txt").read_text()
    assert compat in notice and ("SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt" in notice) == synaptics_bt
    assert ("Synaptics (Wi-Fi)" in notice) == synaptics_wifi and "UNMODIFIED" in notice


def _card(base, mtime):
    """post-image over deterministic dummy content whose files are stamped with `mtime`, like fresh outputs of a build at that time."""
    b = base / "images"
    (b / "rpi-firmware" / "overlays").mkdir(parents=True)
    (b / "rpi-firmware" / "start.elf").write_bytes(b"\x01" * 2048)
    shutil.copy(f"{BOARD}/firmware/config_3_64bit.txt", b / "rpi-firmware" / "config.txt")
    (b / "rpi-firmware" / "overlays" / "x.dtbo").write_bytes(b"dtbo")
    (b / "bcm2710-rpi-3-b-plus.dtb").write_bytes(b"dtb")
    (b / "u-boot.bin").write_bytes(b"\x02" * 4096)
    (b / "boot.scr").write_bytes(b"\x03" * 512)          # mkimage stamps the time (Buildroot's honours SOURCE_DATE_EPOCH)
    (base / "root" / "etc").mkdir(parents=True)
    (base / "root" / "etc" / "x").write_text("x\n")
    subprocess.run(["find", str(base), "-exec", "touch", "-h", "-d", mtime, "{}", "+"], check=True)
    # Buildroot (BR2_REPRODUCIBLE) stamps the target tree with SOURCE_DATE_EPOCH before the rootfs image; the boot files keep build times
    subprocess.run(["find", str(base / "root"), "-exec", "touch", "-h", "-d", "@1770000000", "{}", "+"], check=True)
    uuid = "8d3b1c54-2f0e-4a51-9c7e-3f1a6e0b9d21"
    sh("mke2fs", "-q", "-t", "ext4", "-U", uuid, "-E", f"hash_seed={uuid}", "-d", str(base / "root"), str(b / "rootfs.ext4"), "16M",
       env={"SOURCE_DATE_EPOCH": "1770000000"})
    cfg = base / ".config"
    cfg.write_text('BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"\n')
    (base / "build").mkdir()
    (base / "target" / "etc").mkdir(parents=True)
    (base / "target" / "etc" / "u-boot-initial-env").write_text(INITIAL_ENV)
    sh(f"{BOARD}/post-image.sh", env={"BINARIES_DIR": str(b), "BUILD_DIR": str(base / "build"), "BR2_CONFIG": str(cfg),
                                      "TARGET_DIR": str(base / "target"),
                                      "PYTHONPATH": f"{REPO}/bridge:{REPO}/flashtool", "SOURCE_DATE_EPOCH": "1770000000"})
    return next(b.glob("hifi-bridge-*.img")).read_bytes()


def test_card_image_is_reproducible_with_source_date_epoch(tmp_path, tools):
    """Same content, build outputs of different age (as in two real builds): byte-identical card. Boot FAT times, the data ext4's UUID,
    hash seed and time stamps, and the MBR are pinned (two clean real builds had identical files but different FATs before)."""
    a = _card(tmp_path / "a", "2026-01-01 10:00")
    b = _card(tmp_path / "b", "2026-11-19 18:00")
    assert len(a) == len(b) and a == b
