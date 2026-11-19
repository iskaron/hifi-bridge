"""PyInstaller entry of the privileged write helper ``hififlash-helper`` (no Qt; started elevated by the GUI only for the write job)."""
from hififlash.helper.__main__ import run_and_exit

run_and_exit()
