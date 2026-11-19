"""UNIT/INTEGRATION of the platform disk adapters with RECORDED tool outputs and SIMULATED cards: the real Linux / macOS / Windows adapter
code runs end to end through the helper server, but every system call goes to an injected fake (lsblk/diskutil/PowerShell outputs,
fstat/ioctl/fcntl, kernel32). The "device" behind them is a regular file in tmp. No process is started, no device is opened."""
import json
import os
import plistlib
import stat
import struct
import sys
import threading
import types

import pytest

import flashfixtures as F
from btbridge import provisioning as P
from hififlash import provision as FP
from hififlash import writer as W
from hififlash.helper import client as CL
from hififlash.helper import protocol as PR
from hififlash.platforms import adapter_for, run_command
from hififlash.platforms import posixio as PX
from hififlash.platforms import windows as WIN
from hififlash.platforms.blockio import BlockTarget
from hififlash.platforms.darwin import MacAdapter
from hififlash.platforms.linux import LinuxAdapter
from hififlash.platforms.simulated import SimulatedAdapter, create

pytestmark = pytest.mark.usefixtures("no_subprocess")
GB = 1000 ** 3
CARD = 2 * GB


def card_file(tmp_path, size=CARD):
    p = tmp_path / "device.bin"
    with open(p, "wb") as fh:
        fh.truncate(size)
    return str(p)


class FakeOS:
    """The real ``os`` for I/O on a regular file, but ``open`` maps the device path to that file and ``fstat`` reports a device node."""

    def __init__(self, devmap: dict[str, str], mode=stat.S_IFBLK):
        self.devmap, self.mode = devmap, mode
        self.opened, self.advice = [], []
        self.short_writes = 0

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags):
        self.opened.append((path, flags))
        return os.open(self.devmap[path], os.O_RDWR)

    def fstat(self, fd):
        return types.SimpleNamespace(st_mode=self.mode | 0o660)

    def pwrite(self, fd, data, off):
        if self.short_writes:                                     # the kernel may accept less than asked: the loop must continue
            self.short_writes -= 1
            return os.pwrite(fd, bytes(data[:100]), off)
        return os.pwrite(fd, data, off)

    def posix_fadvise(self, fd, off, n, advice):
        self.advice.append(advice)


