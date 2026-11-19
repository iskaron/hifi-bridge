"""The privileged helper's only job (docs/FLASH_TOOL.md §4): write ONE validated image to ONE validated removable whole disk.

After elevation it trusts nothing from the GUI except the user's choice:
  1. validate the request (``protocol.validate_request``);
  2. enumerate the disks AGAIN with its own adapter, find the requested path, require the same identity (path + size + serial) and apply
     the full safety policy (``disks.problems``) - removable, not the system disk, nothing mounted at a system location, writable, size;
  3. unmount / lock the disk's volumes, open the whole disk exclusively, check that the opened device still reports the same size;
  4. stream the image through ``writer.write_card`` (hash on the fly, flush, read back, provisioning slot, invalidate on any failure);
  5. release the disk and report.
A cancel message or EOF from the GUI (the GUI died) cancels the write; the writer then invalidates the card.
"""
from __future__ import annotations

import os
import stat
import threading
import time

from .. import disks as D
from .. import writer as W
from . import protocol as PR


class HelperError(Exception):
    def __init__(self, code: str, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.code, self.message, self.reasons = code, message, reasons or []


class Reporter:
    """Thread-safe, throttled progress events (at most every ``interval`` s per stage, plus the final value)."""

    def __init__(self, wfile, interval: float = 0.2, clock=time.monotonic):
        self.wfile, self.interval, self.clock = wfile, interval, clock
        self.lock = threading.Lock()
        self.last: dict[str, float] = {}

    def send(self, obj: dict) -> None:
        with self.lock:
            try:
                self.wfile.write(PR.encode(obj))
                self.wfile.flush()
            except (OSError, ValueError):
                pass                                   # the GUI is gone; the cancel watcher stops the write

    def progress(self, stage: str, done: int, total: int) -> None:
        now = self.clock()
        if done < total and now - self.last.get(stage, -1e9) < self.interval:
            return
        self.last[stage] = now
        self.send({"event": "progress", "stage": stage, "done": done, "total": total})


def _watch_cancel(rfile, cancel: threading.Event) -> None:
    try:
        while True:
            msg = PR.read_message(rfile)
            if msg is None or msg.get("op") == "cancel":
                break
    except (PR.ProtocolError, OSError, ValueError):
        pass
    cancel.set()


def check_disk(req: PR.WriteRequest, fresh: list[D.Disk]) -> D.Disk:
    disk = next((d for d in fresh if d.path == req.disk.path), None)
    if disk is None:
        raise HelperError("disk_gone", "the selected card is no longer connected - nothing was written")
    if not D.same_disk(req.disk, disk):
        raise HelperError("disk_changed", "the selected card was replaced by another device - nothing was written")
    reasons = D.problems(disk, req.manifest["size"], req.allow_large)
    if reasons:
        raise HelperError("disk_refused", "this device must not be written - nothing was written", reasons)
    return disk


def open_image_checked(path: str):
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise HelperError("image_unreadable", "the image is not a regular file")
        return W.open_image(path)
    except OSError as e:
        raise HelperError("image_unreadable", f"the image cannot be read: {e.strerror or e}") from None


def _platform_call(fn, disk: D.Disk, code: str, message: str):
    try:
        return fn(disk)
    except HelperError:
        raise
    except Exception as e:                                           # noqa: BLE001 - platform call boundary
        raise HelperError(code, f"{message}: {e} - nothing was written") from None


def run(req: PR.WriteRequest, adapter, rep: Reporter, cancel: threading.Event) -> dict:
    disk = check_disk(req, adapter.list_disks())
    image = open_image_checked(req.image)
    try:
        rep.progress("prepare", 0, 1)
        try:
            _platform_call(adapter.prepare, disk, "unmount_failed", "the card's volumes could not be unmounted/locked")
            target = _platform_call(adapter.open, disk, "open_failed", "the card could not be opened exclusively")
        except HelperError:
            adapter.finish(disk)                                     # release whatever prepare already locked
            raise
        rep.progress("prepare", 1, 1)
        try:
            return W.write_card(image, req.manifest, target, (disk.path, disk.size, disk.serial), req.provisioning, cancel=cancel,
                                progress=rep.progress)
        except W.Cancelled as e:
            raise HelperError("cancelled", str(e)) from None
        except W.WriteError as e:
            raise HelperError("write_failed", str(e)) from None
        finally:
            target.close()
            adapter.finish(disk)
    finally:
        image.close()


def serve(rfile, wfile, adapter, platform: str) -> int:
    """One session. Returns the process exit code (0 = written and verified)."""
    rep = Reporter(wfile)
    rep.send({"event": "ready", "v": PR.VERSION})
    try:
        msg = PR.read_message(rfile)
        if msg is None:
            return 2
        req = PR.validate_request(msg, platform)
    except PR.ProtocolError as e:
        rep.send({"event": "error", "code": "bad_request", "message": str(e), "reasons": []})
        return 2
    cancel = threading.Event()
    threading.Thread(target=_watch_cancel, args=(rfile, cancel), daemon=True).start()
    try:
        result = run(req, adapter, rep, cancel)
    except HelperError as e:
        rep.send({"event": "error", "code": e.code, "message": e.message, "reasons": e.reasons})
        return 1
    except Exception as e:                                           # noqa: BLE001 - never die silently while holding a disk
        rep.send({"event": "error", "code": "internal", "message": f"{type(e).__name__}: {e}", "reasons": []})
        return 1
    rep.send({"event": "done", "result": {"bytes": result["bytes"], "sha256": result["sha256"], "provisioned": result["provisioned"]}})
    return 0
