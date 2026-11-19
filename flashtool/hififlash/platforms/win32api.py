"""Thin ctypes bindings to kernel32 / shell32 for the Windows adapter and the UAC launch.

NATIVE-ONLY: this module needs ``ctypes.WinDLL`` and is therefore NOT executed by the test suite (Linux containers). It is kept to the bare
call wrappers; all logic that uses them (lock/dismount order, retries, sizes, alignment, cleanup) lives in ``windows.py`` and is tested
against a recording fake with the same method signatures. Measured coverage omits this file explicitly (``.coveragerc-flashtool``), and the
G3 gate (F-1..F-4, F-6) is the place where it is verified on real Windows 11.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_BEGIN = 0
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0


class Kernel32:
    def __init__(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
        k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
        k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.VirtualAlloc.restype = wintypes.LPVOID
        k.VirtualAlloc.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        k.VirtualFree.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]

    def _check(self, ok) -> None:
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, ctypes.FormatError(err))

    def CreateFile(self, path: str, access: int, share: int, disposition: int, flags: int):
        h = self.k.CreateFileW(path, access, share, None, disposition, flags, None)
        if h in (None, INVALID_HANDLE_VALUE):
            self._check(False)
        return h

    def DeviceIoControl(self, h, code: int, inbuf: bytes | None, outsize: int) -> bytes:
        out = ctypes.create_string_buffer(outsize) if outsize else None
        n = wintypes.DWORD(0)
        self._check(self.k.DeviceIoControl(h, code, inbuf, len(inbuf or b""), out, outsize, ctypes.byref(n), None))
        return out.raw[: n.value] if out is not None else b""

    def _aligned(self, n: int):
        # FILE_FLAG_NO_BUFFERING needs sector-aligned memory: VirtualAlloc returns page-aligned memory (MEM_COMMIT|MEM_RESERVE, RW)
        p = self.k.VirtualAlloc(None, max(n, 1), 0x3000, 0x04)
        self._check(p)
        return p

    def ReadFile(self, h, offset: int, n: int) -> bytes:
        self._check(self.k.SetFilePointerEx(h, offset, None, FILE_BEGIN))
        p = self._aligned(n)
        try:
            got = wintypes.DWORD(0)
            self._check(self.k.ReadFile(h, p, n, ctypes.byref(got), None))
            return ctypes.string_at(p, got.value)
        finally:
            self.k.VirtualFree(p, 0, 0x8000)

    def WriteFile(self, h, offset: int, data: bytes) -> int:
        self._check(self.k.SetFilePointerEx(h, offset, None, FILE_BEGIN))
        p = self._aligned(len(data))
        try:
            ctypes.memmove(p, data, len(data))
            done = wintypes.DWORD(0)
            self._check(self.k.WriteFile(h, p, len(data), ctypes.byref(done), None))
            return done.value
        finally:
            self.k.VirtualFree(p, 0, 0x8000)

    def FlushFileBuffers(self, h) -> None:
        self._check(self.k.FlushFileBuffers(h))

    def CloseHandle(self, h) -> None:
        self.k.CloseHandle(h)


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def shell_execute_runas(exe: str, params: str) -> int:
    """Start ``exe`` elevated (UAC prompt). Returns the process handle; raises OSError if the user declines (ERROR_CANCELLED)."""
    info = _SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(_SHELLEXECUTEINFOW), fMask=SEE_MASK_NOCLOSEPROCESS, lpVerb="runas", lpFile=exe,
                              lpParameters=params, nShow=SW_HIDE)
    if not ctypes.WinDLL("shell32", use_last_error=True).ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        raise OSError(err, ctypes.FormatError(err))
    return info.hProcess
