"""Atomic, versioned JSON persistence for settings and secrets.

* ``settings.json``  - non-secret settings (schema in ``schema.py``), safe to include (redacted) in diagnostics.
* ``secrets.json``   - mode 0600: admin password hash, session key, stream token, MQTT password, setup-code hash.
Writes go to a temp file in the same directory, are fsynced and renamed (power-loss safe on POSIX file systems);
a corrupt file (not decodable as a JSON object) is moved aside (``*.corrupt-<ts>``) and defaults are used, so a broken file never
bricks the device. An I/O error (permissions, EIO) is NOT corruption: it raises ``ConfigUnreadable`` instead of silently resetting
the device (review M8).

OTA rollback safety (review M8): keys this version does not know, stored values it cannot validate, and a higher
``schema_version`` written by a newer version are kept verbatim and written back unchanged, so a later upgrade finds them again.
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import threading
import time
from typing import Any, Callable

from .schema import SETTINGS, defaults, validate

SCHEMA_VERSION = 1
SECRET_KEYS = ("admin_password_hash", "setup_code_hash", "session_key", "stream_token", "mqtt_password", "trust_key")


def _atomic_write(path: str, data: dict, mode: int) -> None:
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp-{os.getpid()}-{threading.get_ident()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ConfigUnreadable(OSError):
    """A configuration file exists but cannot be read (permissions, I/O error). Never treated as corruption."""


def _load(path: str, log) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)                     # UnicodeDecodeError and JSONDecodeError are both ValueError
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ConfigUnreadable(e.errno, f"{os.path.basename(path)} unreadable: {e.strerror or e}", path) from e
    except ValueError as e:
        aside = f"{path}.corrupt-{int(time.time())}"
        try:
            os.replace(path, aside)
        except OSError:
            pass
        log(f"config: {os.path.basename(path)} unreadable ({type(e).__name__}); moved to {os.path.basename(aside)}, using defaults")
        return None


def stored_version(data: dict) -> int | None:
    """The file's schema version: 0 when absent (early development file), None when malformed (e.g. "1.0")."""
    if "schema_version" not in data:
        return 0
    v = data["schema_version"]
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def migrate(data: dict) -> dict:
    """Schema migrations (v0 -> v1: unversioned file from early development). Never lowers the version."""
    ver = stored_version(data)
    if ver == 0:
        data = {k: v for k, v in data.items() if k in SETTINGS}
    data["schema_version"] = max(SCHEMA_VERSION, ver or 0)
    return data


class ConfigStore:
    def __init__(self, data_dir: str, log: Callable[[str], None] = lambda m: None):
        self.dir, self.log = data_dir, log
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.secrets_path = os.path.join(data_dir, "secrets.json")
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        raw = _load(self.settings_path, log) or {}
        if stored_version(raw) is None:
            log(f"config: malformed schema_version {raw.get('schema_version')!r}; treated as version {SCHEMA_VERSION}")
            raw = dict(raw, schema_version=SCHEMA_VERSION)
        self._settings = defaults()
        from_file = stored_version(raw) if raw else None
        stored = migrate(dict(raw))
        self._version = stored["schema_version"]
        self._kept: dict[str, Any] = {}                 # unknown keys and unusable values, written back verbatim (rollback)
        for k, v in stored.items():
            if k == "schema_version":
                continue
            if k not in SETTINGS:
                self._kept[k] = v
                continue
            try:
                self._settings.update(validate({k: v}))
            except ValueError as e:                     # invalid stored value -> default at runtime, file value kept
                log(f"config: ignoring stored value ({e})")
                if from_file != 0:                      # a v0 development file is migrated, not preserved
                    self._kept[k] = v
        sec = _load(self.secrets_path, log) or {}
        self._secrets = {k: v for k, v in sec.items() if k in SECRET_KEYS}
        self._kept_secrets = {k: v for k, v in sec.items() if k not in SECRET_KEYS}
        if "session_key" not in self._secrets or "stream_token" not in self._secrets:
            self._secrets.setdefault("session_key", _secrets.token_hex(32))
            self._secrets.setdefault("stream_token", _secrets.token_urlsafe(24))
            self._save_secrets()
        if raw.get("schema_version") != SCHEMA_VERSION:
            self._save_settings()

    # ---- settings ----
    def get(self, key: str) -> Any:
        with self._lock:
            v = self._settings[key]
            return dict(v) if isinstance(v, dict) else v

    def all(self) -> dict:
        with self._lock:
            return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._settings.items()}

    def update(self, changes: dict) -> dict:
        """Validate all changes first (all-or-nothing), persist, notify listeners with the keys that really changed."""
        clean = validate(changes)
        with self._lock:
            changed = {k: v for k, v in clean.items() if self._settings.get(k) != v}
            if not changed:
                return {}
            self._settings.update(changed)
            for k in changed:
                self._kept.pop(k, None)                 # an explicit change replaces a kept value
            self._save_settings()
        for fn in list(self._listeners):
            try:
                fn(dict(changed))
            except Exception as e:                      # a listener must never break persistence
                self.log(f"config listener error: {type(e).__name__}: {e}")
        return changed

    def on_change(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def _save_settings(self) -> None:
        _atomic_write(self.settings_path, {**self._settings, **self._kept, "schema_version": self._version}, 0o600)

    # ---- secrets ----
    def secret(self, key: str) -> str | None:
        if key not in SECRET_KEYS:                       # explicit check (an assert would vanish with python -O)
            raise KeyError(key)
        with self._lock:
            return self._secrets.get(key)

    def set_secret(self, key: str, value: str | None) -> None:
        if key not in SECRET_KEYS:
            raise KeyError(key)
        with self._lock:
            if value is None:
                self._secrets.pop(key, None)
            else:
                self._secrets[key] = value
            self._save_secrets()

    def _save_secrets(self) -> None:
        _atomic_write(self.secrets_path, {**self._kept_secrets, **self._secrets}, 0o600)

    def export_redacted(self) -> dict:
        """For diagnostics bundles: settings + which secrets exist (never their values)."""
        with self._lock:
            return {"settings": self.all(), "secrets_present": sorted(self._secrets)}
