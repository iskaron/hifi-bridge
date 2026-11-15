"""System metrics from procfs/sysfs - read-only, stdlib, cheap enough for a Pi Zero 2 W (one sample = a handful of small reads).

All paths are injectable: tests and the Docker lab use fake trees, so no host hardware is read during development.
Thermal thresholds are provisional (Raspberry Pi firmware throttles from ~80 C; warning 75 C, critical 80 C) and must be checked on
hardware (plan E4). Throttling flags (``vcgencmd get_throttled``) are Pi-specific and not read here (hardware item)."""
from __future__ import annotations

import os
import time


class SystemMonitor:
    def __init__(self, proc: str = "/proc", thermal: str | None = "/sys/class/thermal/thermal_zone0/temp", data_dir: str = "/",
                 warn_c: float = 75.0, crit_c: float = 80.0, clock=time.monotonic):
        self.proc, self.thermal, self.data_dir = proc, thermal, data_dir
        self.warn_c, self.crit_c, self.clock = warn_c, crit_c, clock
        self._cpu_prev: tuple[int, int] | None = None
        self.last: dict = {}

    def _read(self, *parts: str) -> str | None:
        try:
            with open(os.path.join(*parts), encoding="ascii", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    def _cpu(self) -> float | None:
        txt = self._read(self.proc, "stat")
        if not txt:
            return None
        f = [int(x) for x in txt.splitlines()[0].split()[1:]]
        idle, total = f[3] + (f[4] if len(f) > 4 else 0), sum(f[:8])
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if prev is None or total <= prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def _mem(self) -> dict | None:
        txt = self._read(self.proc, "meminfo")
        if not txt:
            return None
        kv = {line.split(":")[0]: int(line.split()[1]) for line in txt.splitlines() if ":" in line and len(line.split()) > 1}
        total, avail = kv.get("MemTotal"), kv.get("MemAvailable", kv.get("MemFree"))
        if not total or avail is None:
            return None
        return {"total_mb": round(total / 1024, 1), "available_mb": round(avail / 1024, 1), "used_percent": round(100 * (1 - avail / total), 1)}

    def _temp(self) -> float | None:
        if not self.thermal:
            return None
        txt = self._read(self.thermal)
        try:
            return round(int(txt.strip()) / 1000.0, 1) if txt else None
        except ValueError:
            return None

    def _net(self) -> dict:
        out = {}
        txt = self._read(self.proc, "net", "dev") or ""
        for line in txt.splitlines()[2:]:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            name, f = name.strip(), rest.split()
            if name == "lo" or len(f) < 9:
                continue
            out[name] = {"rx_bytes": int(f[0]), "tx_bytes": int(f[8])}
        wl = self._read(self.proc, "net", "wireless") or ""
        for line in wl.splitlines()[2:]:
            if ":" in line:
                name, rest = line.split(":", 1)
                f = rest.split()
                try:
                    out.setdefault(name.strip(), {})["wifi_signal_dbm"] = float(f[2].rstrip("."))
                except (IndexError, ValueError):
                    pass
        return out

    def thermal_state(self, temp: float | None) -> str:
        if temp is None:
            return "unknown"
        return "critical" if temp >= self.crit_c else ("warning" if temp >= self.warn_c else "ok")

    def sample(self) -> dict:
        load = (self._read(self.proc, "loadavg") or "").split()
        up = (self._read(self.proc, "uptime") or "").split()
        rss = next((line.split()[1] for line in (self._read(self.proc, "self", "status") or "").splitlines()
                    if line.startswith("VmRSS:")), None)
        try:
            st = os.statvfs(self.data_dir)
            storage = {"total_mb": round(st.f_blocks * st.f_frsize / 2**20, 1), "free_mb": round(st.f_bavail * st.f_frsize / 2**20, 1)}
            storage["used_percent"] = round(100 * (1 - st.f_bavail / st.f_blocks), 1) if st.f_blocks else None
        except OSError:
            storage = None
        temp = self._temp()
        self.last = {
            "cpu_percent": self._cpu(),
            "load": [float(x) for x in load[:3]] if len(load) >= 3 else None,
            "memory": self._mem(),
            "temperature_c": temp,
            "thermal_state": self.thermal_state(temp),
            "uptime_s": int(float(up[0])) if up else None,
            "storage": storage,
            "network": self._net(),
            "process_rss_mb": round(int(rss) / 1024, 1) if rss else None,
            "sampled_at": time.time(),
        }
        return self.last
