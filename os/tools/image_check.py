"""Static checks of a BUILT Hi-Fi Bridge card image (Python stdlib + host tools in btbridge-os). Hardware-free: nothing is booted.

    python3 image_check.py --board B --image IMG --manifest M --rootfs DIR --boot DIR --config .config \
        [--rootfs-img rootfs.ext4] [--dev-ca ca.pem] [--file-list packages-file-list.txt] [--data-img p4] [--storage storage.json] \
        --json OUT --md OUT

The persistence checks (``persist.*``, DECISIONS #121) and the layout checks (``size.layout``, ``size.card-fits-2gb``,
``size.data-holds-update``, ``size.slot-headroom``; DECISIONS #120) take their numbers and the allow-list from os/storage.json.

--rootfs/--boot are the extracted slot-A root file system and boot FAT (scripts/os_check_image.sh extracts them from the card image with
debugfs/mtools as container root, so owners and modes are the image's). Every check is PASS, FAIL or INFO; INFO never fails. Items that
only a device can show are listed as HARDWARE-PENDING. Exit code 1 if any check FAILs.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zlib

OWN_UNITS = ["hifi-bridge.service", "hifi-bridge-boot.service", "hifi-bridge-data.service", "hifi-bridge-growdata.service",
             "hifi-bridge-health.service", "hifi-bridge-reboot.path", "hifi-bridge-reboot.service", "hifi-firewall.service",
             "hifi-netd.service", "hifi-net-wpa@.service", "hifi-net-hostapd@.service", "hifi-net-dhcp@.service"]
# enabled at boot by post-build.sh (template units are started by hifi-netd on demand, never at boot)
ENABLED_AT_BOOT = ["hifi-bridge.service", "hifi-bridge-boot.service", "hifi-bridge-data.service", "hifi-bridge-growdata.service",
                   "hifi-bridge-health.service", "hifi-bridge-reboot.path", "hifi-firewall.service", "hifi-netd.service"]
HARDWARE_PENDING = [
    "boots: firmware -> U-Boot -> kernel from slot A -> multi-user.target (G2 P1-2)",
    "U-Boot reads uboot.env/uboot-redund.env from the FAT, alternates its saves between them; fw_printenv/fw_setenv agree (G2 P3-1)",
    "power cut while U-Boot or fw_setenv writes the environment: the other copy is used, the right slot boots (G2 P3-5/P3-6)",
    "both copies damaged while Linux runs / a truncated copy on a real FAT / a card moved to another board (G2 P3-12...P3-14)",
    "each board (and Zero 2 W radio revision) loads its Wi-Fi/Bluetooth firmware from the per-board list (G2 P1-4)",
    "RAUC install into slot B, boot-count switch, rollback, power cuts (G2 P3-*)",
    "root-owned boot FAT: the boot step writes through the group hifi-boot, other hifi-bridge processes cannot (G2 P6-3)",
    "read-only root really stays read-only at runtime; /var overlay + /data bind mounts persist pairings across reboots (G2 P4-2)",
    "data partition grows to the card size on first boot (G2 P4-1)",
    "Wi-Fi/Bluetooth firmware loads (brcmfmac, hci_uart on PL011) and A2DP is stable (G2 P1-3)",
    "provisioning file is processed and scrubbed on real vfat (G2 P6-3)",
    "RAM/CPU at idle and while playing (G2 P4-4)",
    "at runtime nothing outside the allow-list of os/storage.json is written to the card; the journal stays in RAM (G2 P4-5)",
    "a nominal 2 GB card boots and grows the data partition; a 16 GB card likewise (G2 P4-1)",
]
STORAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage.json")
BOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "br2-external", "board", "hifi-bridge")
MIB = 1 << 20


class Report:
    def __init__(self):
        self.checks, self.facts = [], {}

    def add(self, cid, ok, detail="", info=False):
        status = "INFO" if info else ("PASS" if ok else "FAIL")
        self.checks.append({"id": cid, "status": status, "detail": detail})
        return ok

    @property
    def failed(self):
        return [c for c in self.checks if c["status"] == "FAIL"]


def sh(*argv, check=False):
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def cfg_values(path):
    vals = {}
    for line in open(path):
        m = re.match(r'^([A-Z][A-Z0-9_]+)=(.*)$', line.strip())          # Buildroot (BR2_*) and U-Boot (CONFIG_*) .config
        if m:
            vals[m.group(1)] = m.group(2).strip('"')
    return vals


def kv_file(path):
    out = {}
    for line in open(path):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def ini(path):
    sec, out = None, {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            sec = line.strip("[]")
            out[sec] = {}
        elif "=" in line and sec:
            k, v = line.split("=", 1)
            out[sec][k.strip()] = v.strip()
    return out


def tree_size(root):
    total = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            st = os.lstat(os.path.join(dp, fn))
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def resolve(root, path):
    """Path inside an extracted tree, following absolute symlinks relative to the tree (max 20 hops)."""
    p = path
    for _ in range(20):
        full = os.path.join(root, p.lstrip("/"))
        if os.path.islink(full):
            t = os.readlink(full)
            p = t if t.startswith("/") else os.path.normpath(os.path.join(os.path.dirname(p), t))
            continue
        return full
    return os.path.join(root, p.lstrip("/"))


def enabled_units(root):
    """{target.wants: [unit, ...]} from the .wants/.requires directories in /etc and /usr/lib."""
    out = {}
    for base in ("etc/systemd/system", "usr/lib/systemd/system"):
        for d in sorted(glob.glob(os.path.join(root, base, "*.wants")) + glob.glob(os.path.join(root, base, "*.requires"))):
            for u in sorted(os.listdir(d)):
                out.setdefault(os.path.basename(d), set()).add(u)
    return {k: sorted(v) for k, v in out.items()}


def is_enabled(units, unit):
    return any(unit in v for v in units.values())


def check_identity(r, root, board):
    osr = resolve(root, "/etc/os-release")
    if not r.add("identity.os-release", os.path.isfile(osr), "/etc/os-release present"):
        return
    o = kv_file(osr)
    r.facts["os_release"] = o
    r.add("identity.development-variant", o.get("VARIANT_ID") == "development" and "not hardware-validated" in o.get("PRETTY_NAME", ""),
          f"VARIANT_ID={o.get('VARIANT_ID')}, PRETTY_NAME={o.get('PRETTY_NAME')}")
    bi = os.path.join(root, "etc/hifi-bridge/build-info.json")
    if r.add("identity.build-info", os.path.isfile(bi), "/etc/hifi-bridge/build-info.json"):
        info = json.load(open(bi))
        r.facts["build_info"] = info
        r.add("identity.build-info-development", info.get("variant") == "development" and info.get("hardware_validated") is False,
              json.dumps(info))


def check_fstab_ro(r, root, cmdline_args):
    fstab = [ln.split() for ln in open(os.path.join(root, "etc/fstab")) if ln.strip() and not ln.startswith("#")]
    r.facts["fstab"] = [" ".join(x) for x in fstab]
    rootline = [x for x in fstab if len(x) > 1 and x[1] == "/"]
    r.add("rootfs.fstab-ro", bool(rootline) and "ro" in rootline[0][3].split(","), f"root entry: {rootline}")
    r.add("rootfs.bootargs-ro", " ro " in f" {cmdline_args} ", f"U-Boot bootargs: {cmdline_args.strip()}")
    want = {"/boot": "vfat", "/data": "ext4", "/var/lib/hifi-bridge": "none", "/var/lib/bluetooth": "none",
            "/var/lib/systemd/timesync": "none"}
    for mp, typ in want.items():
        e = [x for x in fstab if len(x) > 2 and x[1] == mp]
        r.add(f"rootfs.fstab{mp.replace('/', '-')}", bool(e) and e[0][2] == typ, f"{mp}: {e[0] if e else 'missing'}")
    var_mount = os.path.join(root, "usr/lib/systemd/system/var.mount")
    ok = os.path.isfile(var_mount) and "Type=overlay" in open(var_mount).read()
    r.add("rootfs.var-overlay-unit", ok, "/var = overlayfs with a tmpfs upper (Buildroot BR2_INIT_SYSTEMD_VAR_OVERLAYFS)")
    r.add("rootfs.var-not-symlink", os.path.isdir(os.path.join(root, "var")) and not os.path.islink(os.path.join(root, "var")),
          "var.mount has ConditionPathIsSymbolicLink=!/var")
    for d in ("boot", "data", "var/lib/hifi-bridge", "var/lib/bluetooth", "var/lib/systemd/timesync"):
        r.add(f"rootfs.mountpoint-{d.replace('/', '-')}", os.path.isdir(os.path.join(root, d)), f"/{d} exists as a mount point")


def check_kernel(r, root, boot_dir):
    img = os.path.join(root, "boot/Image")
    r.add("boot.kernel-in-slot", os.path.isfile(img), f"/boot/Image in the rootfs slot ({os.path.getsize(img) if os.path.isfile(img) else 0} bytes)")
    dtbs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(boot_dir, "*.dtb")))
    r.facts["boot_dtbs"] = dtbs
    r.add("boot.firmware-dtb", bool(dtbs), "DTBs on the FAT (loaded by the firmware, shared by both slots): " + ", ".join(dtbs))
    mods = glob.glob(os.path.join(root, "usr/lib/modules/*"))
    r.add("rootfs.kernel-modules", bool(mods), "modules: " + ", ".join(os.path.basename(m) for m in mods))


def check_boot_partition(r, boot_dir, board, man):
    files = sorted(os.listdir(boot_dir))
    r.facts["boot_files"] = files
    need = ["config.txt", "cmdline.txt", "u-boot.bin", "boot.scr", "hifi-provision.json"]
    need += ["start4.elf", "fixup4.dat"] if board == "rpi4_64" else ["start.elf", "fixup.dat", "bootcode.bin"]
    missing = [f for f in need if f not in files]
    r.add("boot.files", not missing, "missing: " + ", ".join(missing) if missing else "firmware, U-Boot, boot.scr, provisioning slot")
    cfg = [ln.strip() for ln in open(os.path.join(boot_dir, "config.txt")) if ln.strip() and not ln.startswith("#")]
    r.add("boot.config-uboot", "kernel=u-boot.bin" in cfg and "arm_64bit=1" in cfg, "; ".join(cfg))
    r.add("boot.config-no-miniuart-bt", not any("miniuart-bt" in x for x in cfg), "Bluetooth keeps the PL011 UART (DECISIONS #66)")
    prov = os.path.join(boot_dir, "hifi-provision.json")
    if os.path.isfile(prov):
        data = open(prov, "rb").read()
        empty = json.loads(data.decode()) == {"format": "hifi-bridge-provision/1", "state": "empty"}
        r.add("provisioning.placeholder", len(data) == 4096 and empty, f"{len(data)} bytes, empty placeholder: {empty}")
    # boot firmware per board (DECISIONS #134): exactly the variant the board's firmware loads, nothing else
    boot_fw = sorted(f for f in files if re.match(r"^(start.*\.elf|fixup.*\.dat|bootcode\.bin)$", f))
    want_fw = BOOT_FIRMWARE.get(board)
    r.add("firmware.boot-files", boot_fw == want_fw, f"boot firmware on the FAT: {boot_fw} (expected exactly {want_fw})")


# boot firmware each board's ROM/EEPROM loads (config.txt start_file/fixup_file): the Pi 3 B+ and Zero 2 W boot ROM reads bootcode.bin,
# the Pi 4 boots from its EEPROM and reads start4.elf/fixup4.dat (Buildroot BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI / _PI4)
BOOT_FIRMWARE = {"rpi3_64": ["bootcode.bin", "fixup.dat", "start.elf"], "rpizero2w_64": ["bootcode.bin", "fixup.dat", "start.elf"],
                 "rpi4_64": ["fixup4.dat", "start4.elf"]}
FIRMWARE_LISTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "br2-external", "board", "hifi-bridge", "firmware")
RADIO_DIRS = ("brcm", "cypress", "synaptics")
SYNAPTICS_TOOL_LICENCE = "SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt"


def radio_list(path):
    """{path below /usr/lib/firmware: (kind, sha256-or-target, licence)} of a firmware/radio-<compatible>.txt list."""
    out = {}
    for ln in open(path, encoding="utf-8"):
        f = ln.split()
        if f and not f[0].startswith("#"):
            out[f[0]] = (f[1], f[2], f[3])
    return out


def radio_tree(root):
    out = {}
    base = os.path.join(root, "usr/lib/firmware")
    for d in RADIO_DIRS:
        dd = os.path.join(base, d)
        for fn in sorted(os.listdir(dd)) if os.path.isdir(dd) else []:
            p = os.path.join(dd, fn)
            out[f"{d}/{fn}"] = ("link", os.readlink(p)) if os.path.islink(p) else ("file", filecmp_hash(p))
    return out


def check_radio_firmware(r, root, boot_dir, compat, lists_dir=FIRMWARE_LISTS):
    """DECISIONS #134: the image carries exactly the board's radio firmware list (no missing, no extra, unmodified), no file with the
    content of a Synaptics Bluetooth file under another licence tag, and exactly the licence texts those files need."""
    lp = os.path.join(lists_dir, f"radio-{compat}.txt")
    if not r.add("firmware.radio-list-present", os.path.isfile(lp), f"firmware list {os.path.relpath(lp, os.path.dirname(lists_dir))}"):
        return
    want, have = radio_list(lp), radio_tree(root)
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    differ = sorted(p for p in set(want) & set(have) if want[p][:2] != have[p])
    r.facts["radio_firmware"] = sorted(have)
    r.add("firmware.radio-list", not (missing or extra or differ),
          f"{len(have)} entries below /usr/lib/firmware/{{{','.join(RADIO_DIRS)}}} = {os.path.basename(lp)} ({len(want)})"
          + (f"; missing {missing}" if missing else "") + (f"; extra {extra}" if extra else "") + (f"; differ {differ}" if differ else ""))
    syn_bt = set()
    for other in glob.glob(os.path.join(lists_dir, "radio-*.txt")):
        syn_bt |= {v[1] for v in radio_list(other).values() if v[0] == "file" and v[2] == "synaptics-bt"}
    hidden = sorted(p for p, (k, v) in have.items() if k == "file" and v in syn_bt and want.get(p, ("", "", ""))[2] != "synaptics-bt")
    r.add("firmware.no-hidden-synaptics-bt", not hidden, "no file with the content of a Synaptics Bluetooth patch under another name or tag"
          + (f": {hidden}" if hidden else " (BCM43430B0.hcd is byte-identical to synaptics/SYN43430B0.hcd)"))
    lics = {v[2] for v in want.values()}
    lic_dir = os.path.join(boot_dir, "licenses")
    on_fat = sorted(os.listdir(lic_dir)) if os.path.isdir(lic_dir) else []
    need = ["NOTICE.txt", "brcmfmac_sdio-firmware-rpi.LICENSE", "rpi-firmware.LICENCE.broadcom"]
    need += [SYNAPTICS_TOOL_LICENCE] if "synaptics-bt" in lics else []
    r.add("boot.firmware-licences", on_fat == sorted(need),
          f"licenses/ on the FAT: {', '.join(on_fat)} (needed for {sorted(lics)}: {', '.join(sorted(need))})")
    notice = open(os.path.join(lic_dir, "NOTICE.txt"), errors="replace").read() if "NOTICE.txt" in on_fat else ""
    ok = compat in notice and (SYNAPTICS_TOOL_LICENCE in notice) == ("synaptics-bt" in lics) \
        and ("Synaptics (Wi-Fi)" in notice) == ("synaptics-wifi" in lics)
    r.add("boot.firmware-notice", ok, f"NOTICE.txt names this board ({compat}) and exactly the licences of its firmware")


def check_manifest(r, image, man, boot_dir):
    r.add("manifest.size", man["size"] == os.path.getsize(image), f"{man['size']} bytes")
    h = hashlib.sha256()
    with open(image, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    r.add("manifest.sha256", h.hexdigest() == man["sha256"], man["sha256"])
    with open(image, "rb") as fh:
        fh.seek(man["slot"]["offset"])
        raw = fh.read(man["slot"]["size"])
    r.add("manifest.slot-offset", raw == open(os.path.join(boot_dir, "hifi-provision.json"), "rb").read(),
          f"slot {man['slot']['file']} at byte {man['slot']['offset']} matches the FAT file")


def check_partitions(r, image, rootfs_img, slot_a):
    pt = json.loads(sh("sfdisk", "--json", image, check=True).stdout)["partitiontable"]
    parts = pt["partitions"]
    sec = pt.get("sectorsize", 512)
    r.facts["partitions"] = [{"node": p["node"], "type": p["type"], "start": p["start"] * sec, "size": p["size"] * sec,
                              "bootable": p.get("bootable", False)} for p in parts]
    r.add("layout.mbr", pt["label"] == "dos", "MBR (the Pi 3 / Zero 2 W boot ROM cannot read GPT, DECISIONS #65)")
    r.add("layout.four-partitions", [p["type"] for p in parts] == ["c", "83", "83", "83"], "boot FAT | rootfs-A | rootfs-B | data")
    if len(parts) == 4:
        r.add("layout.slots-equal", parts[1]["size"] == parts[2]["size"], f"slot size {parts[1]['size'] * sec} bytes")
        r.add("layout.boot-flag", parts[0].get("bootable") is True, "boot partition active")
        r.add("layout.aligned-4mib", all(p["start"] * sec % (4 << 20) == 0 for p in parts),
              "partition starts (bytes): " + ", ".join(str(p["start"] * sec) for p in parts))
    if rootfs_img and slot_a:
        same = filecmp_hash(rootfs_img) == filecmp_hash(slot_a)
        r.add("layout.slot-a-is-rootfs", same, "slot A content == images/rootfs.ext4")


def filecmp_hash(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_rauc(r, root, cfgv, dev_ca):
    conf = os.path.join(root, "etc/rauc/system.conf")
    if not r.add("rauc.system-conf", os.path.isfile(conf), "/etc/rauc/system.conf"):
        return
    c = ini(conf)
    compat = cfgv.get("BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE")
    r.add("rauc.compatible", c.get("system", {}).get("compatible") == compat, f"compatible={c.get('system', {}).get('compatible')}")
    r.add("rauc.bootloader-uboot", c.get("system", {}).get("bootloader") == "uboot", "bootloader=uboot (provisional, DECISIONS #53)")
    r.add("rauc.check-purpose", c.get("keyring", {}).get("check-purpose") == "codesign", "check-purpose=codesign")
    slots = {k: v for k, v in c.items() if k.startswith("slot.")}
    r.add("rauc.slots", [v.get("device") for v in slots.values()] == ["/dev/mmcblk0p2", "/dev/mmcblk0p3"], json.dumps(slots))
    kr = os.path.join(root, "etc/rauc/keyring.pem")
    text = open(kr).read() if os.path.isfile(kr) else ""
    r.add("rauc.keyring-public-only", "PRIVATE KEY" not in text and text.count("BEGIN CERTIFICATE") >= 1,
          f"{text.count('BEGIN CERTIFICATE')} certificate(s), no private key")
    if dev_ca and text:
        a = sh("openssl", "x509", "-in", kr, "-noout", "-fingerprint", "-sha256").stdout.strip()
        b = sh("openssl", "x509", "-in", dev_ca, "-noout", "-fingerprint", "-sha256").stdout.strip()
        r.add("rauc.keyring-is-dev-ca", a == b and text.count("BEGIN CERTIFICATE") == 1, f"keyring = temporary development CA ({a})")
    trust = os.path.join(root, "etc/hifi-bridge/update-trust.json")
    if r.add("rauc.update-trust-json", os.path.isfile(trust), "/etc/hifi-bridge/update-trust.json"):
        t = json.load(open(trust))
        r.facts["update_trust"] = t
        r.add("rauc.trust-own", t["mode"] == "own" and t["official_source"] is None, f"mode={t['mode']}, official_source={t['official_source']}")
    lines = fw_env_lines(os.path.join(root, "etc/fw_env.config"))
    r.add("rauc.fw-env", [x[0] for x in lines] == ["/boot/uboot.env", "/boot/uboot-redund.env"] and len({x[2] for x in lines}) == 1,
          "fw_env.config: " + (" | ".join(" ".join(x) for x in lines) or "missing") + " (two copies, review N-6)")
    for b in ("usr/bin/rauc", "usr/bin/fw_printenv", "usr/bin/fw_setenv"):
        r.add(f"rauc.binary-{os.path.basename(b)}", os.path.exists(resolve(root, "/" + b)), "/" + b)


def fw_env_lines(path):
    """[[file, offset, size], ...] of an fw_env.config (comments and blank lines skipped)."""
    if not os.path.isfile(path):
        return []
    return [ln.split()[:3] for ln in open(path) if ln.strip() and not ln.lstrip().startswith("#")]


def read_env_image(path, size, redundant=True):
    """A U-Boot environment image: CRC32 (little endian) [+ serial byte if redundant] + NUL-separated key=value pairs."""
    raw = open(path, "rb").read()
    hdr = 5 if redundant else 4
    data = raw[hdr:size]
    crc_ok = len(raw) >= size and int.from_bytes(raw[:4], "little") == zlib.crc32(data)
    out = {}
    for item in data.split(b"\0"):
        if not item:
            break
        k, _, v = item.decode("utf-8", "replace").partition("=")
        out[k] = v
    return {"flags": raw[4] if redundant and len(raw) > 4 else None, "vars": out, "crc_ok": crc_ok}


def check_uboot_env(r, root, boot_dir, uboot_config, fw_printenv=None, boot_script=None, uboot_bin=None, compare_tool=None):
    """Review N-6: two environment copies on the FAT, matching U-Boot's configuration and fw_env.config; both ship with the card and
    carry U-Boot's complete default environment + the fresh-card slot state; the tools fall back to the older copy when the newer one is
    torn; boot.cmd fails safe on U-Boot's built-in default environment."""
    lines = fw_env_lines(os.path.join(root, "etc/fw_env.config"))
    v = cfg_values(uboot_config) if uboot_config and os.path.isfile(uboot_config) else {}
    r.facts["uboot_env"] = {k: v.get(k) for k in ("CONFIG_ENV_SIZE", "CONFIG_ENV_IS_IN_FAT", "CONFIG_ENV_FAT_FILE", "CONFIG_ENV_FAT_FILE_REDUND",
                                                   "CONFIG_ENV_FAT_INTERFACE", "CONFIG_ENV_FAT_DEVICE_AND_PART", "CONFIG_ENV_REDUNDANT")}
    size = int(v["CONFIG_ENV_SIZE"], 16) if v.get("CONFIG_ENV_SIZE") else None
    if v:
        ok = len(lines) == 2 and size is not None and all(int(x[2], 16) == size for x in lines) and v.get("CONFIG_ENV_IS_IN_FAT") == "y" \
            and v.get("CONFIG_ENV_REDUNDANT") == "y" and v.get("CONFIG_ENV_FAT_FILE") == os.path.basename(lines[0][0]) \
            and v.get("CONFIG_ENV_FAT_FILE_REDUND") == os.path.basename(lines[1][0])
        r.add("rauc.uboot-env-matches-fw_env", ok, f"U-Boot CONFIG_ENV_REDUNDANT={v.get('CONFIG_ENV_REDUNDANT')}, CONFIG_ENV_SIZE="
              f"{v.get('CONFIG_ENV_SIZE')}, files {v.get('CONFIG_ENV_FAT_FILE')} + {v.get('CONFIG_ENV_FAT_FILE_REDUND')}; fw_env.config: "
              + " | ".join(" ".join(x) for x in lines))
    size = size or (int(lines[0][2], 16) if lines else 0x4000)
    copies = [os.path.join(boot_dir, n) for n in ("uboot.env", "uboot-redund.env")]
    if not r.add("boot.uboot-env-copies-present", all(os.path.isfile(p) and os.path.getsize(p) == size for p in copies),
                 f"uboot.env + uboot-redund.env on the FAT, {size} bytes each (fw_setenv needs both from the first boot on)"):
        return
    a, b = (read_env_image(p, size) for p in copies)
    r.add("boot.uboot-env-copies-valid", a["crc_ok"] and b["crc_ok"] and a["vars"] == b["vars"] and (a["flags"], b["flags"]) == (1, 0),
          f"CRC ok {a['crc_ok']}/{b['crc_ok']}, serials {a['flags']}/{b['flags']} (copy 1 newer), identical content: {a['vars'] == b['vars']}")
    want = {"BOOT_ORDER": "A B", "BOOT_A_LEFT": "3", "BOOT_B_LEFT": "0", "hifi_env": "1"}
    got = {k: a["vars"].get(k) for k in want}
    r.add("boot.uboot-env-fresh-card", got == want, f"slot state of a fresh card: {got} (slot B empty: never tried)")
    r.facts["uboot_env_shipped"] = a["vars"]
    init = os.path.join(root, "etc/u-boot-initial-env")
    linux_slot = env_text(os.path.join(BOARD_DIR, "uboot", "linux-default-env.txt"))
    slot_keys = set(want) | set(linux_slot)
    if r.add("boot.uboot-env-has-defaults", os.path.isfile(init), "/etc/u-boot-initial-env (Linux-side default environment)"):
        defaults = env_text(init)
        r.facts["uboot_env_linux_default"] = defaults
        diff = sorted(k for k, val in defaults.items() if k not in slot_keys and a["vars"].get(k) != val)
        r.add("boot.uboot-env-defaults-complete", not diff and "bootcmd" in a["vars"],
              f"{len(defaults)} variables in /etc/u-boot-initial-env, all of U-Boot's defaults in the shipped copies with the same value "
              f"(U-Boot imports a valid copy INSTEAD of its defaults); bootcmd={a['vars'].get('bootcmd')!r}"
              + (f"; differing: {', '.join(diff)}" if diff else ""))
        uboot_part = {k: v for k, v in a["vars"].items() if k not in slot_keys}
        got_slot = {k: defaults.get(k) for k in sorted(slot_keys)}
        want_slot = {k: linux_slot.get(k) for k in sorted(slot_keys)}
        ok = {k: v for k, v in defaults.items() if k not in slot_keys} == uboot_part and got_slot == want_slot \
            and defaults.get("bootcmd") == a["vars"].get("bootcmd")
        r.add("boot.uboot-env-linux-default", ok,
              "libubootenv's default when no copy is valid = U-Boot's complete default + " + ", ".join(f"{k}={v}" for k, v in linux_slot.items())
              + f" and no hifi_env (boot.cmd then fails safe); slot variables found: {got_slot}")
        if uboot_bin and os.path.isfile(uboot_bin):
            blob = open(uboot_bin, "rb").read()
            absent = sorted(k for k, v in uboot_part.items() if b"\0" + f"{k}={v}".encode() + b"\0" not in blob)
            ours = sorted(k for k in slot_keys if b"\0" + k.encode() + b"=" in blob)
            r.add("boot.uboot-builtin-env", not absent and not ours,
                  f"u-boot.bin's compiled-in default environment holds all {len(uboot_part)} U-Boot variables of the shipped copies "
                  f"(both copies bad -> the same bootcmd) and none of the slot variables (-> boot.cmd fails safe)"
                  + (f"; not found: {absent}" if absent else "") + (f"; unexpected: {ours}" if ours else ""))
    if fw_printenv:
        env_corruption_matrix(r, copies, size, fw_printenv, init, compare_tool)
    if boot_script and os.path.isfile(boot_script):
        src = [ln.split("#")[0].strip() for ln in open(boot_script, errors="replace")]   # dumpimage keeps the binary length header
        ok = 'if test "${hifi_env}" != "1"; then' in src and "setenv BOOT_B_LEFT 3" in src and "setenv hifi_bootenv hifi.bootenv=reset" in src
        bootargs = next((ln for ln in src if ln.startswith('setenv bootargs "')), "")
        r.add("boot.uboot-script-failsafe", ok and "${hifi_bootenv}" in bootargs and re.search(r"\bpanic=\d+\b", bootargs) is not None,
              "boot.scr: without hifi_env (built-in default env) both slots get attempts and the boot is marked hifi.bootenv=reset; "
              "panic=N lets a slot that cannot run fall back")


