"""Hi-Fi Bridge flash/provisioning tool - CORE LOGIC ONLY (phase-2b preparation, P9).

No GUI yet (PySide6 is the provisional choice) and no code path that writes a real disk: writing is implemented against the ``Target``
interface, and this package only ships the simulated ``FileTarget``. Real device enumeration parses the outputs of lsblk / diskutil /
PowerShell, but the commands themselves are never run by the tests or on the development machine (docs/FLASH_TOOL.md).
"""
__version__ = "0.1.0-dev"
