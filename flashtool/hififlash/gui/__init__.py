"""PySide6 GUI of the flash tool (docs/FLASH_TOOL.md). The GUI has no write logic of its own: it drives the tested core (catalogue,
image verification, provisioning, trust) and hands the one write job to the privileged helper (``hififlash.helper``). It always runs
unprivileged."""