def fake_ioctl(size, sector=512, log=None, fail=()):
    def ioctl(fd, req, arg=0):
        if log is not None:
            log.append(req)
        if req in fail:
            raise OSError(25, "Inappropriate ioctl for device")
        return {PX.BLKGETSIZE64: struct.pack("=Q", size), PX.BLKSSZGET: struct.pack("=i", sector),
                PX.DKIOCGETBLOCKSIZE: struct.pack("=I", sector), PX.DKIOCGETBLOCKCOUNT: struct.pack("=Q", size // sector)}.get(req, 0)
    return ioctl


def lsblk(mounted=True):
    return json.dumps({"blockdevices": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 512 * GB, "model": "SSD", "serial": "S1", "tran": "nvme",
         "rm": False, "ro": False, "hotplug": False, "mountpoints": [None],
         "children": [{"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "mountpoints": ["/"]}]},
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": CARD, "model": "SD Reader", "serial": "R1", "tran": "usb",
         "rm": True, "ro": False, "hotplug": True, "mountpoints": [None],
         "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "mountpoints": ["/media/u/BOOT"] if mounted else [None]},
                      {"name": "sdb2", "type": "part", "mountpoints": ["/media/u/root", "/media/u/x"] if mounted else []}]}]}).encode()


def request_for(disk_path, name, serial, im, prov=True):
    doc = P.render(FP.new_document("DE")[0]) if prov else None
    return {"v": 1, "op": "write", "disk": {"path": disk_path, "name": name, "size": CARD, "serial": serial}, "allow_large": False,
            "image": im["path"], "manifest": im["manifest"], "provisioning": base64(doc)}, doc


def base64(b):
    import base64 as b64
    return b64.b64encode(b).decode() if b is not None else None


# ------------------------------------------------------------------ Linux
def test_linux_adapter_end_to_end_through_the_helper(tmp_path):
    dev = card_file(tmp_path)
    im = F.image(tmp_path)
    state = {"mounted": True}
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[0] == "umount":
            state["mounted"] = False
            return b""
        return lsblk(state["mounted"])
    fos, log = FakeOS({"/dev/sdb": dev}), []
    adapter = LinuxAdapter(runner=runner, io_factory=lambda p: PX.PosixDeviceIO(p, "linux", osmod=fos, ioctl=fake_ioctl(CARD, log=log),
                                                                                  fcntl_fn=None))
    req, doc = request_for("/dev/sdb", "sdb", "R1", im)
    result = CL.run_session(CL.inprocess(adapter, "linux"), req)
    assert result["provisioned"] is True
    assert [c for c in calls if c[0] == "umount"] == [["umount", "/dev/sdb1"], ["umount", "/dev/sdb2"]]
    assert fos.opened[0] == ("/dev/sdb", os.O_RDWR | os.O_CLOEXEC | os.O_EXCL)                # exclusive open
    assert PX.BLKFLSBUF in log and fos.advice and log[-1] == PX.BLKRRPART                     # cache dropped; table re-read
    with open(dev, "rb") as fh:
        raw = fh.read(im["manifest"]["size"])
    off = im["manifest"]["slot"]["offset"]
    assert raw[off:off + P.SLOT_SIZE] == doc and raw[:off] == im["data"][:off]


def test_linux_refuses_the_system_disk_and_reports_a_busy_card(tmp_path):
    im = F.image(tmp_path)
    adapter = LinuxAdapter(runner=lambda argv: lsblk(True), io_factory=None)
    req, _ = request_for("/dev/nvme0n1", "nvme0n1", "S1", im)
    req["disk"]["size"] = 512 * GB
    with pytest.raises(PR.ProtocolError):                                  # nvme is not even a valid target path for the helper
        PR.validate_request(req, "linux")
    req, _ = request_for("/dev/sdb", "sdb", "R1", im)
    with pytest.raises(CL.HelperFailed) as e:                               # umount "succeeds" but the card stays mounted
        CL.run_session(CL.inprocess(adapter, "linux"), req)
    assert e.value.code == "unmount_failed" and "/media/u/BOOT" in e.value.message


def test_linux_refuses_a_device_that_changed_size_after_listing(tmp_path):
    dev = card_file(tmp_path)
    adapter = LinuxAdapter(runner=lambda argv: lsblk(False),
                           io_factory=lambda p: PX.PosixDeviceIO(p, "linux", osmod=FakeOS({"/dev/sdb": dev}), ioctl=fake_ioctl(CARD // 2)))
    req, _ = request_for("/dev/sdb", "sdb", "R1", F.image(tmp_path))
    with pytest.raises(CL.HelperFailed) as e:
        CL.run_session(CL.inprocess(adapter, "linux"), req)
    assert e.value.code == "open_failed" and "another size" in e.value.message


def test_posix_io_details(tmp_path):
    dev = card_file(tmp_path, 8 * F.MB)
    with pytest.raises(ValueError):
        PX.PosixDeviceIO(dev, "win32")
    with pytest.raises(W.WriteError, match="not a block device"):                       # a regular file is refused (real fstat)
        PX.PosixDeviceIO(dev, "linux", osmod=type("O", (FakeOS,), {"fstat": lambda self, fd: os.fstat(fd)})({dev: dev}))
    with pytest.raises(W.WriteError, match="implausible"):
        PX.PosixDeviceIO(dev, "linux", osmod=FakeOS({dev: dev}), ioctl=fake_ioctl(8 * F.MB, sector=520))
    with pytest.raises(OSError):
        PX.PosixDeviceIO(dev, "linux", osmod=FakeOS({dev: dev}), ioctl=fake_ioctl(8 * F.MB, fail=(PX.BLKGETSIZE64,)))
    fos = FakeOS({dev: dev})
    io_ = PX.PosixDeviceIO(dev, "linux", osmod=fos, ioctl=fake_ioctl(8 * F.MB, fail=(PX.BLKRRPART,)))
    fos.short_writes = 3
    io_.pwrite(0, b"a" * 1000)
    assert io_.pread(0, 1000) == b"a" * 1000 and io_.pread(8 * F.MB - 10, 100) == bytes(10)    # short read at the end
    io_.sync(); io_.drop_cache()
    io_.close(); io_.close()                                                            # BLKRRPART failure is ignored; idempotent
    zero = FakeOS({dev: dev})
    zero.pwrite = lambda fd, data, off: 0
    io2 = PX.PosixDeviceIO(dev, "linux", osmod=zero, ioctl=fake_ioctl(8 * F.MB))
    with pytest.raises(OSError, match="accepted no data"):
        io2.pwrite(0, b"x")
    io2.close()
    # the real ioctl/fcntl wrappers, exercised harmlessly on a regular file
    fd = os.open(dev, os.O_RDONLY)
    try:
        with pytest.raises(OSError):
            PX._real_ioctl(fd, PX.BLKGETSIZE64, bytes(8))
        assert PX._real_fcntl(fd, 3) & os.O_ACCMODE == os.O_RDONLY                       # F_GETFL
    finally:
        os.close(fd)


# ------------------------------------------------------------------ macOS
def test_macos_adapter_end_to_end(tmp_path):
    dev = card_file(tmp_path)
    im = F.image(tmp_path)
    state = {"mounted": True, "unmount_works": True}

    def listing(physical: bool):
        parts = [{"DeviceIdentifier": "disk4s1", "MountPoint": "/Volumes/BOOT"}] if state["mounted"] else [{"DeviceIdentifier": "disk4s1"}]
        entries = [{"DeviceIdentifier": "disk0", "Partitions": [{"DeviceIdentifier": "disk0s2"}]},
                   {"DeviceIdentifier": "disk4", "Partitions": parts + [{"DeviceIdentifier": "disk4s2"}]}]
        if not physical:                                   # synthesized APFS containers: the Mac's own (disk3) and one on the card
            entries += [{"DeviceIdentifier": "disk3", "APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
                         "APFSVolumes": [{"MountPoint": "/System/Volumes/Data"}]},
                        {"DeviceIdentifier": "disk7", "APFSPhysicalStores": [{"DeviceIdentifier": "disk4s2"}],
                         "APFSVolumes": [{"MountPoint": "/Volumes/Card APFS"}] if state["mounted"] else [{}]}]
        return plistlib.dumps({"AllDisksAndPartitions": entries})
    infos = {"disk0": plistlib.dumps({"TotalSize": 500 * GB, "Internal": True, "WritableMedia": True}),
             "disk4": plistlib.dumps({"TotalSize": CARD, "Internal": False, "RemovableMedia": True, "WritableMedia": True,
                                      "BusProtocol": "Secure Digital", "MediaName": "SD", "DiskUUID": "U4"}),
             "/": plistlib.dumps({"ParentWholeDisk": "disk3"})}
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv == ["diskutil", "list", "-plist", "physical"]:
            return listing(True)
        if argv == ["diskutil", "list", "-plist"]:
            return listing(False)
        if argv[:2] == ["diskutil", "info"]:
            return infos[argv[-1]]
        if argv == ["diskutil", "unmountDisk", "/dev/disk4"] and state["unmount_works"]:
            state["mounted"] = False
        return b"Unmount of all volumes was successful"
    fos, log, fc = FakeOS({"/dev/rdisk4": dev}, mode=stat.S_IFCHR), [], []
    adapter = MacAdapter(runner=runner, io_factory=lambda p: PX.PosixDeviceIO(p, "darwin", osmod=fos, ioctl=fake_ioctl(CARD, log=log),
                                                                              fcntl_fn=lambda fd, cmd, arg=0: fc.append((cmd, arg))))
    req, doc = request_for("/dev/disk4", "disk4", "U4", im)
    assert CL.run_session(CL.inprocess(adapter, "darwin"), req)["provisioned"]
    unmounts = [c for c in calls if c[1] == "unmountDisk"]
    assert unmounts == [["diskutil", "unmountDisk", "/dev/disk7"], ["diskutil", "unmountDisk", "/dev/disk4"]]   # APFS container first
    assert fos.opened[0][0] == "/dev/rdisk4"
    assert fos.opened[0][1] & os.O_EXCL == 0 and fc == [(PX.F_NOCACHE, 1)] and PX.DKIOCSYNCHRONIZECACHE in log
    off = im["manifest"]["slot"]["offset"]
    with open(dev, "rb") as fh:
        fh.seek(off)
        assert fh.read(P.SLOT_SIZE) == doc
    small = MacAdapter(runner=runner, io_factory=lambda p: PX.PosixDeviceIO(p, "darwin", osmod=FakeOS({p: dev}, stat.S_IFCHR),
                                                                            ioctl=fake_ioctl(GB), fcntl_fn=lambda *a: 0))
    with pytest.raises(CL.HelperFailed, match="another size"):
        CL.run_session(CL.inprocess(small, "darwin"), request_for("/dev/disk4", "disk4", "U4", im)[0])
    small.finish(None)
    state.update(mounted=True, unmount_works=False)                       # a volume refuses to unmount: nothing is written
    with pytest.raises(CL.HelperFailed) as e:
        CL.run_session(CL.inprocess(small, "darwin"), request_for("/dev/disk4", "disk4", "U4", im)[0])
    assert e.value.code == "unmount_failed" and "/Volumes/BOOT" in e.value.message and "/Volumes/Card APFS" in e.value.message


# ------------------------------------------------------------------ Windows
class FakeKernel32:
    """Records calls; PhysicalDrive3 is backed by a regular file; volumes can refuse to lock a few times."""

    def __init__(self, dev, size=CARD, sector=512, lock_failures=0, short_write=False, update_fails=False, unlock_fails=False):
        self.dev, self.size, self.sector = dev, size, sector
        self.lock_failures, self.short_write, self.update_fails, self.unlock_fails = lock_failures, short_write, update_fails, unlock_fails
        self.log, self.handles, self.n = [], {}, 0
        self.fh = open(dev, "r+b")

    def CreateFile(self, path, access, share, disposition, flags):
        self.n += 1
        self.handles[self.n] = path
        self.log.append(("CreateFile", path, flags))
        return self.n

    def DeviceIoControl(self, h, code, inbuf, outsize):
        self.log.append(("ioctl", self.handles[h], code))
        if code == WIN.FSCTL_LOCK_VOLUME and self.lock_failures:
            self.lock_failures -= 1
            raise OSError(5, "Access is denied")
        if code == WIN.IOCTL_DISK_UPDATE_PROPERTIES and self.update_fails:
            raise OSError(1, "x")
        if code == WIN.FSCTL_UNLOCK_VOLUME and self.unlock_fails:
            raise OSError(158, "not locked")
        if code == WIN.IOCTL_DISK_GET_LENGTH_INFO:
            return struct.pack("<q", self.size)
        if code == WIN.IOCTL_DISK_GET_DRIVE_GEOMETRY:
            return struct.pack("<qIIII", 1, 12, 255, 63, self.sector)
        return b""

    def ReadFile(self, h, offset, n):
        assert offset % self.sector == 0 and n % self.sector == 0, "unaligned raw read"
        self.fh.seek(offset)
        return self.fh.read(n)

    def WriteFile(self, h, offset, data):
        assert offset % self.sector == 0 and len(data) % self.sector == 0, "unaligned raw write"
        self.fh.seek(offset)
        self.fh.write(data if not self.short_write else data[: len(data) // 2])
        return len(data) if not self.short_write else len(data) // 2

    def FlushFileBuffers(self, h):
        self.fh.flush()
        self.log.append(("flush", self.handles[h]))

    def CloseHandle(self, h):
        self.log.append(("close", self.handles.pop(h)))


def windows_listing():
    return json.dumps([
        {"Number": 0, "FriendlyName": "NVMe", "SerialNumber": "A", "Size": 512 * GB, "BusType": "NVMe", "IsBoot": True, "IsSystem": True,
         "IsReadOnly": False, "DriveLetters": "C"},
        {"Number": 3, "FriendlyName": "SD Card", "SerialNumber": "B", "Size": CARD, "BusType": "SD", "IsBoot": False, "IsSystem": False,
         "IsReadOnly": False, "DriveLetters": "EF"}]).encode()


def test_windows_adapter_locks_dismounts_writes_aligned_and_unlocks(tmp_path):
    dev = card_file(tmp_path)
    im = F.image(tmp_path, size=6 * F.MB + 1000)                     # the last chunk is not sector-aligned: read-modify-write
    k = FakeKernel32(dev, lock_failures=2)
    sleeps = []
    adapter = WIN.WindowsAdapter(runner=lambda argv: windows_listing(), k32=k, sleep=sleeps.append)
    req, doc = request_for("\\\\.\\PhysicalDrive3", "PhysicalDrive3", "B", im)
    assert CL.run_session(CL.inprocess(adapter, "windows"), req)["provisioned"]
    names = [(e[0], e[1], e[2]) for e in k.log if e[0] == "ioctl" and e[2] in (WIN.FSCTL_LOCK_VOLUME, WIN.FSCTL_DISMOUNT_VOLUME,
                                                                                WIN.FSCTL_UNLOCK_VOLUME)]
    assert names[:4] == [("ioctl", "\\\\.\\E:", WIN.FSCTL_LOCK_VOLUME)] * 3 + [("ioctl", "\\\\.\\E:", WIN.FSCTL_DISMOUNT_VOLUME)]
    assert ("ioctl", "\\\\.\\F:", WIN.FSCTL_DISMOUNT_VOLUME) in names and names[-2:] == [
        ("ioctl", "\\\\.\\E:", WIN.FSCTL_UNLOCK_VOLUME), ("ioctl", "\\\\.\\F:", WIN.FSCTL_UNLOCK_VOLUME)]
    assert sleeps == [0.25, 0.25]
    drive = [e for e in k.log if e[0] == "CreateFile" and e[1] == "\\\\.\\PhysicalDrive3"][0]
    assert drive[2] == WIN.FILE_FLAG_NO_BUFFERING | WIN.FILE_FLAG_WRITE_THROUGH
    assert ("ioctl", "\\\\.\\PhysicalDrive3", WIN.IOCTL_DISK_UPDATE_PROPERTIES) in k.log and not k.handles   # every handle closed
    with open(dev, "rb") as fh:
        raw = fh.read(im["manifest"]["size"])
    assert raw == im["data"][:im["manifest"]["slot"]["offset"]] + doc + im["data"][im["manifest"]["slot"]["offset"] + P.SLOT_SIZE:]


def test_windows_failure_paths(tmp_path):
    dev = card_file(tmp_path)
    im = F.image(tmp_path)
    k = FakeKernel32(dev, lock_failures=100, unlock_fails=True)
    adapter = WIN.WindowsAdapter(runner=lambda argv: windows_listing(), k32=k, sleep=lambda s: None)
    req, _ = request_for("\\\\.\\PhysicalDrive3", "PhysicalDrive3", "B", im)
    with pytest.raises(CL.HelperFailed) as e:
        CL.run_session(CL.inprocess(adapter, "windows"), req)
    assert e.value.code == "unmount_failed" and "E: is in use" in e.value.message and not k.handles    # released despite unlock errors
    k2 = FakeKernel32(dev, short_write=True, update_fails=True)
    adapter2 = WIN.WindowsAdapter(runner=lambda argv: windows_listing(), k32=k2, sleep=lambda s: None)
    with pytest.raises(CL.HelperFailed) as e:
        CL.run_session(CL.inprocess(adapter2, "windows"), request_for("\\\\.\\PhysicalDrive3", "PhysicalDrive3", "B", im)[0])
    assert e.value.code == "write_failed" and "short write" in e.value.message and not k2.handles
    for kw, msg in (({"size": GB}, "another size"), ({"sector": 333}, "implausible")):
        k3 = FakeKernel32(dev, **kw)
        adapter3 = WIN.WindowsAdapter(runner=lambda argv: windows_listing(), k32=k3, sleep=lambda s: None)
        with pytest.raises(CL.HelperFailed, match=msg):
            CL.run_session(CL.inprocess(adapter3, "windows"), request_for("\\\\.\\PhysicalDrive3", "PhysicalDrive3", "B", im)[0])
        assert not k3.handles


# ------------------------------------------------------------------ shared pieces
def test_block_target_alignment(tmp_path):
    dev = card_file(tmp_path, 64 * 1024)

    class FileIO:
        size, sector = 64 * 1024, 4096
        def __init__(self):
            self.fh, self.ops = open(dev, "r+b"), []
        def pread(self, off, n):
            assert off % 4096 == 0
            self.ops.append(("r", off, n)); self.fh.seek(off); return self.fh.read(n)
        def pwrite(self, off, data):
            assert off % 4096 == 0 and len(data) % 4096 == 0
            self.ops.append(("w", off, len(data))); self.fh.seek(off); self.fh.write(data)
        def sync(self):
            self.ops.append(("sync",))
        def drop_cache(self):
            self.ops.append(("drop",))
        def close(self):
            self.fh.close()
    io_ = FileIO()
    t = BlockTarget(io_, "/dev/x", "S")
    assert t.identity() == ("/dev/x", 64 * 1024, "S")
    t.write_at(4096, b"A" * 4096)
    assert io_.ops == [("w", 4096, 4096)]                              # aligned: written directly
    t.write_at(5000, b"BC")                                           # unaligned: read-modify-write of one sector
    assert t.read_at(4999, 4) == b"ABCA" and t.read_at(64 * 1024 - 2, 10) == bytes(2)
    t.flush()
    assert io_.ops[-2:] == [("sync",), ("drop",)]
    for off, data in ((-1, b"x"), (64 * 1024 - 1, b"xx")):
        with pytest.raises(W.WriteError, match="beyond"):
            t.write_at(off, data)
    io_.pread = lambda off, n: b"short"
    with pytest.raises(W.WriteError, match="short read"):
        t.write_at(10, b"x")
    t.close()


def test_simulated_adapter_and_adapter_selection(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="unknown"):
        create(str(tmp_path), "x", GB, colour="red")
    p = create(str(tmp_path), "card", 2 * GB, name="sdz", mounts=["/media/x"])
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "broken.card").write_bytes(b"")
    (tmp_path / "broken.card.json").write_text("{")
    a = SimulatedAdapter(str(tmp_path))
    disks = {d.name: d for d in a.list_disks()}
    assert disks["sdz"].path == p and disks["sdz"].mounts == ("/media/x",) and disks["broken"].size == 0
    assert isinstance(adapter_for("linux", simulate=str(tmp_path))[0], SimulatedAdapter)
    assert isinstance(adapter_for("linux")[0], LinuxAdapter) and isinstance(adapter_for("darwin")[0], MacAdapter)
    fake = types.ModuleType("hififlash.platforms.win32api")
    fake.Kernel32 = lambda: "k32"
    monkeypatch.setitem(sys.modules, "hififlash.platforms.win32api", fake)
    win, name = adapter_for("win32")
    assert name == "windows" and win.k32 == "k32"
    with pytest.raises(ValueError):
        adapter_for("plan9")


def test_the_real_command_runner_is_only_a_thin_wrapper(monkeypatch):
    import subprocess
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append((argv, kw)) or types.SimpleNamespace(stdout=b"out"))
    assert run_command(["lsblk", "--json"]) == b"out"
    assert seen == [(["lsblk", "--json"], {"capture_output": True, "check": True, "timeout": 60})]


def test_linux_and_mac_default_device_factories_are_wired(monkeypatch):
    made = []
    monkeypatch.setattr(PX, "PosixDeviceIO", lambda path, plat: made.append((path, plat)) or types.SimpleNamespace(size=1, close=lambda: None))
    LinuxAdapter(runner=None).io_factory("/dev/sdb")
    MacAdapter(runner=None).io_factory("/dev/rdisk4")
    assert made == [("/dev/sdb", "linux"), ("/dev/rdisk4", "darwin")]


class CachedFakeCard:
    """Review M12: a fake-capacity card (keeps only the first ``real`` bytes, the rest reads back as zeros) BEHIND an OS page cache that
    serves recently written blocks from RAM until the cache is dropped - the situation of a real block device after fsync."""

    def __init__(self, size, real, drop_works=True):
        self.size, self.sector, self.real, self.drop_works = size, 512, real, drop_works
        self.media, self.cache = bytearray(size), {}

    def pwrite(self, off, data):
        keep = max(0, min(len(data), self.real - off))
        self.media[off:off + keep] = data[:keep]
        for i in range(0, len(data), self.sector):
            self.cache[off + i] = bytes(data[i:i + self.sector])

    def pread(self, off, n):
        out = bytearray(self.media[off:off + n])
        for i in range(0, n, self.sector):
            if off + i in self.cache:
                out[i:i + self.sector] = self.cache[off + i]
        return bytes(out)

    def sync(self):
        pass

    def drop_cache(self):
        if self.drop_works:
            self.cache.clear()

    def close(self):
        pass


@pytest.mark.parametrize("drop_works", [True, False])
def test_m12_read_back_detects_a_fake_card_only_because_the_cache_is_dropped(tmp_path, drop_works):
    im = F.image(tmp_path)
    t = BlockTarget(CachedFakeCard(64 * F.MB, real=3 * F.MB, drop_works=drop_works), "/dev/sdb", "R1")
    if drop_works:                    # what PosixDeviceIO (BLKFLSBUF + POSIX_FADV_DONTNEED / F_NOCACHE) and Windows NO_BUFFERING do
        with pytest.raises(W.WriteError, match="read-back verification failed"):
            W.write_card(open(im["path"], "rb"), im["manifest"], t, t.identity(), None)
    else:                             # a target that verified from the cache would accept the fake card: the reason for M12
        assert W.write_card(open(im["path"], "rb"), im["manifest"], t, t.identity(), None)["result"] == "ok"


def test_threads_do_not_leak():
    assert threading.active_count() < 50
