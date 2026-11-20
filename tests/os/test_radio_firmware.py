"""Firmware per board and its licences (DECISIONS #125/#134, docs/IMAGE_LICENSING.md section 5). STATIC checks of the per-board lists and
post-build/image-check runs on FAKE firmware trees in btbridge-os (no firmware is downloaded, nothing is booted). Whether each board's
radio really loads these files stays HARDWARE-PENDING (gate P1-3)."""
import hashlib
import os
import shutil
import sys

import pytest

from oshelpers import BOARD, REPO, make_ca, sh

sys.path.insert(0, os.path.join(REPO, "os", "tools"))
import image_check  # noqa: E402

LISTS = os.path.join(BOARD, "firmware")
COMPATS = {"rpi3_64": "hifi-bridge-rpi3", "rpizero2w_64": "hifi-bridge-rpizero2w", "rpi4_64": "hifi-bridge-rpi4"}


def lst(compat):
    return image_check.radio_list(os.path.join(LISTS, f"radio-{compat}.txt"))


def resolve(entries, rel):
    seen = 0
    while entries[rel][0] == "link":
        rel = os.path.normpath(os.path.join(os.path.dirname(rel), entries[rel][1]))
        assert rel in entries, f"link target {rel} not in the list"
        seen += 1
        assert seen < 10
    return rel


# ---------------------------------------------------------------- the lists (STATIC)
@pytest.mark.parametrize("board", sorted(COMPATS))
def test_each_list_is_closed_and_tagged(board):
    e = lst(COMPATS[board])
    assert e
    for rel, (kind, val, lic) in e.items():
        assert rel.split("/")[0] in ("brcm", "cypress", "synaptics") and kind in ("file", "link") and \
            lic in ("cypress", "synaptics-wifi", "synaptics-bt"), rel
        target = resolve(e, rel)                                    # every link ends at a listed regular file
        assert e[target][2] == lic, f"{rel}: licence tag differs from its target {target}"
        if kind == "file":
            assert len(val) == 64 and int(val, 16) >= 0, rel
        if target.startswith("synaptics/"):
            assert lic == "synaptics-bt", rel


def test_pi3_and_pi4_images_carry_no_synaptics_bluetooth_patch():
    """OPEN_QUESTIONS #74: the revocable Synaptics End-User Software Tool License only where it is needed (Zero 2 W)."""
    syn = {v[1] for v in lst("hifi-bridge-rpizero2w").values() if v[0] == "file" and v[2] == "synaptics-bt"}
    assert len(syn) == 2                                            # SYN43430A1.hcd, SYN43430B0.hcd
    for compat in ("hifi-bridge-rpi3", "hifi-bridge-rpi4"):
        e = lst(compat)
        assert not any(r.startswith("synaptics/") or v[2] == "synaptics-bt" or v[1] in syn for r, v in e.items()), compat
        assert "brcm/BCM43430B0.hcd" not in e                       # byte-identical to synaptics/SYN43430B0.hcd


def test_each_board_keeps_the_files_its_chips_request():
    """Names the kernel (brcmfmac sdio.c BRCMF_FW_DEF/ENTRY, btbcm.c bcm_uart_subver_table) requests for the board's DT compatible."""
    need = {
        "hifi-bridge-rpi3": ["brcm/brcmfmac43455-sdio.raspberrypi,3-model-b-plus.bin", "brcm/brcmfmac43455-sdio.raspberrypi,3-model-b-plus.txt",
                             "brcm/brcmfmac43455-sdio.raspberrypi,3-model-b-plus.clm_blob", "brcm/BCM4345C0.raspberrypi,3-model-b-plus.hcd",
                             "brcm/brcmfmac43430-sdio.raspberrypi,3-model-b.bin", "brcm/BCM43430A1.raspberrypi,3-model-b.hcd"],
        "hifi-bridge-rpi4": ["brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.bin", "brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.txt",
                             "brcm/BCM4345C0.raspberrypi,4-model-b.hcd", "brcm/brcmfmac43456-sdio.raspberrypi,400.bin",
                             "brcm/BCM4345C5.raspberrypi,400.hcd", "brcm/brcmfmac43456-sdio.raspberrypi,4-compute-module.bin"],
        "hifi-bridge-rpizero2w": ["brcm/brcmfmac43430-sdio.raspberrypi,model-zero-2-w.bin", "brcm/brcmfmac43430-sdio.raspberrypi,model-zero-2-w.txt",
                                  "brcm/brcmfmac43430-sdio.clm_blob", "brcm/brcmfmac43430b0-sdio.raspberrypi,model-zero-2-w.bin",
                                  "brcm/brcmfmac43430b0-sdio.raspberrypi,model-zero-2-w.txt",
                                  "brcm/BCM43430A1.raspberrypi,model-zero-2-w.hcd", "brcm/BCM43430B0.raspberrypi,model-zero-2-w.hcd"],
    }
    for compat, names in need.items():
        e = lst(compat)
        assert [n for n in names if n not in e] == [], compat
    assert not any("43455" in r or "4345C" in r for r in lst("hifi-bridge-rpizero2w"))
    assert not any("43436" in r or "zero-2-w" in r for r in lst("hifi-bridge-rpi3"))


