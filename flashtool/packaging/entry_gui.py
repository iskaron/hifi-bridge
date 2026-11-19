"""PyInstaller entry of the GUI executable ``hifi-bridge-flash`` (unprivileged)."""
import sys

from hififlash.gui.app import main

sys.exit(main())
