"""Stable pseudonyms for Bluetooth addresses in traces (default ON): reports must not carry third parties' MAC addresses."""
from __future__ import annotations

import re

# hex-boundaries instead of \b: in BlueZ object paths the address follows "dev_" ("_" is a word character, so \b would not match)
_MAC = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{2})([:_])([0-9a-f]{2})\2([0-9a-f]{2})\2([0-9a-f]{2})\2([0-9a-f]{2})\2([0-9a-f]{2})(?![0-9a-f])")


class Anonymizer:
    def __init__(self, enabled: bool = True, keep: tuple[str, ...] = ()):
        self.enabled, self.map, self.keep = enabled, {}, {k.upper().replace("_", ":") for k in keep}

    def mac(self, mac: str) -> str:
        m = mac.upper().replace("_", ":")
        if not self.enabled or m in self.keep:
            return m
        if m not in self.map:
            n = len(self.map) + 1
            self.map[m] = f"02:00:00:00:{(n >> 8) & 0xFF:02X}:{n & 0xFF:02X}"     # locally administered range
        return self.map[m]

    def text(self, s: str) -> str:
        if not self.enabled:
            return s
        def sub(m):
            sep = m.group(2)
            return self.mac(":".join(m.group(i) for i in (1, 3, 4, 5, 6, 7))).replace(":", sep)
        return _MAC.sub(sub, s)

    def value(self, v):
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, dict):
            return {self.text(k) if isinstance(k, str) else k: self.value(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [self.value(x) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return {"bytes_hex": bytes(v).hex()}
        return v
