"""UNIT: target-disk safety policy with RECORDED tool outputs (lsblk / diskutil / PowerShell). The platform commands are never executed."""
import json
import plistlib
import subprocess

import pytest

from hififlash import disks as D

GB = 1000 ** 3
LSBLK = json.dumps({"blockdevices": [
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 512 * GB, "model": "Samsung SSD", "serial": "S1", "tran": "nvme",
     "rm": False, "ro": False, "hotplug": False, "mountpoints": [None],
     "children": [{"name": "nvme0n1p2", "type": "part", "mountpoints": ["/"]}, {"name": "nvme0n1p1", "type": "part", "mountpoints": ["/boot/efi"]}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 32 * GB, "model": "SD/MMC Reader", "serial": "R1", "tran": "usb",
     "rm": True, "ro": False, "hotplug": True, "mountpoints": [None], "children": [{"name": "sdb1", "type": "part", "mountpoints": ["/media/u/BOOT"]}]},
    {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": 2000 * GB, "model": "USB HDD", "serial": "H1", "tran": "usb",
     "rm": False, "ro": False, "hotplug": True, "mountpoints": [None]},
    {"name": "sdd", "path": "/dev/sdd", "type": "disk", "size": 16 * GB, "model": "Locked SD", "serial": "L1", "tran": "usb",
     "rm": True, "ro": True, "hotplug": True, "mountpoints": [None]},
    {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 512 * 1000 ** 2, "model": "", "serial": "T1", "tran": "",
     "rm": True, "ro": False, "hotplug": False, "mountpoints": [None]},
    {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": GB, "mountpoints": ["/snap/x"]},
]})


@pytest.fixture(autouse=True)
def never_run_commands(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to run a host command - not allowed (CLAUDE.md)")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def by(disks):
    return {d.name: d for d in disks}


def test_linux_policy_offers_only_the_sd_card():
    d = by(D.parse_lsblk(LSBLK))
    assert set(d) == {"nvme0n1", "sdb", "sdc", "sdd", "mmcblk0"}               # loop devices are not disks
    assert {x.name for x in D.eligible(list(d.values()), 300 * 1000 ** 2)} == {"sdb"}
    assert "system_disk" in D.problems(d["nvme0n1"], 1) and "not_removable" in D.problems(d["nvme0n1"], 1)
    assert D.problems(d["sdc"], 1) == ["unusually_large_confirm_explicitly"] and D.problems(d["sdc"], 1, allow_large=True) == []
    assert "read_only" in D.problems(d["sdd"], 1) and "too_small_for_product" in D.problems(d["mmcblk0"], 1)
    assert "smaller_than_image" in D.problems(d["sdb"], 40 * GB)


def test_typed_confirmation_and_identity_recheck():
    sd = by(D.parse_lsblk(LSBLK))["sdb"]
    assert D.confirm_token(sd) == "sdb" and D.confirmed(sd, " sdb ") and not D.confirmed(sd, "y") and not D.confirmed(sd, "sdc")
    swapped = D.Disk(path="/dev/sdb", name="sdb", size=64 * GB, serial="R2")
    assert D.same_disk(sd, sd) and not D.same_disk(sd, swapped)


def test_macos_and_windows_parsers():
    lst = plistlib.dumps({"AllDisksAndPartitions": [
        {"DeviceIdentifier": "disk0", "Partitions": [{"MountPoint": "/System/Volumes/Data"}]},
        {"DeviceIdentifier": "disk4", "Partitions": [{"MountPoint": "/Volumes/BOOT"}]}]})
    infos = {"disk0": plistlib.dumps({"TotalSize": 500 * GB, "Internal": True, "RemovableMedia": False, "WritableMedia": True, "BusProtocol": "Apple Fabric"}),
             "disk4": plistlib.dumps({"TotalSize": 32 * GB, "Internal": False, "RemovableMedia": True, "WritableMedia": True,
                                      "BusProtocol": "Secure Digital", "MediaName": "SD Card"})}
    mac = by(D.parse_diskutil(lst, infos))
    assert [x.name for x in D.eligible(list(mac.values()), GB)] == ["disk4"] and mac["disk4"].transport == "sd" and mac["disk0"].system
    win = by(D.parse_windows(json.dumps([
        {"Number": 0, "FriendlyName": "NVMe", "SerialNumber": "A", "Size": 512 * GB, "BusType": "NVMe", "IsBoot": True, "IsSystem": True,
         "IsReadOnly": False, "DriveLetters": "C"},
        {"Number": 3, "FriendlyName": "SD Card", "SerialNumber": "B", "Size": 32 * GB, "BusType": "SD", "IsBoot": False, "IsSystem": False,
         "IsReadOnly": False, "DriveLetters": "E"}])))
    assert [x.name for x in D.eligible(list(win.values()), GB)] == ["PhysicalDrive3"] and win["PhysicalDrive3"].path == "\\\\.\\PhysicalDrive3"
    single = D.parse_windows(json.dumps({"Number": 5, "Size": 16 * GB, "BusType": "USB", "IsReadOnly": False}))
    assert single[0].name == "PhysicalDrive5" and single[0].removable


def test_list_disks_uses_only_the_injected_runner():
    calls = []
    def runner(argv):
        calls.append(argv)
        return LSBLK.encode()
    assert len(D.list_disks("linux", runner)) == 5 and calls == [D.COMMANDS["linux"]]
    with pytest.raises(ValueError):
        D.list_disks("plan9", runner)


def test_list_disks_for_macos_and_windows_via_the_runner():
    lst = plistlib.dumps({"AllDisksAndPartitions": [{"DeviceIdentifier": "disk4", "Partitions": []}]})
    info = plistlib.dumps({"TotalSize": 32 * GB, "RemovableMedia": True, "WritableMedia": True, "Internal": False})
    calls = []
    def mac(argv):
        calls.append(argv)
        return lst if argv == D.COMMANDS["darwin"] else info
    assert [d.name for d in D.list_disks("darwin", mac)] == ["disk4"] and calls[1] == ["diskutil", "info", "-plist", "disk4"]
    win = json.dumps({"Number": 2, "Size": 32 * GB, "BusType": "SD", "IsReadOnly": False}).encode()
    assert D.list_disks("windows", lambda argv: win)[0].label().startswith("PhysicalDrive2 - unknown model - 32.0 GB")


# ---- review M11 (2026-11-18): nested holders and APFS (recorded outputs only) ----
NESTED = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 128 * GB, "model": "Portable SSD", "serial": "S1", "tran": "usb",
     "rm": False, "ro": False, "hotplug": True, "mountpoints": [None],
     "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "mountpoints": ["/boot/efi"]},
         {"name": "sda2", "path": "/dev/sda2", "type": "part", "mountpoints": [None],
          "children": [{"name": "luks-root", "path": "/dev/mapper/luks-root", "type": "crypt", "mountpoints": [None],
                        "children": [{"name": "vg-root", "path": "/dev/mapper/vg-root", "type": "lvm", "mountpoints": ["/"]},
                                     {"name": "vg-home", "path": "/dev/mapper/vg-home", "type": "lvm", "mountpoints": ["/home"]}]}]}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 64 * GB, "model": "USB Backup", "serial": "S2", "tran": "usb",
     "rm": True, "ro": False, "hotplug": True, "mountpoints": [None],
     "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "mountpoints": ["/media/user/Backup"]}]},
    {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": 32 * GB, "model": "Stick", "serial": "S3", "tran": "usb",
     "rm": True, "ro": False, "hotplug": True, "mountpoints": [None],
     "children": [{"name": "sdc1", "path": "/dev/sdc1", "type": "part", "mountpoints": ["[SWAP]"]}]},
    {"name": "sdd", "path": "/dev/sdd", "type": "disk", "size": 32 * GB, "model": "Crypt stick", "serial": "S4", "tran": "usb",
     "rm": True, "ro": False, "hotplug": True, "mountpoints": [None],
     "children": [{"name": "sdd1", "path": "/dev/sdd1", "type": "part", "mountpoints": [None],
                   "children": [{"name": "luks-x", "path": "/dev/mapper/luks-x", "type": "crypt", "mountpoints": [None]}]}]}]}