def check_env_tools(r, root, file_list=None):
    """DECISIONS #133: fw_printenv/fw_setenv are libubootenv's (default environment /etc/u-boot-initial-env, configuration
    /etc/fw_env.config), and the target has no second implementation from uboot-tools."""
    tool = resolve(root, "/usr/bin/fw_printenv")
    blob = open(tool, "rb").read() if os.path.isfile(tool) else b""
    setenv = os.path.join(root, "usr/bin/fw_setenv")
    libs = glob.glob(os.path.join(root, "usr/lib/libubootenv.so*"))
    r.add("rauc.env-tool-libubootenv", bool(libs) and b"/etc/u-boot-initial-env" in blob and b"/etc/fw_env.config" in blob
          and os.path.islink(setenv) and os.path.basename(os.readlink(setenv)) == "fw_printenv",
          f"fw_printenv links libubootenv ({', '.join(sorted(os.path.basename(x) for x in libs)) or 'missing'}), defaults to "
          "/etc/u-boot-initial-env and /etc/fw_env.config; fw_setenv -> fw_printenv")
    owners = {}
    if file_list and os.path.isfile(file_list):
        for ln in open(file_list, errors="replace"):
            pkg, _, path = ln.strip().partition(",")
            owners.setdefault(pkg, []).append(path)
    leftovers = [b for b in ("usr/bin/mkimage", "usr/bin/dumpimage", "usr/bin/mkenvimage") if os.path.lexists(os.path.join(root, b))]
    ok = not owners.get("uboot-tools") and not leftovers and (not owners or "./usr/bin/fw_printenv" in owners.get("libubootenv", []))
    r.add("rauc.env-tool-single", ok, "no target uboot-tools (one environment implementation): "
          f"uboot-tools files {owners.get('uboot-tools') or 'none'}, leftovers {leftovers or 'none'}, fw_printenv from "
          f"{[p for p, fs in owners.items() if './usr/bin/fw_printenv' in fs] or 'unknown (no file list)'}")


