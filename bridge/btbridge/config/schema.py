"""Typed settings schema with defaults and validation.  One source of truth for the store, the WebUI forms and MQTT.

Defaults follow docs/DECISIONS.md: pause `drop_restart` (#15), ICY off (#18), override on (#5), pairing window 5 min (#2)."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Callable


class SettingError(ValueError):
    def __init__(self, key: str, code: str, detail: str = ""):
        super().__init__(f"{key}: {code}{(' - ' + detail) if detail else ''}")
        self.key, self.code, self.detail = key, code, detail


_HOST = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")
_TOPIC = re.compile(r"^[A-Za-z0-9_\-/]{1,64}$")
_PLAYER = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$|^[A-Za-z0-9._:\-]{1,64}$")
_MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def _host(v: str) -> str:
    v = v.strip()
    if v == "":
        return v
    try:
        return str(ipaddress.ip_address(v.strip("[]")))
    except ValueError:
        pass
    if not _HOST.match(v):
        raise ValueError("invalid_host")
    return v.lower()


def _device_name(v: str) -> str:
    v = " ".join(v.split())
    if not 1 <= len(v) <= 32 or any(ord(c) < 32 for c in v):
        raise ValueError("invalid_name")
    return v


def _player(v: str) -> str:
    v = v.strip()
    if v and not _PLAYER.match(v):
        raise ValueError("invalid_player")
    return v.lower() if re.match(r"^[0-9a-fA-F:]{17}$", v) else v


def _topic(v: str) -> str:
    v = v.strip().strip("/")
    if not _TOPIC.match(v) or "//" in v:
        raise ValueError("invalid_topic")
    return v


def _instance_id(v: str) -> str:
    v = v.strip().lower()
    if v and not re.fullmatch(r"[a-z0-9]{6,16}", v):
        raise ValueError("invalid_instance_id")
    return v


def _labels(v: Any) -> dict:
    if not isinstance(v, dict):
        raise ValueError("invalid_labels")
    out = {}
    for k, name in v.items():
        k = str(k).upper()
        if not _MAC.match(k):
            raise ValueError("invalid_address")
        name = " ".join(str(name).split())[:40]
        if name:
            out[k] = name
    return out


@dataclass(frozen=True)
class Setting:
    key: str
    type: type
    default: Any
    choices: tuple | None = None
    min: int | None = None
    max: int | None = None
    check: Callable[[Any], Any] | None = None
    group: str = "general"
    restart: bool = False          # needs a runtime rebuild when changed (e.g. LMS server)


SETTINGS: dict[str, Setting] = {s.key: s for s in [
    Setting("device_name", str, "Hi-Fi Bridge", check=_device_name, group="bluetooth"),
    Setting("pairing_seconds", int, 300, min=60, max=1800, group="bluetooth"),
    Setting("audio_allowed", bool, True, group="playback"),
    Setting("override_lms", bool, True, group="playback"),
    Setting("pause_strategy", str, "drop_restart", choices=("drop_restart", "keepalive_silence"), group="playback"),
    Setting("stream_format", str, "wav", choices=("wav", "flac"), group="playback", restart=True),
    Setting("icy_metadata", bool, False, group="playback", restart=True),
    Setting("lms_host", str, "", check=_host, group="lyrion", restart=True),
    Setting("lms_port", int, 9000, min=1, max=65535, group="lyrion", restart=True),
    Setting("target_player", str, "", check=_player, group="lyrion"),
    Setting("target_label", str, "", check=lambda v: " ".join(v.split())[:80], group="lyrion"),
    Setting("language", str, "auto", choices=("auto", "de", "en"), group="ui"),
    Setting("theme", str, "auto", choices=("auto", "light", "dark"), group="ui"),
    Setting("mqtt_enabled", bool, False, group="mqtt", restart=True),
    Setting("mqtt_host", str, "", check=_host, group="mqtt", restart=True),
    Setting("mqtt_port", int, 1883, min=1, max=65535, group="mqtt", restart=True),
    Setting("mqtt_username", str, "", group="mqtt", restart=True),
    Setting("mqtt_tls", bool, False, group="mqtt", restart=True),
    Setting("mqtt_base_topic", str, "btbridge", check=_topic, group="mqtt", restart=True),
    Setting("mqtt_discovery_prefix", str, "homeassistant", check=_topic, group="mqtt", restart=True),
    Setting("device_labels", dict, {}, check=_labels, group="bluetooth"),
    Setting("setup_complete", bool, False, group="system"),
    Setting("instance_id", str, "", check=_instance_id, group="system"),
]}


def coerce(s: Setting, value: Any) -> Any:
    """Convert form/JSON input to the setting's type; raises SettingError."""
    try:
        if s.type is bool:
            if isinstance(value, bool):
                v = value
            elif str(value).lower() in ("1", "true", "on", "yes"):
                v = True
            elif str(value).lower() in ("0", "false", "off", "no", ""):
                v = False
            else:
                raise ValueError("invalid_bool")
        elif s.type is int:
            if isinstance(value, bool):
                raise ValueError("invalid_int")
            try:
                v = int(str(value).strip())
            except ValueError:
                raise ValueError("invalid_int") from None
        elif s.type is str:
            if not isinstance(value, (str, int, float)):
                raise ValueError("invalid_str")
            v = str(value)
            if len(v) > 256:
                raise ValueError("too_long")
        else:
            v = value
        if s.check:
            v = s.check(v)
    except ValueError as e:
        code = str(e) if re.fullmatch(r"(invalid|too)_[a-z_]+", str(e)) else "invalid"     # never leak Python messages
        raise SettingError(s.key, code) from None
    if s.choices is not None and v not in s.choices:
        raise SettingError(s.key, "invalid_choice")
    if s.min is not None and v < s.min:
        raise SettingError(s.key, "too_small", str(s.min))
    if s.max is not None and v > s.max:
        raise SettingError(s.key, "too_large", str(s.max))
    return v


def validate(changes: dict) -> dict:
    """Validate a dict of changes; unknown keys are rejected (never silently stored)."""
    out = {}
    for k, v in changes.items():
        if k not in SETTINGS:
            raise SettingError(k, "unknown_setting")
        out[k] = coerce(SETTINGS[k], v)
    return out


def defaults() -> dict:
    return {k: (dict(s.default) if isinstance(s.default, dict) else s.default) for k, s in SETTINGS.items()}