def test_m11_nested_luks_lvm_root_is_the_system_disk_even_without_a_separate_efi_mount():
    d = by(D.parse_lsblk(json.dumps(NESTED)))
    assert "system_disk" in D.problems(d["sda"], GB) and set(d["sda"].mounts) == {"/boot/efi", "/", "/home"}
    no_efi = json.loads(json.dumps(NESTED))
    no_efi["blockdevices"][0]["children"] = no_efi["blockdevices"][0]["children"][1:]
    sda = D.parse_lsblk(json.dumps(no_efi))[0]
    assert sda.system and "system_disk" in D.problems(sda, GB)


def test_m11_mounted_data_disk_is_offered_only_with_its_mounts_known_swap_and_stacked_devices_are_refused():
    d = by(D.parse_lsblk(json.dumps(NESTED)))
    assert D.problems(d["sdb"], GB) == [] and d["sdb"].mounts == ("/media/user/Backup",)      # the helper unmounts it before writing
    assert "system_disk" in D.problems(d["sdc"], GB)                                           # active swap = the running system
    assert D.problems(d["sdd"], GB) == ["in_use_by_volume_manager"] and d["sdd"].stacked      # an open LUKS/LVM/RAID holder
    assert {x.name for x in D.eligible(list(d.values()), GB)} == {"sdb"}


