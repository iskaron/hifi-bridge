"""Owner authentication for the WebUI (stdlib only).

* No default or shared password (docs/PRODUCT_SPEC.md "No universal credentials").  On first start a random **setup code** is created;
  on the appliance it comes from the flash tool (phase 2b), in development it is written to ``<data>/setup-code.txt`` (mode 0600) and logged
  once.  The setup wizard requires it to set the admin password; afterwards it is deleted.
* Passwords: PBKDF2-HMAC-SHA256, per-password random salt, iteration count stored with the hash (tunable for a Pi Zero 2 W).
* Sessions: random 256-bit ids held in memory (a restart logs out; browsers can store the password), sliding idle timeout, absolute
  lifetime, one CSRF token per session.  Cookies: ``HttpOnly; SameSite=Strict; Path=/`` (+ ``Secure`` when served over TLS).
* Brute force: per-client exponential back-off after 5 failures (also for setup-code attempts and password changes); IPv6 clients
  are keyed by their /64 prefix; a global failure budget limits guesses spread over many addresses (review M4).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time

ITERATIONS = 200_000                 # ~0.1 s on x86; must be re-measured on a Pi Zero 2 W (docs/OPEN_QUESTIONS.md)
MIN_PASSWORD = 10


class AuthError(Exception):
    def __init__(self, code: str, retry_after: float = 0.0):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


def hash_password(pw: str, iterations: int = ITERATIONS, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored:
        hashlib.pbkdf2_hmac("sha256", pw.encode(), b"x" * 16, ITERATIONS)     # equalise timing when no password is set
        return False
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        cand = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(salt), int(it))
        return hmac.compare_digest(cand, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def password_problems(pw: str) -> list[str]:
    p = []
    if len(pw) < MIN_PASSWORD:
        p.append("too_short")
    if len(pw) > 256:
        p.append("too_long")
    if pw.strip() != pw or not pw.strip():
        p.append("whitespace")
    if len(set(pw)) < 4:
        p.append("too_simple")
    return p


def new_setup_code() -> str:
    """12 characters from an unambiguous alphabet, shown as XXXX-XXXX-XXXX."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalise_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def limit_key(client: str) -> str:
    """Back-off key for a client address: IPv6 by /64 (one host can rotate through its whole prefix), IPv4 as is."""
    try:
        a = ipaddress.ip_address(client)
    except ValueError:
        return client
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


