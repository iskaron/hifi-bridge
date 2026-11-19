"""Static checks of a BUILT Hi-Fi Bridge card image (Python stdlib + host tools in btbridge-os). Hardware-free: nothing is booted.

    python3 image_check.py --board B --image IMG --manifest M --rootfs DIR --boot DIR --config .config \
        [--rootfs-img rootfs.ext4] [--dev-ca ca.pem] [--file-list packages-file-list.txt] --json OUT --md OUT

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

OWN_UNITS = ["hifi-bridge.service", "hifi-bridge-boot.service", "hifi-bridge-data.service", "hifi-bridge-growdata.service",
             "hifi-bridge-health.service", "hifi-bridge-reboot.path", "hifi-bridge-reboot.service", "hifi-firewall.service",
             "hifi-netd.service", "hifi-net-wpa@.service", "hifi-net-hostapd@.service", "hifi-net-dhcp@.service"]
# enabled at boot by post-build.sh (template units are started by hifi-netd on demand, never at boot)
ENABLED_AT_BOOT = ["hifi-bridge.service", "hifi-bridge-boot.service", "hifi-bridge-data.service", "hifi-bridge-growdata.service",
                   "hifi-bridge-health.service", "hifi-bridge-reboot.path", "hifi-firewall.service", "hifi-netd.service"]
HARDWARE_PENDING = [
    "boots: firmware -> U-Boot -> kernel from slot A -> multi-user.target (G2 P1-2)",
    "U-Boot reads uboot.env from the FAT; fw_printenv/fw_setenv agree with it (G2 P3-1)",
    "RAUC install into slot B, boot-count switch, rollback, power cuts (G2 P3-*)",
    "read-only root really stays read-only at runtime; /var overlay + /data bind mounts persist pairings across reboots (G2 P4-2)",
    "data partition grows to the card size on first boot (G2 P4-1)",
    "Wi-Fi/Bluetooth firmware loads (brcmfmac, hci_uart on PL011) and A2DP is stable (G2 P1-3)",
    "provisioning file is processed and scrubbed on real vfat (G2 P6-3)",
    "RAM/CPU at idle and while playing (G2 P4-4)",
]


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
    lic = os.path.join(boot_dir, "licenses")
    want = ["NOTICE.txt", "rpi-firmware.LICENCE.broadcom", "brcmfmac_sdio-firmware-rpi.LICENSE", "SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt"]
    have = sorted(os.listdir(lic)) if os.path.isdir(lic) else []
    r.add("boot.firmware-licences", all(w in have for w in want), "licenses/ on the FAT: " + ", ".join(have))
    for f in files:
        if f.startswith("uboot.env"):
            r.add("boot.no-preset-env", False, "uboot.env must not ship (created by U-Boot on first boot)")


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
    fwenv = os.path.join(root, "etc/fw_env.config")
    r.add("rauc.fw-env", os.path.isfile(fwenv) and open(fwenv).read().split()[:1] == ["/boot/uboot.env"],
          open(fwenv).read().strip() if os.path.isfile(fwenv) else "missing")
    for b in ("usr/bin/rauc", "usr/bin/fw_printenv", "usr/bin/fw_setenv"):
        r.add(f"rauc.binary-{os.path.basename(b)}", os.path.exists(resolve(root, "/" + b)), "/" + b)


def check_uboot_env_size(r, uboot_config, fwenv_line):
    if not uboot_config or not os.path.isfile(uboot_config):
        return
    v = cfg_values(uboot_config)
    size = v.get("CONFIG_ENV_SIZE")
    r.facts["uboot_env"] = {k: v.get(k) for k in ("CONFIG_ENV_SIZE", "CONFIG_ENV_IS_IN_FAT", "CONFIG_ENV_FAT_FILE",
                                                   "CONFIG_ENV_FAT_INTERFACE", "CONFIG_ENV_FAT_DEVICE_AND_PART", "CONFIG_SYS_REDUNDAND_ENVIRONMENT")}
    fw = fwenv_line.split()
    ok = size is not None and len(fw) >= 3 and int(size, 16) == int(fw[2], 16) and v.get("CONFIG_ENV_IS_IN_FAT") == "y" \
        and v.get("CONFIG_ENV_FAT_FILE", "uboot.env") == os.path.basename(fw[0])
    r.add("rauc.uboot-env-matches-fw_env", ok, f"U-Boot CONFIG_ENV_SIZE={size}, file {v.get('CONFIG_ENV_FAT_FILE')}; fw_env.config: {' '.join(fw)}")


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
    r.facts["radio_firmware"] = sorted(os.path.basename(p) for p in bt + wifi)
    r.add("network.radio-firmware", bool(bt) and bool(wifi), f"{len(bt)} Bluetooth + {len(wifi)} Wi-Fi firmware files")
    mid = os.path.join(root, "etc/machine-id")
    content = open(mid).read().strip() if os.path.isfile(mid) else None
    r.facts["machine_id"] = "absent" if content is None else ("empty" if content == "" else "SET")
    r.add("rootfs.machine-id-not-baked", content in (None, "", "uninitialized"),
          f"/etc/machine-id {r.facts['machine_id']} (must not be shared by all devices; with a read-only /etc systemd uses a transient id per boot)")


def sizes(r, root, boot_dir, image, rootfs_img, file_list):
    s = {"card_image": os.path.getsize(image), "rootfs_tree_bytes": tree_size(root), "boot_files_bytes": tree_size(boot_dir)}
    if rootfs_img:
        s["rootfs_ext4_bytes"] = os.path.getsize(rootfs_img)
        d = sh("dumpe2fs", "-h", rootfs_img).stdout
        g = {k: int(v) for k, v in re.findall(r"^(Block count|Free blocks|Block size|Inode count|Free inodes):\s+(\d+)", d, re.M)}
        s["rootfs_ext4_used_bytes"] = (g["Block count"] - g["Free blocks"]) * g["Block size"]
        s["rootfs_ext4_free_bytes"] = g["Free blocks"] * g["Block size"]
        s["rootfs_inodes_used"] = g["Inode count"] - g["Free inodes"]
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


def write_md(r, path, board):
    s = r.facts.get("sizes", {})
    mb = lambda b: f"{b / 1048576:.1f} MiB"  # noqa: E731
    lines = [f"# Image check - {board}", "",
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
    lines += ["", "## HARDWARE-PENDING (cannot be shown without a board)", ""] + [f"* {h}" for h in HARDWARE_PENDING]
    open(path, "w").write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("board", "image", "manifest", "rootfs", "boot", "config", "json", "md"):
        ap.add_argument(f"--{a}", required=True)
    for a in ("rootfs-img", "slot-a", "dev-ca", "file-list", "uboot-config", "bootargs"):
        ap.add_argument(f"--{a}")
    a = ap.parse_args(argv)
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
    fwenv = os.path.join(a.rootfs, "etc/fw_env.config")
    check_uboot_env_size(r, a.uboot_config, open(fwenv).read() if os.path.isfile(fwenv) else "")
    check_users(r, a.rootfs)
    check_setuid(r, a.rootfs)
    check_bridge(r, a.rootfs)
    check_units(r, a.rootfs, enabled_units(a.rootfs))
    systemd_verify(r, a.rootfs)
    check_network_and_misc(r, a.rootfs)
    sizes(r, a.rootfs, a.boot, a.image, a.rootfs_img, a.file_list)
    json.dump({"board": a.board, "checks": r.checks, "facts": r.facts, "hardware_pending": HARDWARE_PENDING}, open(a.json, "w"), indent=1)
    write_md(r, a.md, a.board)
    for c in r.checks:
        if c["status"] != "PASS":
            print(f"{c['status']:4} {c['id']}: {c['detail'][:300]}")
    print(f"image check {a.board}: {len(r.failed)} FAIL, {sum(c['status'] == 'PASS' for c in r.checks)} PASS, "
          f"{sum(c['status'] == 'INFO' for c in r.checks)} INFO")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