def mac_outputs(boot_on_external: bool):
    physical = plistlib.dumps({"AllDisksAndPartitions": [
        {"DeviceIdentifier": "disk0", "Partitions": [{"DeviceIdentifier": "disk0s1"}, {"DeviceIdentifier": "disk0s2"}]},
        {"DeviceIdentifier": "disk4", "Partitions": [{"DeviceIdentifier": "disk4s1"}, {"DeviceIdentifier": "disk4s2"}]},
        {"DeviceIdentifier": "disk6", "Partitions": [{"DeviceIdentifier": "disk6s1", "MountPoint": "/Volumes/BOOT"}]}]})
    every = plistlib.dumps({"AllDisksAndPartitions": [
        {"DeviceIdentifier": "disk0", "Partitions": []}, {"DeviceIdentifier": "disk4", "Partitions": []},
        {"DeviceIdentifier": "disk3", "APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
         "APFSVolumes": [{"DeviceIdentifier": "disk3s1", "MountPoint": "/Volumes/Old Mac" if boot_on_external else "/System/Volumes/Data"}]},
        {"DeviceIdentifier": "disk5", "APFSPhysicalStores": [{"DeviceIdentifier": "disk4s2"}],
         "APFSVolumes": [{"DeviceIdentifier": "disk5s1", "MountPoint": "/System/Volumes/Data" if boot_on_external else "/Volumes/T7"},
                         {"DeviceIdentifier": "disk5s2"}]},
        {"DeviceIdentifier": "disk6", "Partitions": [{"DeviceIdentifier": "disk6s1", "MountPoint": "/Volumes/BOOT"}]}]})
    info = {"disk0": {"TotalSize": 500 * GB, "Internal": True, "RemovableMedia": False, "WritableMedia": True, "BusProtocol": "Apple Fabric"},
            "disk4": {"TotalSize": 128 * GB, "Internal": False, "RemovableMedia": False, "Ejectable": True, "WritableMedia": True,
                      "BusProtocol": "USB", "MediaName": "Samsung T7"},
            "disk6": {"TotalSize": 32 * GB, "Internal": False, "RemovableMedia": True, "WritableMedia": True, "BusProtocol": "Secure Digital"}}
    root = {"ParentWholeDisk": "disk5" if boot_on_external else "disk3", "MountPoint": "/"}
    return physical, every, {k: plistlib.dumps(v) for k, v in info.items()}, plistlib.dumps(root)


@pytest.mark.parametrize("external_boot", [True, False])
def test_m11_macos_boot_disk_behind_an_apfs_container_is_never_offered(external_boot):
    physical, every, infos, root = mac_outputs(external_boot)
    mac = by(D.parse_diskutil(physical, infos, every, root))
    if external_boot:                                       # booted from the external USB SSD: it is the system disk
        assert mac["disk4"].system and "system_disk" in D.problems(mac["disk4"], GB) and "/System/Volumes/Data" in mac["disk4"].mounts
        assert not mac["disk0"].system
    else:                                                   # the external SSD carries a mounted APFS volume: known, unmounted later
        assert mac["disk0"].system and not mac["disk4"].system and mac["disk4"].mounts == ("/Volumes/T7",)
    assert "disk6" in {d.name for d in D.eligible(list(mac.values()), GB)} and mac["disk6"].mounts == ("/Volumes/BOOT",)
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv == D.COMMANDS["darwin"]:
            return physical
        if argv == D.DARWIN_ALL:
            return every
        return root if argv[-1] == "/" else infos[argv[-1]]
    listed = by(D.list_disks("darwin", runner))
    assert listed["disk4"].system == external_boot and ["diskutil", "info", "-plist", "/"] in calls
    assert D.apfs_containers_on(every, "disk4") == ["disk5"] and D.apfs_containers_on(every, "disk6") == []
    assert D.whole_disk("disk4s2") == "disk4" and D.whole_disk("disk12") == "disk12"


def test_a_real_2_gb_card_is_accepted_and_the_limit_matches_the_card_layout():
    """DECISIONS #120: the technical target is a nominal 2 GB card (real ones report 1.9-1.98*10^9 bytes), 16 GB is only a
    recommendation. The limit is the usable size os/storage.json assumes for a 2 GB card, which the whole image must fit in."""
    import os
    storage = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "os", "storage.json")))
    assert D.MIN_BYTES == storage["layout"]["card_target"]["usable_bytes"]
    card = lambda size: D.Disk(path="/dev/sdx", name="sdx", size=size, transport="usb", removable=True)  # noqa: E731
    image = 1092 * 1024 ** 2                                              # 4 + 64 + 2 x 384 + 256 MiB
    assert D.problems(card(1_931_476_992), image) == []                  # a real "2 GB" card
    assert D.problems(card(16 * GB), image) == []
    assert D.problems(card(1_500_000_000), image) == ["too_small_for_product"]
    assert D.problems(card(1_000_000_000), image) == ["too_small_for_product", "smaller_than_image"]
