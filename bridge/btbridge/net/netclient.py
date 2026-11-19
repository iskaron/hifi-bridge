"""Bridge side of the network management (unprivileged): talks to the root agent ``hifi-netd`` and owns ``network.json``.

Change protocol (docs/NETWORK.md "Owner changes"):
1. the new configuration is validated and written to ``network.pending.json`` (0600);
2. the agent is asked to try it (``trial`` with the file's SHA-256); it validates it again and applies it;
3. ``confirmed``/``unchanged`` -> the pending file replaces ``network.json``; ``pending`` -> the owner confirms (``confirm``) or the agent
   rolls back; whenever the agent no longer has this trial pending, the pending file is promoted (confirmed) or deleted (rolled back).
A power cut during a trial therefore always comes back with the previous, working ``network.json``.

Consistency rules (review N-2/N-3): a status answer whose request started before the last ``save()`` ended is never used to judge the
pending file (generation counter); a request that reached the agent but got no answer (timeout) keeps the pending file - the next status
decides by trial id, and ``trial_request`` (received, not yet applied) keeps it too; only a request that never reached the agent, or an
agent that does not know the trial (restarted, rolled back, superseded), removes it.

Without an agent (Docker lab, development host) the service is *unmanaged*: it shows the kernel's view (netlink) and changes nothing.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from typing import Callable

from . import netconfig, netlink

DEFAULT_SOCKET = "/run/hifi-net/agent.sock"
TIMESYNC_FLAG = "/run/systemd/timesync/synchronized"
WATCH_S = 5.0


class AgentUnavailable(Exception):
    """``sent``: the request reached the agent completely before the failure (it may act on it; only the answer is missing)."""

    def __init__(self, message: str = "", sent: bool = False):
        super().__init__(message)
        self.sent = sent


class NotManaged(Exception):
    pass


class AgentClient:
    def __init__(self, path: str = DEFAULT_SOCKET, timeout: float = 15.0):
        self.path, self.timeout = path, timeout

    def available(self) -> bool:
        return os.path.exists(self.path)

    def call(self, op: str, **kw) -> dict:
        sent = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                s.sendall(json.dumps({"op": op, **kw}).encode() + b"\n")
                sent = True
                data = b""
                while not data.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            reply = json.loads(data.decode("utf-8"))
        except (OSError, ValueError) as e:
            raise AgentUnavailable(f"network agent {'did not answer' if sent else 'not reachable'}: {type(e).__name__}", sent) from None
        if not isinstance(reply, dict):
            raise AgentUnavailable("network agent: bad reply", True)
        return reply


class NetworkService:
    def __init__(self, data_dir: str, client: AgentClient | None = None, log: Callable[[str], None] = print,
                 secrets: list | None = None, interfaces: Callable[[], dict] = netlink.safe_interfaces,
                 timesync_flag: str = TIMESYNC_FLAG):
        self.data, self.client, self.log = data_dir, client, log
        self.secrets = secrets if secrets is not None else []          # the bridge's log redaction list (LogBuffer.secrets)
        self.interfaces, self.timesync_flag = interfaces, timesync_flag
        self.cfg_path = os.path.join(data_dir, netconfig.FILE_NAME)
        self.pending_path = os.path.join(data_dir, netconfig.PENDING_NAME)
        self.access = None                                               # LocalAccess: certificates and mDNS follow the network
        self.on_change: list[Callable[[dict], None]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._last: dict | None = None
        self._synced: bool | None = None
        self._addr_key = None
        self._gen = 0                                                    # bumped when a save() ends (review N-2)
        try:
            self._register(self.config())
        except netconfig.NetConfigError:
            pass

    # ---- configuration (own files)
    def config(self) -> dict:
        """The configuration in effect: the pending one while a change is being tried, else network.json."""
        if os.path.exists(self.pending_path):
            try:
                return netconfig.load(self.pending_path)
            except netconfig.NetConfigError:
                pass
        return netconfig.load(self.cfg_path)

    def _register(self, cfg: dict) -> None:
        for s in netconfig.secrets_of(cfg):
            if s not in self.secrets:
                self.secrets.append(s)                                   # defence in depth: never in a log line

    def _promote(self) -> None:
        os.replace(self.pending_path, self.cfg_path)
        try:
            fd = os.open(self.data, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def _drop_pending(self) -> None:
        try:
            os.unlink(self.pending_path)
        except FileNotFoundError:
            pass

    def _pending_id(self) -> str | None:
        try:
            with open(self.pending_path, "rb") as fh:
                return netconfig.digest(fh.read())
        except OSError:
            return None

    def _sync_pending(self, st: dict) -> None:
        pid = self._pending_id()
        if pid is None:
            return
        t = st.get("trial") or {}
        if t.get("id") == pid and t.get("state") == "pending":
            return
        if t.get("id") == pid and t.get("state") == "confirmed":
            self._promote()
        elif t.get("id") != pid and st.get("trial_request") == pid:
            return                                                       # received, not applied yet (the agent is busy)
        else:
            self._drop_pending()                                         # rolled back, superseded, or the agent restarted

    # ---- status
    def managed(self) -> bool:
        return self.client is not None and self.client.available()

    def _unmanaged_status(self) -> dict:
        ifs = self.interfaces()
        return {"managed": False, "state": "unmanaged", "hostname": None,
                "time_synchronized": os.path.exists(self.timesync_flag) if os.path.isdir(os.path.dirname(self.timesync_flag)) else None,
                "interfaces": [{"name": n, "role": "other", "carrier": i["carrier"], "mac": i["mac"],
                                "addresses": [f'{a["address"]}/{a["prefix"]}' for a in i["addresses"]], "default_route": i["default_route"]}
                               for n, i in sorted(ifs.items())]}

    def status(self) -> dict:
        if not self.managed():
            return self._unmanaged_status()
        gen = self._gen
        try:
            st = self.client.call("status")
        except AgentUnavailable as e:
            st = self._unmanaged_status()
            st.update(state="agent_unreachable", error=str(e))
            return st
        with self._lock:
            if gen == self._gen:                                         # else: asked before the last save() ended - stale
                self._sync_pending(st)
        st["pending_change"] = os.path.exists(self.pending_path)
        return st

    # ---- owner changes
    def save(self, new: dict) -> dict:
        """Try a new configuration (see module doc). Returns {result: confirmed|unchanged|pending, id} or {result: error, ...}."""
        if not self.managed():
            raise NotManaged()
        with self._lock:
            try:
                return self._save(new)
            finally:
                self._gen += 1

    def _save(self, new: dict) -> dict:
        raw = netconfig.write_private(self.pending_path, new)          # validates; raises NetConfigError
        tid = netconfig.digest(raw)
        self._register(netconfig.parse(raw))
        try:
            r = self.client.call("trial", id=tid)
        except AgentUnavailable as e:
            if e.sent:                                                   # the agent may apply it: the next status decides
                self.log(f"network: change {tid[:8]} handed over, no answer yet ({e})")
            else:
                self._drop_pending()
            raise
        if r.get("error"):
            self._drop_pending()
            return {"result": "error", "error": r["error"], "problems": r.get("problems") or []}
        if r.get("result") in ("confirmed", "unchanged"):
            self._promote()
        self.log(f"network: change {tid[:8]} -> {r.get('result')}")
        return {"result": r.get("result"), "id": tid}

    def confirm(self) -> bool | None:
        """True kept, False nothing (any more) to keep, None = no answer from the agent (the next status tells)."""
        with self._lock:
            pid = self._pending_id()
            if pid is None or not self.managed():
                return False
            try:
                ok = bool(self.client.call("confirm", id=pid).get("ok"))
            except AgentUnavailable:
                return None                                              # pending file kept: _sync_pending reconciles
            if ok:
                self._promote()
            return ok

    def revert(self) -> bool | None:
        """True undone, False nothing to undo, None = no answer from the agent."""
        with self._lock:
            if not self.managed():
                return False
            try:
                ok = bool(self.client.call("revert").get("ok"))
            except AgentUnavailable as e:
                if not e.sent:
                    self._drop_pending()                                 # never reached the agent (it is down: a restart rolls back)
                return None
            self._drop_pending()
            return ok

    def scan(self) -> bool:
        if not self.managed():
            return False
        try:
            return bool(self.client.call("scan").get("ok"))
        except AgentUnavailable:
            return False

    def apply_hostname(self) -> None:
        """Called before the mDNS publisher restarts with a new name (LocalAccess.before_rename): the system host name and Avahi's
        own name follow first, so Avahi itself publishes the name and keeps its addresses current on every interface."""
        if not self.managed():
            return
        try:
            self.client.call("hostname")
        except AgentUnavailable:
            self.log("network: host name not applied (network agent not reachable)")

    def diagnostics(self) -> dict:
        try:
            cfg, err = netconfig.redact(self.config()), None
        except netconfig.NetConfigError as e:
            cfg, err = None, e.problems
        return {"status": self.status(), "config": cfg, "config_error": err, "pending_change": os.path.exists(self.pending_path)}

    # ---- follow the network: certificate check after the first time sync, mDNS/certificates after address changes
    def poll(self) -> dict:
        st = self.status()
        synced = st.get("time_synchronized")
        key = tuple(sorted(a for i in st.get("interfaces", []) for a in i["addresses"]))
        acc = self.access
        if acc is not None:
            if synced and self._synced is False:
                self.log("network: clock synchronised - re-checking the HTTPS certificate")
                acc.ensure_certificates()
            if self._addr_key is not None and key != self._addr_key and hasattr(acc, "network_changed"):
                acc.network_changed()
        self._synced, self._addr_key = synced, key
        if st != self._last:
            self._last = st
            for fn in list(self.on_change):
                try:
                    fn(st)
                except Exception as e:
                    self.log(f"network listener error: {type(e).__name__}: {e}")
        return st

    def start(self) -> None:
        if os.path.exists(self.pending_path) and not self.managed():
            self._drop_pending()                                         # a pending change can only exist while an agent tries it
        threading.Thread(target=self._watch, daemon=True, name="network-watch").start()

    def _watch(self) -> None:
        while not self._stop.wait(WATCH_S):
            try:
                self.poll()
            except Exception as e:
                self.log(f"network watch error: {type(e).__name__}: {e}")

    def stop(self) -> None:
        self._stop.set()
