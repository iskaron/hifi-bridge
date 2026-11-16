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
