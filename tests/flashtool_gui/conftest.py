import gc
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from guienv import Env  # noqa: E402

from hififlash.gui import theme  # noqa: E402
from hififlash.gui.i18n import I18n  # noqa: E402
from hififlash.gui.window import MainWindow  # noqa: E402


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    """The GUI tests never start a process (CLAUDE.md: no lsblk/diskutil/PowerShell/pkexec on the host)."""
    def refuse(*a, **k):
        raise AssertionError(f"a GUI test tried to start a process {a[:1]} - not allowed (CLAUDE.md)")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)


@pytest.fixture(autouse=True, scope="session")
def gc_in_the_gui_thread_only():
    """Like the app (gui.app.gui_thread_gc): no automatic GC, which could free Qt objects in a worker thread and crash Qt."""
    was = gc.isenabled()
    gc.disable()
    yield
    gc.collect()
    if was:
        gc.enable()


@pytest.fixture(autouse=True)
def collect_after_each_test():
    yield
    gc.collect()


@pytest.fixture
def env(tmp_path):
    e = Env(str(tmp_path))
    yield e
    e.close()


@pytest.fixture
def win(qtbot, qapp, env):
    w = MainWindow(env.services, I18n("en"))
    theme.apply(qapp, "light")
    qtbot.addWidget(w)
    w.show()
    yield w
    for p in w.pages:
        if getattr(p, "task", None) is not None:
            p.task.cancel.set()
    qtbot.waitUntil(lambda: not w.any_busy(), timeout=20000)