def env_text(path):
    """key=value lines of an environment text file (comments and blank lines skipped)."""
    out = {}
    for ln in open(path, encoding="utf-8", errors="replace"):
        ln = ln.rstrip("\n")
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k] = v
    return out


def env_corruption_matrix(r, copies, size, tool, default, compare_tool=None):
    """DECISIONS #133: the image's environment tools (libubootenv compiled from the image's source + patches by
    os/tools/build_libubootenv.sh) on scratch copies of the SHIPPED environment and with the image's own /etc/u-boot-initial-env, through
    every corruption case of uboot_env_sim.py (SIMULATED; U-Boot's choice is a port of env_check_redund). Optionally the same matrix
    with another fw_printenv (the uboot-tools host build of the previous images) as an INFO comparison."""
    import uboot_env_sim as sim
    c1, c2 = (open(p, "rb").read() for p in copies)
    res = sim.run_matrix(tool, c1, c2, default, size)
    exp = sim.expectations(res, sim.parse(c1, size)["vars"], default)
    bad = [f"{c}: {why}" for c, ok, why in exp if not ok]
    r.facts["uboot_env_matrix"] = res
    r.facts["uboot_env_matrix_md"] = sim.table_md(res, exp)
    r.add("boot.uboot-env-corruption-matrix", not bad and res["tool"].startswith("libubootenv"),
          f"{res['tool']}: {len(exp) - len(bad)}/{len(exp)} corruption cases behave (valid copy used, the other never destroyed, both bad "
          "-> the image's complete default, fail-safe boot); table below" + (f"; FAILED: {'; '.join(bad)}" if bad else ""))
    if compare_tool:
        cres = sim.run_matrix(compare_tool, c1, c2, default, size)
        r.facts["uboot_env_matrix_compare_md"] = sim.table_md(cres)
        both_bad = next(x for x in cres["results"] if x["case"] == "both-crc-bad")
        hang = [x["case"] for x in cres["results"] if x["printenv"]["rc"] == 124]
        r.add("boot.uboot-env-compare-previous-tool", True, f"comparison {cres['tool']}: both copies bad -> stored bootcmd="
              f"{(both_bad['uboot_next']['vars'] or {}).get('bootcmd')!r}; did not finish on {hang or 'no case'}", info=True)


