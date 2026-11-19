"""PyInstaller entry of the privileged write helper ``hififlash-helper`` (no Qt; started elevated by the GUI only for the write job)."""
import sys

from hififlash.helper.__main__ import main

sys.exit(main())