def test_boot_firmware_per_board():
    assert image_check.BOOT_FIRMWARE == {"rpi3_64": ["bootcode.bin", "fixup.dat", "start.elf"],
                                         "rpizero2w_64": ["bootcode.bin", "fixup.dat", "start.elf"], "rpi4_64": ["fixup4.dat", "start4.elf"]}
    for board, cfg in (("rpi3_64", "config_3_64bit.txt"), ("rpizero2w_64", "config_zero2w_64bit.txt"), ("rpi4_64", "config_4_64bit.txt")):
        text = open(os.path.join(BOARD, "firmware", cfg)).read().splitlines()
        start = next(x.split("=")[1] for x in text if x.startswith("start_file="))
        fixup = next(x.split("=")[1] for x in text if x.startswith("fixup_file="))
        assert {start, fixup} <= set(image_check.BOOT_FIRMWARE[board]), board
        defc = open(os.path.join(REPO, "os", "br2-external", "configs", f"hifi_bridge_{board}_defconfig")).read().splitlines()
        assert ("BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI4=y" in defc) == (board == "rpi4_64")
        assert ("BR2_PACKAGE_RPI_FIRMWARE_BOOTCODE_BIN=y" in defc) == (board != "rpi4_64")


# ---------------------------------------------------------------- post-build pruning on a fake tree
def fake_board(tmp_path, entries):
    """A copy of the board directory with a synthetic radio list for hifi-bridge-rpi3 + a fake firmware tree holding those entries and
    extra ones. Returns (post-build.sh, target)."""
    repo = tmp_path / "repo"
    board = repo / "os" / "br2-external" / "board" / "hifi-bridge"
    shutil.copytree(BOARD, board, symlinks=True)
    (repo / "bridge").symlink_to(os.path.join(REPO, "bridge"))
    target = tmp_path / "target"
    fw = target / "lib" / "firmware"
    lines = ["# synthetic"]
    for rel, content in entries.items():
        p = fw / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str) and content.startswith("->"):
            p.symlink_to(content[2:])
            lines.append(f"{rel} link {content[2:]} cypress")
        else:
            p.write_bytes(content)
            lines.append(f"{rel} file {hashlib.sha256(content).hexdigest()} cypress")
    (board / "firmware" / "radio-hifi-bridge-rpi3.txt").write_text("\n".join(lines) + "\n")
    for rel in ("brcm/BCM4343A2.hcd", "brcm/brcmfmac43456-sdio.raspberrypi,400.bin", "cypress/cyfmac43439-sdio.bin",
                "synaptics/SYN43430A1.hcd", "synaptics/SYN43430B0.hcd"):
        (fw / rel).parent.mkdir(parents=True, exist_ok=True)
        (fw / rel).write_bytes(b"extra " + rel.encode())
    return board / "post-build.sh", target, board


def run_post_build(tmp_path, script, target):
    dev = make_ca(tmp_path, "dev")
    cfg = tmp_path / "config"
    cfg.write_text('BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE="hifi-bridge-rpi3"\nBR2_PACKAGE_HIFI_BRIDGE_UPDATE_TRUST_OWN=y\n'
                   f'BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA="{dev["ca"]}"\nBR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI=y\n')
    return sh(str(script), str(target), env={"BR2_CONFIG": str(cfg)}, check=False)


ENTRIES = {"cypress/cyfmac43455-sdio.bin": b"cypress fw", "brcm/brcmfmac43455-sdio.bin": "->../cypress/cyfmac43455-sdio.bin",
           "brcm/brcmfmac43455-sdio.txt": b"nvram", "brcm/BCM4345C0.hcd": b"bt patch",
           "brcm/BCM4345C0.raspberrypi,3-model-b-plus.hcd": "->BCM4345C0.hcd"}


def tree(target):
    fw = target / "lib" / "firmware"
    return sorted(str(p.relative_to(fw)) for p in fw.rglob("*") if p.is_file() or p.is_symlink())


def test_post_build_keeps_exactly_the_list(tmp_path, tools):
    script, target, _ = fake_board(tmp_path, ENTRIES)
    r = run_post_build(tmp_path, script, target)
    assert r.returncode == 0, r.stderr
    assert tree(target) == sorted(ENTRIES)
    assert not (target / "lib" / "firmware" / "synaptics").exists()          # emptied directory removed
    assert "5 entries kept, 5 removed" in r.stdout
    (tmp_path / "again").mkdir()
    r = run_post_build(tmp_path / "again", script, target)
    assert r.returncode == 0 and "0 removed" in r.stdout                     # incremental build: idempotent