def merged_units(root):
    """{unit name: text of the unit file + its drop-ins} for every .service in /usr/lib and /etc (masks to /dev/null skipped)."""
    out = {}
    for base in ("usr/lib/systemd/system", "etc/systemd/system"):
        for p in sorted(glob.glob(os.path.join(root, base, "*.service"))):
            if not os.path.islink(p) or not os.readlink(p).startswith("/dev/null"):
                f = resolve(root, "/" + os.path.relpath(p, root))
                if os.path.isfile(f):
                    out.setdefault(os.path.basename(p), open(f, errors="replace").read())
        for p in sorted(glob.glob(os.path.join(root, base, "*.service.d", "*.conf"))):
            u = os.path.basename(os.path.dirname(p))[:-2]
            out[u] = out.get(u, "") + "\n" + open(p, errors="replace").read()
    return out


def unit_values(text, key):
    return [v.strip() for k, _, v in (ln.partition("=") for ln in text.splitlines()) if k.strip() == key]


def check_boot_fat_access(r, root):
    """Review N-14: the boot FAT is root-owned; only the group hifi-boot (no members) may write it, and only the boot step joins it."""
    fstab = [ln.split() for ln in open(os.path.join(root, "etc/fstab")) if ln.strip() and not ln.startswith("#")]
    boot = next((x for x in fstab if len(x) > 3 and x[1] == "/boot"), None)
    groups = {g.split(":")[0]: g.strip().split(":") for g in open(os.path.join(root, "etc/group")) if g.count(":") >= 3}
    hb = groups.get("hifi-boot")
    o = dict(x.partition("=")[::2] for x in boot[3].split(",")) if boot else {}
    fm, dm = int(o.get("fmask", "0"), 8), int(o.get("dmask", "0"), 8)
    ok = bool(boot and hb) and o.get("uid") == "0" and o.get("gid") == hb[2] and fm & 0o007 == 0o007 and dm & 0o007 == 0o007
    r.add("boot.fat-root-owned", ok, f"/boot options {boot[3] if boot else 'missing'}; group hifi-boot: {':'.join(hb) if hb else 'missing'}")
    r.add("boot.fat-group-no-members", bool(hb) and hb[3] == "", "hifi-boot has no members (not even hifi-bridge): only units that "
          "declare SupplementaryGroups=hifi-boot can write the FAT")
    units = merged_units(root)
    members = sorted(u for u, t in units.items() if any("hifi-boot" in v.split() for v in unit_values(t, "SupplementaryGroups")))
    others = sorted(u for u, t in units.items() if u != "hifi-bridge-boot.service" and "hifi-bridge" in unit_values(t, "User")
                    and ("strict" not in unit_values(t, "ProtectSystem") or any("/boot" in v for v in unit_values(t, "ReadWritePaths"))))
    r.add("units.boot-fat-writers", members == ["hifi-bridge-boot.service"] and not others,
          f"units in group hifi-boot: {members}; other hifi-bridge units without ProtectSystem=strict or with /boot writable: {others or 'none'}")
    grow = units.get("hifi-bridge-growdata.service", "")
    r.add("units.growdata-no-premount-guard", "Before=data.mount" in grow and not any("/data" in v for k in ("ConditionPathExists",
          "ConditionPathIsDirectory", "ConditionFileNotEmpty") for v in unit_values(grow, k)),
          "growdata has no condition on a path below /data (it would be evaluated on the empty mount point, review N-13); it is idempotent")


