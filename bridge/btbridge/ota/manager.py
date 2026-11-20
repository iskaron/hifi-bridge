"""The update state machine (docs/OTA.md section 3).

Persistent phases live in ``<data>/ota/state.json`` (written atomically, under an advisory file lock, because the post-boot health check
is a second process). Transient phases (downloading, verifying) exist only in memory; after a restart a partial download can be resumed.

    idle ──download/upload──> (verifying) ──RAUC InspectBundle ok + compatible──> staged ──install──> installing
    installing ──Completed(0)──> installed ──owner: reboot──> rebooting ──boot of the new slot──> confirming ──health ok──> idle (updated)
    installing ──Completed(≠0) / RAUC gone──> staged + error           installed ──owner: cancel──> idle (RAUC marks the booted slot active)
    rebooting/confirming ──old slot boots again (boot-count or health failure)──> idle (rolled_back)
    idle ──owner: rollback──> switching ──reboot──> confirming ──health ok──> idle (switched)

Nothing is ever installed without an explicit owner action (DECISIONS #10). Trust is RAUC's: a bundle is staged only after RAUC's
InspectBundle verified its signature against the image keyring, and only if its compatible matches the device (RAUC's InspectBundle
does not compare the compatible - checked here, and again by RAUC during installation).

Before every installation the bridge's own data files are copied to ``<data>/ota/snapshots/<slot>/`` together with the running version.
Restoring it is ONLY an owner action: the owner requests it in the WebUI (``request_snapshot_restore``), the device restarts, and
``prepare_data()`` applies it before the configuration is loaded. Nothing is restored automatically: after a rollback the older version
keeps reading the current data (ConfigStore keeps unknown keys and never lowers ``schema_version``); see docs/OTA.md section 6.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import threading
import time
from typing import Callable

from .. import __version__
from .rauc import RaucError, RaucUnavailable
from .source import UpdateSource, load_trust
from .transfer import Download, TransferError, ensure_space

FORMAT = 1
BUNDLE = "bundle.raucb"
MAX_BUNDLE = 1 << 30                    # 1 GiB: far above two rootfs slots (PHASE2B_PLAN section 3); the space check is the real limit
SNAPSHOT_FILES = ("settings.json", "secrets.json", "network.json")
SNAPSHOT_DIRS = ("certs",)
PERSISTENT = ("idle", "staged", "installing", "installed", "rebooting", "switching", "confirming")
REBOOT_PHASES = ("installed", "switching")
MONITOR_S = 0.5
RAUC_GONE_S = 120.0                     # RAUC unreachable or unusable this long during an installation -> interrupted
REBOOT_WAIT_S = 300.0                   # "rebooting" on the same boot this long: the restart never happened -> back, with guidance


class UpdateError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{(': ' + detail) if detail else ''}")
        self.code, self.detail = code, detail


# ------------------------------------------------------------------------------------------------ persistence helpers
def _fsync_dir(d: str) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: str, data) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def boot_id(path: str = "/proc/sys/kernel/random/boot_id") -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


def booted_slot_from_cmdline(path: str = "/proc/cmdline") -> str | None:
    """RAUC's boot slot as the bootloader passed it (``rauc.slot=A``), for use before the RAUC service is up."""
    try:
        with open(path) as fh:
            m = re.search(r"(?:^|\s)rauc\.slot=(\S+)", fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


def progress_of(props: dict) -> dict:
    """RAUC's Progress (percentage, message, depth) for the page; a malformed value shows 0 % instead of ending the monitor."""
    pr = props.get("Progress")
    try:
        pct, msg = int(pr[0]), str(pr[1])
    except (TypeError, ValueError, IndexError, KeyError):
        pct, msg = 0, ""
    return {"done": max(0, min(100, pct)), "total": 100, "message": msg[:200]}


def empty_state() -> dict:
    return {"format": FORMAT, "phase": "idle", "staged": None, "install": None, "error": None, "result": None, "check": None}


class StateFile:
    """``<data>/ota/state.json`` with an advisory lock shared by the bridge and the health check."""

    def __init__(self, ota_dir: str):
        self.dir = ota_dir
        os.makedirs(ota_dir, mode=0o700, exist_ok=True)
        self.path = os.path.join(ota_dir, "state.json")
        self._tlock = threading.RLock()

    @contextlib.contextmanager
    def edit(self):
        with self._tlock, open(os.path.join(self.dir, "state.lock"), "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            st = self.read()
            before = json.dumps(st, sort_keys=True)
            yield st
            if json.dumps(st, sort_keys=True) != before:
                atomic_json(self.path, st)

    def read(self) -> dict:
        st = load_json(self.path, None)
        if not isinstance(st, dict) or st.get("format") != FORMAT or st.get("phase") not in PERSISTENT:
            return empty_state()
        return {**empty_state(), **st}


# ------------------------------------------------------------------------------------------------ data snapshot
def take_snapshot(data_dir: str, ota_dir: str, bootname: str, version: str, schema_version: int, clock=time.time) -> str:
    """Copy the bridge's own data files (settings, secrets, network, certificates) for ``bootname``; atomic via a temporary dir."""
    base = os.path.join(ota_dir, "snapshots")
    os.makedirs(base, mode=0o700, exist_ok=True)
    final, tmp = os.path.join(base, bootname), os.path.join(base, f".{bootname}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o700)
    files = []
    for name in SNAPSHOT_FILES:
        src = os.path.join(data_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(tmp, name))
            files.append(name)
    for name in SNAPSHOT_DIRS:
        src = os.path.join(data_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, name))
            files.append(name + "/")
    atomic_json(os.path.join(tmp, "meta.json"), {"version": version, "slot": bootname, "schema_version": schema_version,
                                                 "created": clock(), "files": files})
    old = os.path.join(base, f".{bootname}.old")
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(final):
        os.replace(final, old)
    os.replace(tmp, final)
    _fsync_dir(base)
    shutil.rmtree(old, ignore_errors=True)
    return final


def snapshot_meta(ota_dir: str, bootname: str) -> dict | None:
    m = load_json(os.path.join(ota_dir, "snapshots", bootname, "meta.json"))
    return m if isinstance(m, dict) else None


RESTORE_REQUEST = "restore-request.json"
TRANSIENT = (BUNDLE + ".upload",)                      # an upload can never be resumed: its HTTP connection is gone
PARTIAL = (BUNDLE + ".part", BUNDLE + ".part.json")    # a paused/interrupted download: resumable while the device keeps running
TMP_NAME = re.compile(r"^state\.json\.tmp-\d+$|^\.health-probe$")


def cleanup_staging(data_dir: str, at_boot: bool = True) -> list[str]:
    """Persistence rule (DECISIONS #121, docs/PERSISTENCE.md): remove every update file that no persistent state needs any more.
    Runs in the boot step (``at_boot``: nothing transient survives a reboot, e.g. after a power cut during an upload or a download;
    nothing else writes the directory at that point) and at every bridge start (``at_boot=False``: only what the previous bridge
    process left behind; a paused download stays resumable until the next reboot, and the health check, which may run concurrently,
    keeps its own temporary files). Kept: the bundle that the persistent state still references (phase ``staged`` = verified, waiting
    for the owner's install; ``installing`` = RAUC reads it, and after a power cut the owner can retry), the snapshots,
    ``slots.json``/``health.json``/``state.json``. Removed: an interrupted upload, a bundle no state refers to (e.g. a verification
    interrupted by a restart), and at boot a partial download, temporary files of atomic writes and half-written snapshots.
    Returns the removed paths relative to the data directory."""
    ota_dir = os.path.join(data_dir, "ota")
    if not os.path.isdir(ota_dir):
        return []
    removed = []

    def rm(name: str) -> None:
        p = os.path.join(ota_dir, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.unlink(p)
        else:
            return
        removed.append("ota/" + name)
    for name in TRANSIENT + (PARTIAL if at_boot else ()):
        rm(name)
    st = load_json(os.path.join(ota_dir, "state.json"), {})
    staged = st.get("staged") if isinstance(st, dict) else None
    needed = isinstance(staged, dict) and st.get("phase") in ("staged", "installing") \
        and staged.get("path") == os.path.join(ota_dir, BUNDLE)
    if not needed:
        rm(BUNDLE)
    if not at_boot:
        if removed:
            _fsync_dir(ota_dir)
        return removed
    for name in sorted(os.listdir(ota_dir)):
        if TMP_NAME.match(name):
            rm(name)
    snaps = os.path.join(ota_dir, "snapshots")
    if os.path.isdir(snaps):
        for name in sorted(os.listdir(snaps)):
            if name.startswith(".") and name.endswith((".tmp", ".old")):
                rm(os.path.join("snapshots", name))
    if removed:
        _fsync_dir(ota_dir)
    return removed


def forget_owner_data(data_dir: str) -> list[str]:
    """Factory reset (recovery.factory_reset): the snapshots hold copies of settings and SECRETS of the previous owner; they, a pending
    restore request, the update state and any bundle go. Kept: slots.json and health.json (which version is in which slot - device facts)."""
    ota = os.path.join(data_dir, "ota")
    removed = []
    snaps = os.path.join(ota, "snapshots")
    if os.path.isdir(snaps):
        shutil.rmtree(snaps)
        removed.append("ota/snapshots/")
    for name in (RESTORE_REQUEST, "state.json", BUNDLE, BUNDLE + ".part", BUNDLE + ".part.json", BUNDLE + ".upload"):
        p = os.path.join(ota, name)
        if os.path.isfile(p):
            os.unlink(p)
            removed.append(f"ota/{name}")
    return removed


def restorable_snapshot(ota_dir: str, bootname: str | None, version: str) -> dict | None:
    """The snapshot this exact version took on this slot before an update, if any (only that one fits this version's code)."""
    meta = snapshot_meta(ota_dir, bootname) if bootname else None
    return meta if meta and meta.get("version") == version and meta.get("slot") == bootname else None


def prepare_data(data_dir: str, version: str = __version__, bootname: str | None = None, log: Callable[[str], None] = print) -> str:
    """Boot step BEFORE anything reads the data (``bootfiles``, i.e. ``hifi-bridge-boot.service``, which ``hifi-netd`` and the bridge are
    ordered after - review N-4; the bridge calls it again as a no-op fallback): apply an OWNER-REQUESTED snapshot restore (never
    automatic). The request names the snapshot (slot, version, creation time); it is applied only if that snapshot still exists and
    belongs to the running version on the booted slot. All files are copied first and swapped in only when every copy succeeded; the
    request file is removed afterwards; a power cut in between repeats the (idempotent) restore.
    Never raises (a failure must not stop the start, or every restart would repeat it): returns none | restored | mismatch | deferred
    (the booted slot is not known here; the request stays for a later step) | failed (logged, recorded as the error ``restore_failed``
    for the update/backup pages, request dropped, nothing changed)."""
    ota_dir = os.path.join(data_dir, "ota")
    req_path = os.path.join(ota_dir, RESTORE_REQUEST)
    try:
        req = load_json(req_path)
        if req is None and not os.path.exists(req_path):
            return "none"
        bootname = bootname or booted_slot_from_cmdline()
        if bootname is None:
            log("update: snapshot restore deferred (the booted slot is not known in this step)")
            return "deferred"
        meta = restorable_snapshot(ota_dir, bootname, version)
        if not isinstance(req, dict) or not meta or req.get("slot") != bootname or req.get("created") != meta.get("created"):
            os.unlink(req_path)
            log("update: snapshot restore request ignored (it does not match this version and slot)")
            return "mismatch"
        _restore_snapshot(data_dir, os.path.join(ota_dir, "snapshots", bootname))
        with StateFile(ota_dir).edit() as st:
            st["restored"] = {"at": time.time(), "version": version, "snapshot_created": meta.get("created")}
        os.unlink(req_path)
        _fsync_dir(ota_dir)
    except Exception as e:                                     # ENOSPC, EIO on the card, a damaged snapshot, ...
        detail = f"{type(e).__name__}: {e}"
        log(f"update: snapshot restore FAILED ({detail}) - the device starts with its current settings")
        try:
            with StateFile(ota_dir).edit() as st:
                st["error"] = {"code": "restore_failed", "detail": detail[:500], "at": time.time()}
            os.unlink(req_path)
        except Exception as e2:
            log(f"update: could not record the failed restore ({type(e2).__name__}: {e2})")
        return "failed"
    log(f"update: restored the settings snapshot of version {version} as requested by the owner")
    return "restored"


def _restore_snapshot(data_dir: str, snap: str) -> None:
    """Copy everything next to its target first; only when every copy succeeded, swap the copies in (a failing copy changes nothing)."""
    swaps: list[tuple[str | None, str]] = []                   # (staged copy, or None = delete the target; target)
    try:
        for name in (*SNAPSHOT_FILES, *SNAPSHOT_DIRS):
            src, dst = os.path.join(snap, name), os.path.join(data_dir, name)
            tmp = dst + ".restore"
            if name in SNAPSHOT_DIRS and os.path.isdir(src):
                shutil.rmtree(tmp, ignore_errors=True)
                swaps.append((tmp, dst))                       # registered first: a partial copy is cleaned up as well
                shutil.copytree(src, tmp)
            elif name in SNAPSHOT_FILES and os.path.isfile(src):
                swaps.append((tmp, dst))
                shutil.copy2(src, tmp)
            elif name in SNAPSHOT_FILES and os.path.isfile(dst):
                swaps.append((None, dst))                      # did not exist when the snapshot was taken
    except BaseException:
        for tmp, _ in swaps:
            if tmp and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            elif tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        raise
    for tmp, dst in swaps:
        if tmp is None:
            os.unlink(dst)
        else:
            if os.path.isdir(tmp):
                shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
    _fsync_dir(data_dir)


# ------------------------------------------------------------------------------------------------ boot reconciliation
def reconcile(st: dict, booted: str | None, current_boot: str, rauc_busy: bool = False, now: float | None = None,
              primary: str | None = None, last_error: str = "") -> dict:
    """Pure: bring the persistent state in line with what actually booted. Used by the bridge at start and by the health check.
    ``primary`` is the bootname RAUC will boot next; ``rauc_busy`` = RAUC's Operation is still 'installing'."""
    now = time.time() if now is None else now
    if booted is None:                                          # RAUC not reachable yet: nothing can be decided (the health check
        return st                                               # waits for RAUC and reconciles later)
    ph, inst = st["phase"], st.get("install") or {}
    rebooted = inst.get("boot_id") not in (None, current_boot)
    if ph == "installing":
        if not rebooted:
            if rauc_busy:                                       # the bridge restarted; RAUC still works: keep watching
                return st
            if primary == inst.get("to_slot"):                 # RAUC finished while the bridge was down (it activates only on success)
                st["phase"], st["staged"] = "installed", None
                inst["finished_at"] = now
            else:
                st["phase"] = "staged" if st.get("staged") else "idle"
                st["install"] = None
                st["error"] = {"code": "install_interrupted", "detail": last_error[:500], "at": now}
            return st
        if booted == inst.get("to_slot"):                      # power cut after RAUC finished: the new slot booted
            st["phase"], st["staged"] = "confirming", None
        else:
            st["phase"] = "staged" if st.get("staged") and os.path.exists(st["staged"].get("path", "")) else "idle"
            if st["phase"] == "idle":
                st["staged"] = None
            st["install"] = None
            st["error"] = {"code": "install_interrupted", "detail": "power loss or restart during the installation", "at": now}
        return st
    if ph in ("installed", "rebooting", "switching") and rebooted:
        if booted == inst.get("to_slot"):
            st["phase"] = "confirming"
        else:
            st["phase"], st["staged"] = "idle", None
            st["result"] = {"kind": "rolled_back", "from_version": inst.get("to_version"), "to_version": inst.get("from_version"),
                            "reason": inst.get("failure") or "boot_failed", "at": now}
            st["install"] = None
    return st


def confirm(st: dict, booted: str | None, version: str, now: float | None = None) -> dict:
    """Pure: the health check passed on this boot - finish a pending update/switch."""
    now = time.time() if now is None else now
    inst = st.get("install") or {}
    if st["phase"] == "confirming" and booted == inst.get("to_slot"):
        st["result"] = {"kind": "switched" if inst.get("kind") == "switch" else "updated", "from_version": inst.get("from_version"),
                        "to_version": version, "at": now}
        st["phase"], st["install"], st["error"] = "idle", None, None
    return st


# ------------------------------------------------------------------------------------------------ the manager
class UpdateManager:
    def __init__(self, data_dir: str, rauc, trust: dict | None = None, source: UpdateSource | None = None, version: str = __version__,
                 reboot: Callable[[], None] | None = None, current_boot: Callable[[], str] = boot_id,
                 free: Callable[[str], int] | None = None, log: Callable[[str], None] = print, clock: Callable[[], float] = time.time,
                 download_opts: dict | None = None, monitor_s: float = MONITOR_S, rauc_gone_s: float = RAUC_GONE_S,
                 max_bundle: int = MAX_BUNDLE, reboot_wait_s: float = REBOOT_WAIT_S):
        self.data_dir, self.rauc, self.version, self.log, self.clock = data_dir, rauc, version, log, clock
        self.ota_dir = os.path.join(data_dir, "ota")
        self.state = StateFile(self.ota_dir)
        self.trust = trust if trust is not None else (load_trust() or {})
        self.source = source if source is not None else UpdateSource(self.trust.get("official_source"))
        self.reboot_fn = reboot or RebootRequest()
        self.current_boot, self.free = current_boot, free
        self.download_opts = download_opts or {}
        self.monitor_s, self.rauc_gone_s, self.max_bundle, self.reboot_wait_s = monitor_s, rauc_gone_s, max_bundle, reboot_wait_s
        self._lock = threading.RLock()
        self._busy: str | None = None                          # transient phase: downloading | verifying | uploading
        self._progress = {"done": 0, "total": 0}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def bundle_path(self) -> str:
        return os.path.join(self.ota_dir, BUNDLE)

    # ---------------------------------------------------------------- read side
    def _rauc_view(self) -> dict:
        try:
            props = self.rauc.properties()
            slots = self.rauc.slots()
            primary = self.rauc.primary()
        except (RaucUnavailable, RaucError) as e:
            return {"available": False, "error": str(e)}
        known = load_json(os.path.join(self.ota_dir, "slots.json"), {}) or {}
        booted = props.get("BootSlot")
        out = []
        for s in slots:
            b = s.get("bootname")
            version = self.version if b == booted else (s.get("bundle.version") or (known.get(b) or {}).get("version"))
            out.append({"name": s["name"], "bootname": b, "booted": b == booted, "primary": s["name"] == primary,
                        "boot_status": s.get("boot-status", "unknown"), "version": version,
                        "installed": s.get("installed.timestamp")})
        return {"available": True, "compatible": props.get("Compatible"), "booted": booted, "operation": props.get("Operation"),
                "last_error": props.get("LastError"), "progress": progress_of(props), "slots": out,
                "primary": primary}

    def info(self) -> dict:
        self._reboot_timeout()
        st = self.state.read()
        rv = self._rauc_view()
        other = next((s for s in rv.get("slots", []) if not s["booted"]), None)
        rollback = bool(other and other["boot_status"] == "good" and other["version"] and st["phase"] in ("idle", "staged")
                        and not self._busy)
        partial = os.path.exists(self.bundle_path + ".part")
        return {"version": self.version, "phase": self._busy or st["phase"], "progress": dict(self._progress),
                "staged": st["staged"], "install": st["install"], "error": st["error"], "result": st["result"], "check": st["check"],
                "restored": st.get("restored"), "rauc": rv, "other_slot": other, "can_rollback": rollback,
                "download_paused": partial and not self._busy, "reboot_required": st["phase"] in REBOOT_PHASES,
                "trust": {"mode": self.trust.get("mode"), "certificates": self.trust.get("certificates") or [],
                          "official_source": self.trust.get("official_source")},
                "check_available": self.source.available,
                "snapshot": restorable_snapshot(self.ota_dir, rv.get("booted"), self.version),
                "restore_requested": os.path.exists(os.path.join(self.ota_dir, RESTORE_REQUEST))}

    # ---------------------------------------------------------------- helpers
    def _set_error(self, code: str, detail: str = "") -> None:
        """Record the error, THEN leave a transient phase (so the UI never sees 'idle' without the error). A storage error while
        recording never keeps the transient phase (review N-10): the caller's UpdateError still tells the owner."""
        try:
            with self.state.edit() as st:
                st["error"] = {"code": code, "detail": detail[:500], "at": self.clock()}
        except OSError as e:
            self.log(f"update: could not record the error {code} ({type(e).__name__}: {e})")
        finally:
            self._busy = None
        self.log(f"update: {code}{(' - ' + detail) if detail else ''}")

    def _reboot_timeout(self) -> None:
        """Review N-10: the owner confirmed the restart but nothing restarted (same boot, REBOOT_WAIT_S later; e.g. the root restart
        helper did not act). Go back to the phase before, with an error that tells the owner what to do. A real restart changes the
        boot id and is reconciled at the next bridge start instead."""
        if self.state.read()["phase"] != "rebooting":          # cheap check first: no file lock on every page load
            return
        with self.state.edit() as st:
            inst = st.get("install") or {}
            rb = inst.get("reboot") or {}
            since = rb.get("at", inst.get("finished_at") or inst.get("started_at") or 0)
            if (st["phase"] != "rebooting" or rb.get("boot_id", inst.get("boot_id")) != self.current_boot()
                    or self.clock() - since < self.reboot_wait_s):
                return
            st["phase"] = rb.get("from") if rb.get("from") in REBOOT_PHASES else ("switching" if inst.get("kind") == "switch" else "installed")
            inst.pop("reboot", None)
            st["error"] = {"code": "reboot_timeout", "detail": "", "at": self.clock()}
        self.log("update: the requested restart did not happen - waiting for the owner again")

    def _require_idle(self, allowed=("idle", "staged")) -> dict:
        self._reboot_timeout()
        if self._busy:
            raise UpdateError("busy", self._busy)
        st = self.state.read()
        if st["phase"] not in allowed:
            raise UpdateError("busy", st["phase"])
        return st

    def _space(self, needed: int) -> None:
        os.makedirs(self.ota_dir, mode=0o700, exist_ok=True)
        kw = {"free": self.free} if self.free else {}
        try:
            ensure_space(self.ota_dir, needed, **kw)
        except TransferError as e:
            raise UpdateError("no_space", e.detail) from None

    def _remove_bundle(self) -> None:
        for p in (self.bundle_path, self.bundle_path + ".part", self.bundle_path + ".part.json", self.bundle_path + ".upload"):
            if os.path.exists(p):
                os.unlink(p)

    # ---------------------------------------------------------------- check + download
    def check(self) -> dict:
        if not self.source.available:
            raise UpdateError("no_source")
        compatible = (self._rauc_view().get("compatible") or "")
        if not compatible:
            raise UpdateError("rauc_unavailable")
        res = self.source.check(self.version, compatible)
        with self.state.edit() as st:
            st["check"] = res
        self.log(f"update check: {res['status']}" + (f" ({res['latest']['version']})" if res.get("latest") else ""))
        return res

    def start_download(self) -> None:
        with self._lock:
            self._require_idle(("idle",))
            st = self.state.read()
            latest = (st.get("check") or {}).get("latest")
            if not latest or (st["check"] or {}).get("status") != "update_available":
                raise UpdateError("no_update")
            asset = latest["asset"]
            if asset["size"] > self.max_bundle:
                raise UpdateError("too_large", str(asset["size"]))
            self._busy, self._progress = "downloading", {"done": 0, "total": asset["size"]}
            self._cancel = threading.Event()
            self._thread = threading.Thread(target=self._download, args=(latest,), daemon=True, name="ota-download")
            self._thread.start()

    def _download(self, latest: dict) -> None:
        asset = latest["asset"]
        try:
            kw = dict(self.download_opts)
            if self.free:
                kw["free"] = self.free
            Download(asset["url"], self.bundle_path, asset["size"], asset.get("sha256"), max_size=self.max_bundle,
                     progress=lambda d, t: self._progress.update(done=d, total=t), cancel=self._cancel, **kw).run()
        except TransferError as e:
            if e.code != "cancelled":
                self._set_error(e.code, e.detail)
            self._busy = None
            return
        except Exception as e:                                  # never leave the manager stuck in "downloading"
            self._set_error("download_failed", f"{type(e).__name__}: {e}")
            return
        try:
            self._verify(self.bundle_path, "download", asset["name"])
        except UpdateError:                                     # already recorded for the page; nobody waits for this thread
            pass

    def cancel_download(self) -> None:
        self._cancel.set()

    def discard(self) -> None:
        with self._lock:
            self._require_idle()
            self._remove_bundle()
            with self.state.edit() as st:
                st["phase"], st["staged"], st["error"] = "idle", None, None

    # ---------------------------------------------------------------- upload
    def begin_upload(self, size: int) -> str:
        """Reserve the upload: returns the temporary path to stream into. Raises UpdateError(busy | too_large | no_space)."""
        with self._lock:
            self._require_idle()
            if size > self.max_bundle:
                raise UpdateError("too_large", str(size))
            self._space(size)
            self._busy, self._progress = "uploading", {"done": 0, "total": size}
            return self.bundle_path + ".upload"

    def upload_progress(self, done: int) -> None:
        self._progress["done"] = done

    def abort_upload(self, code: str = "upload_failed", detail: str = "") -> None:
        p = self.bundle_path + ".upload"
        if os.path.exists(p):
            os.unlink(p)
        if code:
            self._set_error(code, detail)
        self._busy = None

    def finish_upload(self, name: str) -> dict:
        tmp = self.bundle_path + ".upload"
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            self.abort_upload("no_file")
            raise UpdateError("no_file")
        try:
            with self.state.edit() as st:
                st["staged"], st["phase"] = None, "idle"
            if os.path.exists(self.bundle_path):
                os.unlink(self.bundle_path)
            os.replace(tmp, self.bundle_path)
        except OSError as e:                                    # review N-10: never stay in "uploading"
            try:
                self.abort_upload("upload_failed", f"{type(e).__name__}: {e}")
            except OSError:
                self._busy = None
            raise UpdateError("upload_failed", type(e).__name__) from None
        return self._verify(self.bundle_path, "upload", os.path.basename(name or "")[:120])

    # ---------------------------------------------------------------- verification (RAUC's signature check + our compatible check)
    def _verify(self, path: str, origin: str, name: str) -> dict:
        """Transient phase 'verifying'; whatever happens, the manager leaves it (review N-10)."""
        self._busy = "verifying"
        try:
            return self._verify_bundle(path, origin, name)
        except UpdateError:
            raise
        except Exception as e:                                  # storage error, malformed RAUC answer, ...
            try:
                self._remove_bundle()
            except OSError:
                pass
            self._set_error("verify_failed", f"{type(e).__name__}: {e}")
            raise UpdateError("verify_failed", type(e).__name__) from None
        finally:
            self._busy = None

    def _verify_bundle(self, path: str, origin: str, name: str) -> dict:
        try:
            props = self.rauc.properties()
            info = self.rauc.inspect(path)
        except RaucUnavailable as e:
            self._remove_bundle()
            self._set_error("rauc_unavailable", str(e))
            raise UpdateError("rauc_unavailable", str(e)) from None
        except RaucError as e:                                  # RAUC refused it: unsigned, wrong signer, corrupt, not a bundle
            self._remove_bundle()
            self._set_error("signature_invalid", str(e))
            raise UpdateError("signature_invalid", str(e)) from None
        upd = info.get("update") or {}
        if upd.get("compatible") != props.get("Compatible"):
            self._remove_bundle()
            detail = f"bundle {upd.get('compatible')!r}, device {props.get('Compatible')!r}"
            self._set_error("wrong_compatible", detail)
            raise UpdateError("wrong_compatible", detail)
        staged = {"path": path, "version": str(upd.get("version") or ""), "compatible": upd.get("compatible"), "origin": origin,
                  "name": name, "size": os.path.getsize(path), "description": str(upd.get("description") or "")[:200],
                  "verified_at": self.clock()}
        with self.state.edit() as st:
            st["phase"], st["staged"], st["error"] = "staged", staged, None
        self.log(f"update: bundle {staged['version']} verified by RAUC ({origin})")
        return staged

    # ---------------------------------------------------------------- install
    def install(self) -> None:
        from ..config.store import SCHEMA_VERSION
        with self._lock:
            st = self._require_idle(("staged",))
            staged = st["staged"]
            if not staged or not os.path.exists(staged["path"]):
                raise UpdateError("no_file")
            try:
                props = self.rauc.properties()
                other = next(s for s in self.rauc.slots() if s.get("bootname") != props.get("BootSlot"))
            except (RaucUnavailable, RaucError, StopIteration) as e:
                raise UpdateError("rauc_unavailable", str(e)) from None
            if props.get("Operation") != "idle":
                raise UpdateError("busy", "rauc")
            booted = props.get("BootSlot")
            try:                                               # never install without the snapshot of the current data
                take_snapshot(self.data_dir, self.ota_dir, booted, self.version, SCHEMA_VERSION, self.clock)
            except OSError as e:
                self._set_error("snapshot_failed", f"{type(e).__name__}: {e}")
                raise UpdateError("snapshot_failed", type(e).__name__) from None
            try:
                while self.rauc.completed() is not None:       # drop stale signals
                    pass
            except RaucUnavailable:
                pass
            with self.state.edit() as s2:
                s2["phase"], s2["error"], s2["result"] = "installing", None, None
                s2["install"] = {"kind": "update", "from_slot": booted, "to_slot": other.get("bootname"), "from_version": self.version,
                                 "to_version": staged["version"], "boot_id": self.current_boot(), "started_at": self.clock()}
            try:
                self.rauc.install(staged["path"])
            except (RaucUnavailable, RaucError) as e:
                with self.state.edit() as s3:
                    s3["phase"], s3["install"] = "staged", None
                self._set_error("install_failed", str(e))
                raise UpdateError("install_failed", str(e)) from None
            self.log(f"update: installing {staged['version']} into slot {other.get('bootname')}")
            self._thread = threading.Thread(target=self._monitor, daemon=True, name="ota-install")
            self._thread.start()

    def _monitor(self) -> None:
        """Watch RAUC until the installation ends. The thread never dies silently (review N-5): RAUC unreachable, a RAUC error reply
        (RaucError) or anything unexpected counts as "RAUC not usable"; after rauc_gone_s the installation is reported as interrupted,
        so the page leaves "installing" and every action is possible again."""
        gone_since = None
        while True:
            try:
                if self._monitor_step():
                    return
                gone_since = None
            except Exception as e:
                why = "RAUC was not reachable" if isinstance(e, RaucUnavailable) else f"{type(e).__name__}: {e}"
                gone_since = gone_since or self.clock()
                if self.clock() - gone_since > self.rauc_gone_s:
                    try:
                        self._finish(None, f"{why} during the installation")
                    except Exception as e2:                    # e.g. the data partition cannot be written
                        self.log(f"update: could not record the end of the installation ({type(e2).__name__}: {e2})")
                    return
            time.sleep(self.monitor_s)

    def _monitor_step(self) -> bool:
        """One look at RAUC; True when the installation has ended (and was recorded)."""
        result = self.rauc.completed()
        props = self.rauc.properties()
        self._progress = progress_of(props)
        if result is not None:
            self._finish(result, str(props.get("LastError") or ""))
            return True
        if props.get("Operation") != "installing":             # idle without our Completed: finished while we were not looking,
            time.sleep(self.monitor_s)                         # or RAUC was restarted; give a late signal a moment to arrive
            try:
                result = self.rauc.completed()
            except (RaucUnavailable, RaucError):
                result = None
            if result is None and self._primary_bootname() == (self.state.read().get("install") or {}).get("to_slot"):
                result = 0                                     # RAUC activates the new slot only after a complete installation
            self._finish(result, str(props.get("LastError") or ""))
            return True
        return False

    def _primary_bootname(self) -> str | None:
        try:
            primary = self.rauc.primary()
            return next((s.get("bootname") for s in self.rauc.slots() if s["name"] == primary), None)
        except (RaucUnavailable, RaucError):
            return None

    def _finish(self, result: int | None, last_error: str) -> None:
        with self.state.edit() as st:
            if st["phase"] != "installing":
                return
            if result == 0:
                self._remove_bundle()                          # frees the data partition (the image is in the slot now), BEFORE
                st["phase"], st["staged"] = "installed", None  # the state says so: no orphaned bundle after a power cut
                st["install"]["finished_at"] = self.clock()
            else:
                st["phase"], st["install"] = ("staged" if st.get("staged") else "idle"), None
                code = "install_failed" if result is not None else "install_interrupted"
                st["error"] = {"code": code, "detail": last_error[:500], "at": self.clock()}
        if result == 0:
            self.log("update: installation complete - restart to use the new version")
        else:
            self.log(f"update: installation {'failed' if result is not None else 'interrupted'}: {last_error}")
        self._progress = {"done": 0, "total": 0}

    def resume(self) -> None:
        """Bridge start: drop staging files of a flow that ended with the previous process (an interrupted upload, an unverified
        bundle), reconcile with what booted; continue watching an installation that RAUC is still running."""
        try:
            removed = cleanup_staging(self.data_dir, at_boot=False)
            if removed:
                self.log(f"update: removed {', '.join(removed)} (left behind by an interrupted transfer)")
        except OSError as e:
            self.log(f"update: could not clean up the staging files ({type(e).__name__}: {e})")
        try:
            props = self.rauc.properties()
        except (RaucUnavailable, RaucError):
            props = {}
        primary = self._primary_bootname() if props else None
        with self.state.edit() as st:
            reconcile(st, props.get("BootSlot"), self.current_boot(), props.get("Operation") == "installing", self.clock(),
                      primary, props.get("LastError") or "")
            watching = st["phase"] == "installing"
        if watching:
            self._thread = threading.Thread(target=self._monitor, daemon=True, name="ota-install")
            self._thread.start()

    # ---------------------------------------------------------------- after install: reboot / cancel / rollback
    def cancel_installed(self) -> None:
        """Owner declines the restart: the booted slot becomes primary again (the installed slot is kept, but not booted)."""
        with self._lock:
            st = self._require_idle(REBOOT_PHASES)
            try:
                self.rauc.mark("active", "booted")
            except (RaucUnavailable, RaucError) as e:
                raise UpdateError("rauc_unavailable", str(e)) from None
            with self.state.edit() as s2:
                inst = st["install"] or {}
                s2["phase"], s2["install"] = "idle", None
                s2["result"] = {"kind": "cancelled", "from_version": inst.get("from_version"), "to_version": inst.get("to_version"),
                                "at": self.clock()}

    def request_reboot(self) -> None:
        with self._lock:
            st0 = self._require_idle(REBOOT_PHASES)
            with self.state.edit() as st:
                st["phase"], st["error"] = "rebooting", None
                if st.get("install"):                          # for the "restart did not happen" timeout (review N-10)
                    st["install"]["reboot"] = {"at": self.clock(), "boot_id": self.current_boot(), "from": st0["phase"]}
            try:
                self.reboot_fn()
            except OSError as e:                               # no restart helper (e.g. not on the image): stay where we were
                with self.state.edit() as st:
                    st["phase"] = st0["phase"]
                    (st.get("install") or {}).pop("reboot", None)
                raise UpdateError("reboot_unavailable", type(e).__name__) from None
            self.log("update: restart requested by the owner")

    def rollback(self) -> None:
        """Owner selects the other (previous, known-good) slot for the next boot."""
        with self._lock:
            self._require_idle(("idle", "staged"))
            info = self.info()
            other = info["other_slot"]
            if not info["can_rollback"]:
                raise UpdateError("rollback_unavailable")
            try:
                name, _ = self.rauc.mark("active", "other")
            except (RaucUnavailable, RaucError) as e:
                raise UpdateError("rauc_unavailable", str(e)) from None
            self._remove_bundle()
            with self.state.edit() as st:
                st["phase"], st["staged"], st["error"], st["result"] = "switching", None, None, None
                st["install"] = {"kind": "switch", "from_slot": info["rauc"]["booted"], "to_slot": other["bootname"],
                                 "from_version": self.version, "to_version": other["version"], "boot_id": self.current_boot(),
                                 "started_at": self.clock(), "slot_name": name}
            self.log(f"update: slot {other['bootname']} ({other['version']}) selected for the next start")

    def request_snapshot_restore(self) -> None:
        """Owner action: restore the settings snapshot that THIS version took before an update, then restart (the files are replaced
        by prepare_data() before the configuration is loaded, so the running service never sees a half-restored state)."""
        with self._lock:
            self._require_idle(("idle", "staged"))
            info = self.info()
            meta = info["snapshot"]
            if not meta:
                raise UpdateError("no_snapshot")
            if info["restore_requested"]:
                raise UpdateError("busy", "restore already requested")
            req = os.path.join(self.ota_dir, RESTORE_REQUEST)
            atomic_json(req, {"slot": meta["slot"], "version": meta["version"], "created": meta["created"], "requested": self.clock()})
            try:
                self.reboot_fn()
            except OSError as e:
                os.unlink(req)
                raise UpdateError("reboot_unavailable", type(e).__name__) from None
            self.log(f"update: owner requested the settings snapshot of {meta['version']}; restarting")

    def dismiss(self) -> None:
        with self.state.edit() as st:
            st["error"], st["result"] = None, None
            st.pop("restored", None)


class RebootRequest:
    """The bridge cannot reboot (unprivileged). It drops a request file; the root-owned ``hifi-bridge-reboot.path`` unit reboots.
    The file's content is not parsed by anyone (no input to validate)."""

    def __init__(self, path: str = "/run/hifi-bridge-ota/reboot-request"):
        self.path = path

    def __call__(self) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