@pytest.mark.parametrize("damage,msg", [
    (lambda fw: (fw / "brcm/BCM4345C0.hcd").write_bytes(b"patched"), "differs"),
    (lambda fw: (fw / "brcm/brcmfmac43455-sdio.txt").unlink(), "missing"),
    (lambda fw: ((fw / "brcm/brcmfmac43455-sdio.bin").unlink(), (fw / "brcm/brcmfmac43455-sdio.bin").symlink_to("other.bin")), "link"),
])
def test_post_build_refuses_modified_or_missing_firmware(tmp_path, tools, damage, msg):
    script, target, _ = fake_board(tmp_path, ENTRIES)
    damage(target / "lib" / "firmware")
    r = run_post_build(tmp_path, script, target)
    assert r.returncode != 0 and msg in r.stderr, r.stderr


def test_post_build_needs_a_list_for_the_board(tmp_path, tools):
    script, target, board = fake_board(tmp_path, ENTRIES)
    (board / "firmware" / "radio-hifi-bridge-rpi3.txt").unlink()
    r = run_post_build(tmp_path, script, target)
    assert r.returncode != 0 and "no radio firmware list" in r.stderr


# ---------------------------------------------------------------- the image check on fake trees
def fake_image_tree(tmp_path, compat, mutate=None):
    """rootfs + boot FAT trees holding exactly the REAL list of `compat` (content unknown here: the check compares the list's own hashes
    by computing them, so the files get content whose hash we write into a copied list)."""
    lists = tmp_path / "lists"
    lists.mkdir()
    root, boot = tmp_path / "rootfs", tmp_path / "boot"
    fw = root / "usr" / "lib" / "firmware"
    for f in os.listdir(LISTS):
        if f.startswith("radio-"):
            shutil.copy(os.path.join(LISTS, f), lists / f)
    out = []
    for ln in open(os.path.join(LISTS, f"radio-{compat}.txt")).read().splitlines():
        if not ln or ln.startswith("#"):
            out.append(ln)
            continue
        rel, kind, val, lic = ln.split()
        p = fw / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if kind == "link":
            p.symlink_to(val)
        else:
            data = f"content of {rel}".encode()
            p.write_bytes(data)
            val = hashlib.sha256(data).hexdigest()
        out.append(f"{rel} {kind} {val} {lic}")
    (lists / f"radio-{compat}.txt").write_text("\n".join(out) + "\n")
    (boot / "licenses").mkdir(parents=True)
    lics = {x.split()[3] for x in out if x and not x.startswith("#")}
    names = ["NOTICE.txt", "brcmfmac_sdio-firmware-rpi.LICENSE", "rpi-firmware.LICENCE.broadcom"]
    names += [image_check.SYNAPTICS_TOOL_LICENCE] if "synaptics-bt" in lics else []
    for n in names:
        (boot / "licenses" / n).write_text("x")
    notice = f"board ({compat})" + (" Synaptics (Wi-Fi)" if "synaptics-wifi" in lics else "") + \
        (f" {image_check.SYNAPTICS_TOOL_LICENCE}" if "synaptics-bt" in lics else "")
    (boot / "licenses" / "NOTICE.txt").write_text(notice)
    if mutate:
        mutate(fw, boot)
    r = image_check.Report()
    image_check.check_radio_firmware(r, str(root), str(boot), compat, str(lists))
    return {c["id"]: c for c in r.checks}


@pytest.mark.parametrize("compat", sorted(COMPATS.values()))
def test_image_check_accepts_exactly_the_list(tmp_path, compat):
    res = fake_image_tree(tmp_path, compat)
    assert all(c["status"] == "PASS" for c in res.values()), res


def test_image_check_finds_extra_missing_and_licence_errors(tmp_path):
    def mutate(fw, boot):
        (fw / "brcm" / "BCM4343A2.hcd").write_bytes(b"extra")
        (fw / "brcm" / "BCM4345C0.hcd").unlink()
        (boot / "licenses" / image_check.SYNAPTICS_TOOL_LICENCE).write_text("x")      # not needed on a Pi 3 image
    res = fake_image_tree(tmp_path, "hifi-bridge-rpi3", mutate)
    assert res["firmware.radio-list"]["status"] == "FAIL"
    assert "extra ['brcm/BCM4343A2.hcd']" in res["firmware.radio-list"]["detail"] and "missing ['brcm/BCM4345C0.hcd']" in \
        res["firmware.radio-list"]["detail"]
    assert res["boot.firmware-licences"]["status"] == "FAIL"


def test_image_check_finds_a_synaptics_patch_under_a_broadcom_name(tmp_path, monkeypatch):
    """A Pi 3 image with brcm/BCM43430B0.hcd: byte-identical to the Zero 2 W's synaptics/SYN43430B0.hcd (Synaptics tool licence)."""
    real = lst("hifi-bridge-rpizero2w")["synaptics/SYN43430B0.hcd"][1]
    orig = image_check.filecmp_hash
    monkeypatch.setattr(image_check, "filecmp_hash", lambda p: real if p.endswith("BCM43430B0.hcd") else orig(p))
    res = fake_image_tree(tmp_path, "hifi-bridge-rpi3", lambda fw, boot: (fw / "brcm" / "BCM43430B0.hcd").write_bytes(b"x"))
    assert res["firmware.no-hidden-synaptics-bt"]["status"] == "FAIL" and "BCM43430B0.hcd" in res["firmware.no-hidden-synaptics-bt"]["detail"]