def check_users(r, root):
    passwd = [ln.split(":") for ln in open(os.path.join(root, "etc/passwd")) if ln.strip()]
    shadow = [ln.split(":") for ln in open(os.path.join(root, "etc/shadow")) if ln.strip()]
    hb = [p for p in passwd if p[0] == "hifi-bridge"]
    r.add("users.hifi-bridge", bool(hb) and hb[0][2] == "990" and hb[0][3] == "990", f"{':'.join(hb[0]).strip() if hb else 'missing'}")
    groups = {g.split(":")[0]: g.strip().split(":")[3].split(",") for g in open(os.path.join(root, "etc/group")) if g.count(":") >= 3}
    r.add("users.hifi-bridge-groups", "hifi-bridge" in groups.get("bluetooth", []) and "hifi-bridge" in groups.get("audio", []),
          f"bluetooth: {groups.get('bluetooth')}, audio: {groups.get('audio')}")
    locked = [s[0] for s in shadow if s[1].startswith("*") or s[1].startswith("!")]
    r.add("users.all-locked", len(locked) == len(shadow), f"{len(locked)}/{len(shadow)} accounts without a usable password")
    st = os.stat(os.path.join(root, "etc/shadow"))
    r.add("users.shadow-mode", stat.S_IMODE(st.st_mode) & 0o077 == 0 and st.st_uid == 0,
          f"/etc/shadow mode {oct(stat.S_IMODE(st.st_mode))} uid {st.st_uid}")
    remote = [p for p in ("usr/sbin/sshd", "usr/sbin/dropbear", "usr/bin/dropbear", "usr/sbin/telnetd", "usr/bin/telnetd")
              if os.path.lexists(os.path.join(root, p))]
    r.add("users.no-remote-login", not remote, "no SSH/telnet daemon" if not remote else "found: " + ", ".join(remote))


def check_setuid(r, root):
    found = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and st.st_mode & (stat.S_ISUID | stat.S_ISGID):
                found.append(f"/{os.path.relpath(p, root)} {oct(stat.S_IMODE(st.st_mode))}")
    r.facts["setuid_setgid"] = sorted(found)
    r.add("rootfs.setuid-files", True, f"{len(found)} setuid/setgid file(s): " + ", ".join(sorted(found)), info=True)


def check_bridge(r, root):
    pkg = os.path.join(root, "usr/lib/hifi-bridge/btbridge")
    r.add("bridge.package", os.path.isfile(os.path.join(pkg, "__init__.py")), "/usr/lib/hifi-bridge/btbridge")
    py = glob.glob(os.path.join(pkg, "**", "*.py"), recursive=True)
    pyc = glob.glob(os.path.join(pkg, "**", "__pycache__", "*.pyc"), recursive=True)
    r.add("bridge.bytecode", len(pyc) >= len(py) > 0, f"{len(py)} modules, {len(pyc)} precompiled (read-only root)")
    r.add("bridge.python", bool(glob.glob(os.path.join(root, "usr/bin/python3*"))), "python3 interpreter")
    for mod in ("jeepney",):
        r.add(f"bridge.dep-{mod}", bool(glob.glob(os.path.join(root, f"usr/lib/python3*/site-packages/{mod}"))), mod)


def _conf_text(root, pattern):
    return "\n".join(open(p, errors="replace").read() for p in sorted(glob.glob(os.path.join(root, pattern))) if os.path.isfile(p))


def check_audio_ingress(r, root):
    """The phone's A2DP audio must reach the bridge as a recordable source (real-hardware finding G1, docs/PIPEWIRE_CAPTURE.md)."""
    plugin = glob.glob(os.path.join(root, "usr/lib/spa-0.2/bluez5/libspa-bluez5.so"))
    r.add("audio.bluez5-spa-plugin", bool(plugin), "PipeWire's BlueZ plugin (A2DP endpoints): " + ("present" if plugin else "MISSING"))
    tools = {t: os.path.exists(os.path.join(root, "usr/bin", t)) for t in ("pw-cat", "pw-dump")}
    r.add("audio.capture-tools", all(tools.values()), ", ".join(f"{t}: {'yes' if ok else 'MISSING'}" for t, ok in tools.items())
          + " (capture + discovery of the phone's node)")
    wp = _conf_text(root, "etc/wireplumber/wireplumber.conf.d/*.conf")
    role = re.search(r"monitor\.bluez\.rules\s*=.*?node\.name\s*=\s*\"~bluez_input\.\*\".*?bluez5\.media-source-role\s*=\s*\"input\"",
                     wp, re.S)
    r.add("audio.a2dp-input-role", bool(role), "WirePlumber rule bluez5.media-source-role = input for bluez_input.* "
          + ("present" if role else "MISSING: the phone would play on the device's own output and the bridge would record nothing (G1)"))
    pw = _conf_text(root, "etc/pipewire/pipewire.conf.d/*.conf")
    r.add("audio.allowed-rates", bool(re.search(r"allowed-rates\s*=\s*\[[^\]]*44100", pw)),
          "default.clock.allowed-rates includes 44100 (no 44.1 -> 48 -> 44.1 kHz resampling)")
    main = _conf_text(root, "usr/share/wireplumber/wireplumber.conf")
    seat = re.search(r"mixin\.systemwide-session\s*=\s*\{[^}]*monitor\.bluez\.seat-monitoring\s*=\s*disabled", main)
    r.add("audio.seat-monitoring", True, "system-wide WirePlumber profile disables Bluetooth seat monitoring: "
          + ("yes" if seat else "not found") + " (whether the image's PipeWire instance is system-wide is OPEN_QUESTIONS #24/#25)", info=True)


def check_units(r, root, units):
    r.facts["enabled_units"] = units
    for u in ENABLED_AT_BOOT:
        r.add(f"units.enabled-{u}", is_enabled(units, u), f"{u} is wanted by " +
              ", ".join(k for k, v in units.items() if u in v) if is_enabled(units, u) else f"{u} not enabled")
    for u in ("bluetooth.service", "avahi-daemon.service", "systemd-networkd.service", "systemd-timesyncd.service",
              "systemd-resolved.service", "dbus.service", "rauc.service", "rauc-mark-good.service", "var.mount",
              "prepare-var-overlay.service", "wpa_supplicant.service", "hostapd.service", "dnsmasq.service", "nftables.service",
              "pipewire.service", "wireplumber.service"):
        present = os.path.exists(os.path.join(root, "usr/lib/systemd/system", u)) or os.path.exists(os.path.join(root, "etc/systemd/system", u))
        r.add(f"units.state-{u}", True, f"unit file {'present' if present else 'absent'}, {'enabled' if is_enabled(units, u) else 'not enabled'}",
              info=True)
    mg = os.path.join(root, "etc/systemd/system/rauc-mark-good.service")
    r.add("units.rauc-mark-good-masked", os.path.islink(mg) and os.readlink(mg) == "/dev/null",
          "rauc-mark-good masked: only hifi-bridge-health.service marks a slot good (docs/OTA.md section 4)")
    for u in ("wpa_supplicant.service", "avahi-dnsconfd.service"):
        p = os.path.join(root, "etc/systemd/system", u)
        r.add(f"units.masked-{u}", os.path.islink(p) and os.readlink(p) == "/dev/null" and not is_enabled(units, u),
              f"stock {u} masked (Wi-Fi only via hifi-netd; no DNS from mDNS announcements)")
    templ = [u for u in ("hifi-net-wpa@.service", "hifi-net-hostapd@.service", "hifi-net-dhcp@.service")
             if any(x.startswith(u.split("@")[0] + "@") for v in units.values() for x in v)]
    r.add("units.net-templates-not-at-boot", not templ, "Wi-Fi client/hotspot/DHCP instances are started by hifi-netd only")
    both = [u for u in ("hostapd.service", "wpa_supplicant.service") if is_enabled(units, u)]
    r.add("units.not-hotspot-and-client", len(both) < 2, "never both hostapd and wpa_supplicant enabled at boot (PRODUCT_SPEC)")
    dns = is_enabled(units, "dnsmasq.service") and is_enabled(units, "systemd-resolved.service")
    r.add("units.no-port-53-conflict", not dns, "dnsmasq and systemd-resolved are not both enabled at boot")
    user_pw = sorted(os.path.basename(p) for p in glob.glob(os.path.join(root, "usr/lib/systemd/user/pipewire*")) +
                     glob.glob(os.path.join(root, "usr/lib/systemd/user/wireplumber*")))
    r.facts["pipewire_user_units"] = user_pw
    sysw = os.path.exists(os.path.join(root, "usr/lib/systemd/system/pipewire.service"))
    r.add("units.pipewire-mode", True, f"system-wide PipeWire unit: {'yes' if sysw else 'NO'}; user units: {', '.join(user_pw) or 'none'} "
          "(docs/SERVICE_SUPERVISION.md: system-wide vs. per-user is open)", info=True)


