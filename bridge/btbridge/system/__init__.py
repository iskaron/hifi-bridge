"""System health: CPU, temperature, memory, storage, uptime, network (procfs/sysfs; paths injectable for tests)."""
from .monitor import SystemMonitor

__all__ = ["SystemMonitor"]
