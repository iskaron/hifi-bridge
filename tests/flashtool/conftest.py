import subprocess

import pytest

from oshelpers import tools  # noqa: F401  (fixture; tests/os is on PYTHONPATH)


@pytest.fixture
def no_subprocess(monkeypatch):
    """Fails the test if any process is started (CLAUDE.md: never run lsblk/diskutil/PowerShell or anything else on the host)."""
    def refuse(*a, **k):
        raise AssertionError(f"a test tried to start a process {a[:1]} - not allowed here (CLAUDE.md)")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