def systemd_verify(r, root):
    if not shutil.which("systemd-analyze"):
        r.add("units.verify", False, "systemd-analyze not available")
        return
    # unit NAMES (resolved in the image's own search path); templates are verified as an instance
    names = [u.replace("@.", "@wlan0.") for u in OWN_UNITS]
    res = sh("systemd-analyze", "verify", f"--root={root}", "--man=no", "--generators=no", *names)
    out = (res.stdout + res.stderr).strip()
    r.facts["systemd_verify_output"] = out
    # warnings about units of OTHER packages that ours reference are listed, but only messages about our own units fail the check
    stems = [u.split("@")[0].split(".")[0] for u in OWN_UNITS]
    own = [ln for ln in out.splitlines() if any(st in ln for st in stems)]
    r.add("units.verify", res.returncode == 0 and not own,
          f"systemd-analyze verify --root (rc {res.returncode}): " + (" | ".join(out.splitlines()[:12]) or "no messages"))
    for u in ("hifi-bridge.service", "hifi-bridge-boot.service", "hifi-netd.service", "hifi-bridge-health.service"):
        s = sh("systemd-analyze", "security", "--offline=true", f"--root={root}", "--no-pager", u)
        m = re.search(r"Overall exposure level for \S+: ([0-9.]+) (\S+)", s.stdout + s.stderr)
        r.facts.setdefault("exposure", {})[u] = m.group(0) if m else (s.stdout + s.stderr).strip()[-300:]
        r.add(f"units.exposure-{u}", bool(m), m.group(0) if m else "could not compute exposure", info=not m)


def check_network_and_misc(r, root):
    nets = sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, "etc/systemd/network/*")) +
                  glob.glob(os.path.join(root, "usr/lib/systemd/network/*")))
    r.facts["networkd_files"] = nets
    r.add("network.networkd-config", True, "networkd files: " + ", ".join(nets), info=True)
    r.add("network.nft", os.path.exists(resolve(root, "/usr/sbin/nft")), "nft binary (firewall ruleset: network stream)")
    base = os.path.join(root, "usr/share/hifi-bridge/nftables-base.nft")
    text = open(base).read() if os.path.isfile(base) else ""
    r.add("network.firewall-base-ruleset", "policy drop" in text and "input" in text,
          "base ruleset /usr/share/hifi-bridge/nftables-base.nft (default-deny inbound, loaded by hifi-firewall.service before networking)")
    disc = [ln.strip() for ln in text.splitlines() if "sport 3483" in ln]
    r.add("network.firewall-discovery-reply-only", disc == ["udp sport 3483 udp dport 3484 accept"] and "1024-65535" not in text,
          f"Lyrion discovery replies only to the bridge's fixed discovery port (review N-8): {disc}")
    res = os.path.join(root, "etc/systemd/resolved.conf.d/hifi-bridge.conf")
    rv = kv_file(res) if os.path.isfile(res) else {}
    r.add("network.resolved-no-responders", rv.get("LLMNR") == "no" and rv.get("MulticastDNS") == "no",
          f"systemd-resolved: LLMNR={rv.get('LLMNR')}, MulticastDNS={rv.get('MulticastDNS')} (no 5355 responder; Avahi owns mDNS)")
    mods = os.path.join(root, "etc/modules-load.d/hifi-bridge-nftables.conf")
    r.add("network.nft-modules", os.path.isfile(mods), "nftables kernel modules loaded at boot (modules-load.d)")
    ko = glob.glob(os.path.join(root, "usr/lib/modules/*/kernel/net/netfilter/nf_tables.ko*"))
    builtin = any("nf_tables" in open(p).read() for p in glob.glob(os.path.join(root, "usr/lib/modules/*/modules.builtin")))
    r.add("network.nf_tables-in-kernel", bool(ko) or builtin, "nf_tables " + ("module" if ko else "built in" if builtin else "MISSING"))
    pol = os.path.join(root, "etc/dbus-1/system.d/de.pengutronix.rauc-hifi-bridge.conf")
    r.add("rauc.dbus-policy", os.path.isfile(pol) and "hifi-bridge" in open(pol).read(), "RAUC D-Bus policy for the hifi-bridge user")
    for f in ("etc/avahi/avahi-daemon.conf", "etc/systemd/timesyncd.conf.d/hifi-bridge.conf", "usr/lib/tmpfiles.d/hifi-bridge-ota.conf"):
        r.add(f"files.{os.path.basename(f)}", os.path.isfile(os.path.join(root, f)), "/" + f)
    regdb = glob.glob(os.path.join(root, "usr/lib/firmware/regulatory.db*"))
    r.add("network.regdb", bool(regdb), "wireless regulatory database")
    bt = glob.glob(os.path.join(root, "usr/lib/firmware/brcm/BCM*.hcd"))
    wifi = glob.glob(os.path.join(root, "usr/lib/firmware/brcm/brcmfmac*"))
    r.add("network.radio-firmware", bool(bt) and bool(wifi), f"{len(bt)} Bluetooth + {len(wifi)} Wi-Fi firmware names (exact list: "
          "firmware.radio-list)")
    duid = os.path.join(root, "etc/systemd/networkd.conf.d/hifi-bridge.conf")
    r.add("network.dhcp-duid-link-layer", os.path.isfile(duid) and open(duid).read().count("DUIDType=link-layer") == 2,
          "networkd DHCPv4/DHCPv6 DUID from the MAC address (stable despite the transient machine id)")
    mid = os.path.join(root, "etc/machine-id")
    content = open(mid).read().strip() if os.path.isfile(mid) else None
    r.facts["machine_id"] = "absent" if content is None else ("empty" if content == "" else "SET")
    r.add("rootfs.machine-id-not-baked", content in (None, "", "uninitialized"),
          f"/etc/machine-id {r.facts['machine_id']} (must not be shared by all devices; with a read-only /etc systemd uses a transient id per boot)")


