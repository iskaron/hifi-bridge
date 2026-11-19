"""Platform disk adapters behind one interface (docs/FLASH_TOOL.md §4):

    list_disks() -> list[Disk]      enumerate (lsblk / diskutil / PowerShell); also used unprivileged by the GUI
    prepare(disk)                   unmount (Linux), diskutil unmountDisk (macOS), lock + dismount volumes (Windows)  - helper only
    open(disk) -> writer.Target     exclusive whole-disk access; the opened device must report the enumerated size   - helper only
    finish(disk)                    release locks (always called after prepare)                                       - helper only

Every system call goes through an injected runner / os / ioctl / kernel32 object, so the tests drive the real adapter code with RECORDED
outputs and SIMULATED cards and never touch a host device.
"""
from __future__ import annotations

import subprocess


def run_command(argv: list[str]) -> bytes:
    """The real command runner (owner's machine only; tests inject recorded outputs and forbid subprocesses)."""
    return subprocess.run(argv, capture_output=True, check=True, timeout=60).stdout


def adapter_for(platform: str, simulate: str | None = None):
    """(adapter, protocol platform name) for ``sys.platform``."""
    if simulate:
        from .simulated import SimulatedAdapter
        return SimulatedAdapter(simulate), "simulated"
    if platform.startswith("linux"):
        from .linux import LinuxAdapter
        return LinuxAdapter(), "linux"
    if platform == "darwin":
        from .darwin import MacAdapter
        return MacAdapter(), "darwin"
    if platform in ("win32", "cygwin"):
        from .windows import WindowsAdapter
        return WindowsAdapter(), "windows"
    raise ValueError(f"unsupported platform {platform}")