class RateLimiter:
    GLOBAL = "*"

    def __init__(self, free: int = 5, base_s: float = 2.0, max_s: float = 300.0, clock=time.monotonic,
                 global_free: int = 50, global_max_s: float = 60.0, global_quiet_s: float = 900.0):
        self.free, self.base, self.max, self.clock = free, base_s, max_s, clock
        self.global_free, self.global_max, self.global_quiet = global_free, global_max_s, global_quiet_s
        self._fails: dict[str, tuple[int, float]] = {}
        self._global = (0, 0.0, 0.0)                     # (failures, blocked until, last failure)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            n, until = self._fails.get(key, (0, 0.0))
            now = self.clock()
            if until > now:
                raise AuthError("rate_limited", until - now)
            gn, guntil, _ = self._global
            if guntil > now:
                raise AuthError("rate_limited", guntil - now)

    def fail(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            delay = 0.0 if n < self.free else min(self.max, self.base * 2 ** min(n - self.free, 64))     # capped exponent: 2**1100 overflows a float
            self._fails[key] = (n, now + delay)
            if len(self._fails) > 10_000:                     # bound memory
                self._fails.pop(next(iter(self._fails)))
            gn, _, last = self._global
            gn = 1 if now - last > self.global_quiet else gn + 1     # the budget refills after a quiet period
            gdelay = 0.0 if gn < self.global_free else min(self.global_max, self.base * 2 ** min(gn - self.global_free, 64))
            self._global = (gn, now + gdelay, now)

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._global = (0, 0.0, 0.0)


class Sessions:
    def __init__(self, idle_s: float = 8 * 3600, absolute_s: float = 30 * 24 * 3600, clock=time.monotonic):
        # monotonic: a Pi has no RTC, so the wall clock jumps at the NTP sync after boot (review L8: mass logouts)
        self.idle, self.absolute, self.clock = idle_s, absolute_s, clock
        self._s: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, str]:
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = self.clock()
        with self._lock:
            self._s[sid] = {"csrf": csrf, "created": now, "seen": now}
            for k in [k for k, v in self._s.items() if not self._valid(v, now)]:
                del self._s[k]
        return sid, csrf

    def _valid(self, s: dict, now: float) -> bool:
        return now - s["seen"] < self.idle and now - s["created"] < self.absolute

    def get(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        now = self.clock()
        with self._lock:
            s = self._s.get(sid)
            if s is None or not self._valid(s, now):
                self._s.pop(sid, None)
                return None
            s["seen"] = now
            return dict(s)

    def check_csrf(self, sid: str | None, token: str | None) -> bool:
        s = self.get(sid)
        return bool(s and token and hmac.compare_digest(s["csrf"], token))

    def destroy(self, sid: str | None) -> None:
        with self._lock:
            self._s.pop(sid or "", None)

    def destroy_all(self) -> None:
        with self._lock:
            self._s.clear()


class Auth:
    """Ties the config store's secrets to setup, login and password change."""

    def __init__(self, store, log=lambda m: None, iterations: int = ITERATIONS, limiter: RateLimiter | None = None,
                 sessions: Sessions | None = None):
        self.store, self.log, self.iterations = store, log, iterations
        self.limiter = limiter or RateLimiter()
        self.sessions = sessions or Sessions()
        self.code_file = os.path.join(store.dir, "setup-code.txt")
        if not self.has_password() and not store.secret("setup_code_hash"):
            self._new_code()

    def has_password(self) -> bool:
        return bool(self.store.secret("admin_password_hash"))

    def _new_code(self) -> str:
        code = new_setup_code()
        self.store.set_secret("setup_code_hash", hash_password(normalise_code(code), self.iterations))
        fd = os.open(self.code_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(code + "\n")
        self.log(f"setup code created (see {self.code_file}); required once to set the owner password")
        return code

    def complete_setup(self, client: str, code: str, password: str) -> tuple[str, str]:
        if self.has_password():
            raise AuthError("already_set_up")
        key = limit_key(client)
        self.limiter.check(key)
        if not verify_password(normalise_code(code), self.store.secret("setup_code_hash")):
            self.limiter.fail(key)
            raise AuthError("bad_setup_code")
        probs = password_problems(password)
        if probs:
            raise AuthError("weak_password:" + ",".join(probs))
        self.limiter.ok(key)
        self.store.set_secret("admin_password_hash", hash_password(password, self.iterations))
        self.store.set_secret("setup_code_hash", None)
        self.store.set_secret("trust_key", None)          # P7: the flash tool's one-time device-verification key ends with the setup
        try:
            os.unlink(self.code_file)
        except OSError:
            pass
        self.log("owner password set; setup code and device trust key invalidated")
        return self.sessions.create()

    def login(self, client: str, password: str) -> tuple[str, str]:
        key = limit_key(client)
        self.limiter.check(key)
        if not verify_password(password, self.store.secret("admin_password_hash")):
            self.limiter.fail(key)
            raise AuthError("bad_password")
        self.limiter.ok(key)
        return self.sessions.create()

    def change_password(self, old: str, new: str, client: str = "") -> None:
        """A stolen session must not become an unlimited password oracle: wrong old passwords back off like logins (review M4)."""
        key = limit_key(client)
        self.limiter.check(key)
        if not verify_password(old, self.store.secret("admin_password_hash")):
            self.limiter.fail(key)
            raise AuthError("bad_password")
        self.limiter.ok(key)
        probs = password_problems(new)
        if probs:
            raise AuthError("weak_password:" + ",".join(probs))
        self.store.set_secret("admin_password_hash", hash_password(new, self.iterations))
        self.sessions.destroy_all()                        # every other session must log in again

    def adopt_setup_code(self, code: str) -> None:
        """P6 provisioning: use the owner-known code from the flash tool / bootstrap file. Its plaintext is NOT stored on the device
        (the owner already has it); an existing generated code file is removed."""
        if self.has_password():
            raise AuthError("already_set_up")
        self.store.set_secret("setup_code_hash", hash_password(normalise_code(code), self.iterations))
        try:
            os.unlink(self.code_file)
        except OSError:
            pass
        self.log("setup code taken from the provisioning file")

    def reset_with_new_setup_code(self) -> str:
        """Recovery path (console/flash tool only, never via the network): forget the password, issue a new setup code."""
        self.store.set_secret("admin_password_hash", None)
        self.sessions.destroy_all()
        return self._new_code()