def size_bytes(text):
    """systemd size value (16M, 2M, 512K, 1G, plain bytes) -> bytes, or None."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)\s*", text or "")
    return int(m.group(1)) * 1024 ** " KMGT".index(m.group(2) or " ") if m else None


def conf_values(root, main, dropins, section):
    """{key: value} of a systemd configuration file + its drop-ins (later files win), for one section."""
    files = [os.path.join(root, main)] if main else []
    for d in dropins:
        files += sorted(glob.glob(os.path.join(root, d, "*.conf")))
    out = {}
    for f in files:
        if os.path.isfile(f):
            out.update(ini(f).get(section, {}))
    return out


def unit_alias_target(root, unit):
    """The real unit name behind an alias symlink (e.g. dbus-org.bluez.service -> bluetooth.service); the name itself otherwise."""
    for base in ("etc/systemd/system", "usr/lib/systemd/system"):
        p = os.path.join(root, base, unit)
        if os.path.islink(p) and not os.readlink(p).startswith("/dev/null"):
            return os.path.basename(os.readlink(p))
    return unit


def persistent_locations(storage):
    """Directories whose content lives on the card: the data partition, the boot FAT and the bind mounts from /data."""
    return ["/data", "/boot"] + [p["bind"] for p in storage["persistent"] if p.get("bind")]


def under(path, places):
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in places)


def check_persistence(r, root, storage):
    """DECISIONS #121 ("persist only what must survive a reboot"; docs/PERSISTENCE.md, allow-list os/storage.json)."""
    lim = storage["ram_limits"]
    j = conf_values(root, "etc/systemd/journald.conf", ["usr/lib/systemd/journald.conf.d", "etc/systemd/journald.conf.d"], "Journal")
    use, fsz = size_bytes(j.get("RuntimeMaxUse")), size_bytes(j.get("RuntimeMaxFileSize"))
    r.add("persist.journald-volatile", j.get("Storage") == "volatile" and use is not None and use <= size_bytes(lim["journald_runtime_max_use"])
          and fsz is not None and fsz <= use,
          f"journald Storage={j.get('Storage')}, RuntimeMaxUse={j.get('RuntimeMaxUse')}, RuntimeMaxFileSize={j.get('RuntimeMaxFileSize')} "
          f"(RAM only, bounded; limit {lim['journald_runtime_max_use']})")
    r.add("persist.no-persistent-journal-dir", not os.path.lexists(os.path.join(root, "var/log/journal")),
          "no /var/log/journal in the image (journald would store persistently there)")
    mgr = conf_values(root, "etc/systemd/system.conf", ["usr/lib/systemd/system.conf.d", "etc/systemd/system.conf.d"], "Manager")
    sysctl = {}
    for f in sorted(glob.glob(os.path.join(root, "usr/lib/sysctl.d/*.conf")) + glob.glob(os.path.join(root, "etc/sysctl.d/*.conf")),
                    key=os.path.basename):
        sysctl.update(kv_file(f))
    cd = conf_values(root, "etc/systemd/coredump.conf", ["usr/lib/systemd/coredump.conf.d", "etc/systemd/coredump.conf.d"], "Coredump")
    has_cd = os.path.exists(os.path.join(root, "usr/lib/systemd/systemd-coredump"))
    r.add("persist.coredumps-off", mgr.get("DefaultLimitCORE") == "0" and sysctl.get("kernel.core_pattern") == "|/bin/false"
          and (not has_cd or cd.get("Storage") == "none"),
          f"DefaultLimitCORE={mgr.get('DefaultLimitCORE')}, kernel.core_pattern={sysctl.get('kernel.core_pattern')}, systemd-coredump "
          f"{'present, Storage=' + str(cd.get('Storage')) if has_cd else 'absent'}")
    units = merged_units(root)
    py = sorted(u for u, t in units.items() if any("python3" in v for v in unit_values(t, "ExecStart")))
    nobc = [u for u in py if not any("PYTHONDONTWRITEBYTECODE=1" in v.split() for v in unit_values(units[u], "Environment"))]
    r.add("persist.python-no-bytecode-writes", bool(py) and not nobc,
          f"Python units: {', '.join(py)}; " + (f"WITHOUT PYTHONDONTWRITEBYTECODE=1: {', '.join(nobc)}" if nobc else
                                                "all set PYTHONDONTWRITEBYTECODE=1") + " (bytecode is precompiled into the read-only root)")
    allowed = {p["path"] for p in storage["persistent"]}
    fstab = [ln.split() for ln in open(os.path.join(root, "etc/fstab")) if ln.strip() and not ln.startswith("#")]
    binds = {x[0]: x[1] for x in fstab if len(x) > 2 and x[2] == "none" and x[0].startswith("/data")}
    data_unit = units.get("hifi-bridge-data.service", "")
    made = [p for v in unit_values(data_unit, "ExecStart") if v.startswith("/bin/mkdir") for p in v.split()[1:] if p.startswith("/")]
    rauc = ini(os.path.join(root, "etc/rauc/system.conf")).get("system", {}).get("data-directory") \
        if os.path.isfile(os.path.join(root, "etc/rauc/system.conf")) else None
    want_binds = {p["path"]: p["bind"] for p in storage["persistent"] if p.get("bind")}
    used = set(binds) | set(made) | ({rauc} if rauc else set())
    bad = sorted(p for p in used if p not in allowed)
    r.add("persist.data-allowlist", not bad and binds == want_binds and rauc in allowed,
          f"/data paths in use: {', '.join(sorted(used))}; bind mounts {binds}; RAUC data-directory {rauc}"
          + (f"; NOT in os/storage.json: {', '.join(bad)}" if bad else "; all in the allow-list (os/storage.json)"))
    places = persistent_locations(storage)
    ok_pairs = {tuple(x) for x in storage["unit_writers"]["allowed"]}
    writers, offenders = set(), set()
    for u, t in sorted(units.items()):
        u = unit_alias_target(root, u)                         # dbus-org.bluez.service is bluetooth.service
        paths = [p.lstrip("-+:") for v in unit_values(t, "ReadWritePaths") for p in v.split()]
        paths += ["/var/lib/" + s.split(":")[0] for v in unit_values(t, "StateDirectory") for s in v.split()]
        for p in paths:
            if under(p, places):
                (writers if (u, p) in ok_pairs else offenders).add(f"{u}:{p}")
    writers, offenders = sorted(writers), sorted(offenders)
    r.add("persist.unit-write-paths", not offenders, f"units writing persistent locations: {', '.join(writers)}"
          + (f"; NOT allowed: {', '.join(offenders)}" if offenders else " (all allowed by os/storage.json)"))
    bad_tmp = []
    for f in sorted(glob.glob(os.path.join(root, "usr/lib/tmpfiles.d/*.conf")) + glob.glob(os.path.join(root, "etc/tmpfiles.d/*.conf"))):
        for ln in open(f, errors="replace"):
            x = ln.split()
            if len(x) > 1 and not x[0].startswith("#") and under(x[1], places):
                bad_tmp.append(f"{os.path.basename(f)}: {' '.join(x[:2])}")
    r.add("persist.tmpfiles-no-persistent-paths", not bad_tmp, "no tmpfiles.d entry creates or changes anything on /data, /boot or a "
          "bind mount" + (": " + "; ".join(bad_tmp) if bad_tmp else ""))
    br = units.get("hifi-bridge.service", "")
    ex = " ".join(unit_values(br, "ExecStart"))
    r.add("persist.bridge-faults-in-ram", "--run-dir /run/hifi-bridge" in ex and unit_values(br, "RuntimeDirectory") == ["hifi-bridge"],
          "hifi-bridge.service keeps the fault history in its runtime directory /run/hifi-bridge (tmpfs), not on /data")
    src = lambda p: open(os.path.join(root, "usr/lib/hifi-bridge/btbridge", p), errors="replace").read() \
        if os.path.isfile(os.path.join(root, "usr/lib/hifi-bridge/btbridge", p)) else ""  # noqa: E731
    r.add("persist.ota-staging-cleanup", "storage.boot_cleanup(" in src("bootfiles.py") and "def boot_cleanup" in src("storage.py")
          and "cleanup_staging(self.data_dir, at_boot=False)" in src("ota/manager.py"),
          "the boot step removes interrupted uploads/downloads, unreferenced bundles and temporary files; the bridge start removes an "
          "interrupted upload (btbridge.storage, ota.manager.cleanup_staging)")
    ts = conf_values(root, "etc/systemd/timesyncd.conf", ["usr/lib/systemd/timesyncd.conf.d", "etc/systemd/timesyncd.conf.d"], "Time")
    iv = int(ts.get("SaveIntervalSec", "60") or 60)
    r.add("persist.timesync-save-interval", iv >= lim["timesyncd_save_interval_s_min"],
          f"timesyncd saves the clock file on /data every {iv} s (minimum {lim['timesyncd_save_interval_s_min']} s)")


def ext4_numbers(img):
    d = sh("dumpe2fs", "-h", img).stdout
    g = {k: int(v) for k, v in re.findall(r"^(Block count|Free blocks|Reserved block count|Block size|Inode count|Free inodes):\s+(\d+)",
                                          d, re.M)}
    if "Block size" not in g:
        return None
    feats = (re.search(r"^Filesystem features:\s+(.*)$", d, re.M) or [None, ""])[1].split()
    return {"bytes": g["Block count"] * g["Block size"], "block_size": g["Block size"], "inodes": g["Inode count"],
            "free_bytes": g["Free blocks"] * g["Block size"], "reserved_bytes": g["Reserved block count"] * g["Block size"],
            "avail_bytes": (g["Free blocks"] - g["Reserved block count"]) * g["Block size"], "features": feats}


def check_layout(r, image, data_img, storage):
    """DECISIONS #120: boot 64 MiB, slots 384 MiB, data as measured (os/storage.json), the whole card within a nominal 2 GB card; the data
    partition holds one update of a full slot even if it never grew (the update manager's free-space rule)."""
    lay, bud = storage["layout"], storage["data_budget"]
    parts = r.facts.get("partitions", [])
    if len(parts) != 4:
        r.add("size.layout", False, "four partitions expected")
        return
    got = [p["size"] // MIB for p in parts]
    want = [lay["boot_mib"], lay["slot_mib"], lay["slot_mib"], lay["data_initial_mib"]]
    r.add("size.layout", got == want, f"partitions {got} MiB, os/storage.json {want} MiB (boot | A | B | data before growth)")
    total = os.path.getsize(image)
    end = max(p["start"] + p["size"] for p in parts)
    usable = lay["card_target"]["usable_bytes"]
    r.add("size.card-fits-2gb", end <= total <= usable,
          f"card image {total} bytes ({total / MIB:.0f} MiB) <= {usable} bytes, the usable size of a nominal 2 GB card; after growth the "
          f"data partition gets ~{(usable - parts[3]['start']) / MIB:.0f} MiB of such a card")
    if not data_img:
        return
    e = ext4_numbers(data_img)
    if not e:
        r.add("size.data-holds-update", False, "data partition: not an ext4 file system")
        return
    r.facts["data_ext4"] = e
    need = (bud["largest_bundle_mib"] + bud["space_margin_mib"] + bud["state_mib"]) * MIB
    r.add("size.data-holds-update", e["avail_bytes"] >= need,
          f"data partition before growth: {e['avail_bytes'] / MIB:.1f} MiB available to the bridge (of {e['bytes'] / MIB:.0f} MiB; reserved "
          f"{e['reserved_bytes'] / MIB:.1f} MiB, metadata+journal {(e['bytes'] - e['free_bytes']) / MIB:.1f} MiB) >= {need / MIB:.0f} MiB = "
          f"largest bundle {bud['largest_bundle_mib']} + free-space margin {bud['space_margin_mib']} + state {bud['state_mib']}")
    r.add("size.data-ext4-layout", e["block_size"] == 4096 and e["inodes"] <= e["bytes"] // 65536 and "has_journal" in e["features"],
          f"data ext4: {e['block_size']} B blocks, {e['inodes']} inodes, journal: {'has_journal' in e['features']} (grows with the card)")
    ls = sh("debugfs", "-R", "ls -p /", data_img).stdout
    names = sorted({x.split("/")[5] for x in ls.splitlines() if x.count("/") >= 6} - {".", ".."})
    r.add("persist.data-partition-empty", names == ["lost+found"], f"fresh data partition contains: {', '.join(names) or 'nothing'} "
          "(everything else is created on the device by the allow-listed units)")


def check_slot_headroom(r, storage):
    s, parts = r.facts.get("sizes", {}), r.facts.get("partitions", [])
    if "rootfs_ext4_used_bytes" not in s or len(parts) != 4:
        return
    used, slot = s["rootfs_ext4_used_bytes"], parts[1]["size"]
    r.add("size.slot-headroom", used <= 0.8 * slot, f"rootfs uses {used / MIB:.1f} of {slot / MIB:.0f} MiB ({100 * used / slot:.0f} %; "
          f"limit 80 %, the rest is room for later releases in the same slot size)")


def sizes(r, root, boot_dir, image, rootfs_img, file_list):
    s = {"card_image": os.path.getsize(image), "rootfs_tree_bytes": tree_size(root), "boot_files_bytes": tree_size(boot_dir)}
    if rootfs_img:
        s["rootfs_ext4_bytes"] = os.path.getsize(rootfs_img)
        d = sh("dumpe2fs", "-h", rootfs_img).stdout
        g = {k: int(v) for k, v in re.findall(r"^(Block count|Free blocks|Block size|Inode count|Free inodes):\s+(\d+)", d, re.M)}
        s["rootfs_ext4_used_bytes"] = (g["Block count"] - g["Free blocks"]) * g["Block size"]
        s["rootfs_ext4_free_bytes"] = g["Free blocks"] * g["Block size"]
        s["rootfs_inodes_used"] = g["Inode count"] - g["Free inodes"]
        feats = (re.search(r"^Filesystem features:\s+(.*)$", d, re.M) or [None, ""])[1].split()
        r.add("rootfs.ext4-layout", "has_journal" not in feats and g["Block size"] == 4096 and "64bit" not in feats,
              f"read-only slot image: 4 KiB blocks, no journal, no 64bit (features: {' '.join(feats)})")
    kern = os.path.join(root, "boot/Image")
    s["kernel_image_bytes"] = os.path.getsize(kern) if os.path.isfile(kern) else 0
    s["kernel_modules_bytes"] = sum(tree_size(m) for m in glob.glob(os.path.join(root, "usr/lib/modules/*")))
    s["firmware_bytes"] = tree_size(os.path.join(root, "usr/lib/firmware"))
    s["python_stdlib_bytes"] = sum(tree_size(m) for m in glob.glob(os.path.join(root, "usr/lib/python3*")))
    s["bridge_bytes"] = tree_size(os.path.join(root, "usr/lib/hifi-bridge"))
    if file_list and os.path.isfile(file_list):
        per = {}
        for line in open(file_list):
            pkg, _, path = line.rstrip("\n").partition(",")
            p = os.path.join(root, path.lstrip("./"))
            if os.path.isfile(p) and not os.path.islink(p):
                per[pkg] = per.get(pkg, 0) + os.path.getsize(p)
        s["top_packages"] = sorted(per.items(), key=lambda x: -x[1])[:25]
    r.facts["sizes"] = s
    if rootfs_img:
        r.add("size.rootfs-fits", s["rootfs_ext4_free_bytes"] > 0, f"rootfs.ext4 {s['rootfs_ext4_bytes']} bytes, used "
              f"{s['rootfs_ext4_used_bytes']}, free {s['rootfs_ext4_free_bytes']}", info=True)


def write_md(r, path, board, label=None):
    s = r.facts.get("sizes", {})
    mb = lambda b: f"{b / 1048576:.1f} MiB"  # noqa: E731
    lines = [f"# Image check - {board}", ""] + ([f"**Evidence: {label}**", ""] if label else []) + [
             "Generated by `scripts/os_check_image.sh` (os/tools/image_check.py) against the built card image. **BUILD-VERIFIED only: "
             "nothing was booted.** Development build, not hardware-validated.", "",
             f"Result: **{len(r.failed)} FAIL**, {sum(c['status'] == 'PASS' for c in r.checks)} PASS, "
             f"{sum(c['status'] == 'INFO' for c in r.checks)} INFO.", "", "| Check | Status | Detail |", "|---|---|---|"]
    for c in r.checks:
        lines.append(f"| {c['id']} | {c['status']} | {c['detail'].replace('|', '/')[:400]} |")
    lines += ["", "## Partitions", "", "| Node | Type | Start | Size |", "|---|---|---|---|"]
    for p in r.facts.get("partitions", []):
        lines.append(f"| {os.path.basename(p['node'])} | 0x{p['type']} | {p['start']} | {p['size']} ({mb(p['size'])}) |")
    lines += ["", "## Sizes", ""]
    for k, v in s.items():
        if k != "top_packages":
            lines.append(f"* {k}: {v} ({mb(v)})")
    if s.get("top_packages"):
        lines += ["", "Largest target packages (installed, stripped file bytes in the rootfs):", "", "| Package | Size |", "|---|---|"]
        lines += [f"| {p} | {mb(b)} |" for p, b in s["top_packages"]]
    lines += ["", "## Enabled units", ""]
    for k, v in sorted(r.facts.get("enabled_units", {}).items()):
        lines.append(f"* {k}: {', '.join(v)}")
    if r.facts.get("exposure"):
        lines += ["", "## systemd-analyze security (offline)", ""] + [f"* {u}: {e}" for u, e in r.facts["exposure"].items()]
    lines += ["", "## systemd-analyze verify output", "", "```", r.facts.get("systemd_verify_output", "") or "(no messages)", "```"]
    if r.facts.get("uboot_env_matrix_md"):
        lines += ["", "## U-Boot environment corruption matrix (SIMULATED, image's own copies and /etc/u-boot-initial-env)", "",
                  r.facts["uboot_env_matrix_md"]]
    if r.facts.get("uboot_env_matrix_compare_md"):
        lines += ["", "## Comparison: the same matrix with the previous images' uboot-tools fw_printenv (INFO)", "",
                  r.facts["uboot_env_matrix_compare_md"]]
    lines += ["", "## HARDWARE-PENDING (cannot be shown without a board)", ""] + [f"* {h}" for h in HARDWARE_PENDING]
    open(path, "w").write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("board", "image", "manifest", "rootfs", "boot", "config", "json", "md"):
        ap.add_argument(f"--{a}", required=True)
    for a in ("rootfs-img", "slot-a", "dev-ca", "file-list", "uboot-config", "bootargs", "fw-printenv", "boot-script", "data-img",
              "uboot-bin", "compare-fw-printenv", "label"):
        ap.add_argument(f"--{a}")
    ap.add_argument("--storage", default=STORAGE, help="layout + allow-list of persistent paths (default: os/storage.json)")
    a = ap.parse_args(argv)
    storage = json.load(open(a.storage))
    r = Report()
    cfgv = cfg_values(a.config)
    man = json.load(open(a.manifest))
    r.facts["manifest"] = man
    check_identity(r, a.rootfs, a.board)
    check_partitions(r, a.image, a.rootfs_img, a.slot_a)
    check_manifest(r, a.image, man, a.boot)
    check_boot_partition(r, a.boot, a.board, man)
    check_kernel(r, a.rootfs, a.boot)
    check_fstab_ro(r, a.rootfs, a.bootargs or "")
    check_rauc(r, a.rootfs, cfgv, a.dev_ca)
    check_uboot_env(r, a.rootfs, a.boot, a.uboot_config, a.fw_printenv, a.boot_script,
                    a.uboot_bin or os.path.join(a.boot, "u-boot.bin"), a.compare_fw_printenv)
    check_env_tools(r, a.rootfs, a.file_list)
    check_radio_firmware(r, a.rootfs, a.boot, cfgv.get("BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE"))
    check_boot_fat_access(r, a.rootfs)
    check_users(r, a.rootfs)
    check_setuid(r, a.rootfs)
    check_bridge(r, a.rootfs)
    check_audio_ingress(r, a.rootfs)
    check_units(r, a.rootfs, enabled_units(a.rootfs))
    systemd_verify(r, a.rootfs)
    check_network_and_misc(r, a.rootfs)
    check_persistence(r, a.rootfs, storage)
    check_layout(r, a.image, a.data_img, storage)
    sizes(r, a.rootfs, a.boot, a.image, a.rootfs_img, a.file_list)
    check_slot_headroom(r, storage)
    json.dump({"board": a.board, "checks": r.checks, "facts": r.facts, "hardware_pending": HARDWARE_PENDING}, open(a.json, "w"), indent=1)
    write_md(r, a.md, a.board, a.label)
    for c in r.checks:
        if c["status"] != "PASS":
            print(f"{c['status']:4} {c['id']}: {c['detail'][:300]}")
    print(f"image check {a.board}: {len(r.failed)} FAIL, {sum(c['status'] == 'PASS' for c in r.checks)} PASS, "
          f"{sum(c['status'] == 'INFO' for c in r.checks)} INFO")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
