"""Offscreen screenshots of the flash tool GUI (scripts/run_flashtool.sh screenshots) in light/dark and English/German.

Everything shown is SIMULATED: a throw-away "official" test key, a loopback release server, simulated cards (regular files) and the real
helper protocol in process. The header carries the "SIMULATION" badge so a screenshot is never mistaken for a real run.
Output: reports/screenshots/flashtool-<page>-<theme>-<lang>.png"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt                     # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

from guienv import Env                            # noqa: E402
from hififlash.gui import theme                   # noqa: E402
from hififlash.gui.dialogs import SettingsDialog  # noqa: E402
from hififlash.gui.i18n import I18n               # noqa: E402
from hififlash.gui.window import MainWindow       # noqa: E402


def wait(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("screenshot step timed out")
        time.sleep(0.01)
    QApplication.processEvents()


def shoot(widget, out: str, name: str, mode: str, lang: str) -> str:
    QApplication.processEvents()
    path = os.path.join(out, f"flashtool-{name}-{mode}-{lang}.png")
    widget.grab().save(path)
    return path


def run(out: str, mode: str, lang: str, full: bool) -> list[str]:
    app = QApplication.instance()
    theme.apply(app, mode)
    shots = []
    with tempfile.TemporaryDirectory() as tmp:
        env = Env(tmp)
        try:
            win = MainWindow(env.services, I18n(lang))
            win.resize(1040, 760)
            win.show()
            p = win.pages
            if full:
                shots.append(shoot(win, out, "1-board", mode, lang))
            p[0].radios["rpi3"].setChecked(True)
            win.next()
            wait(lambda: p[1].list.count() == 1 and not p[1].busy())
            p[1].list.setCurrentRow(0)
            p[1].run_verify()
            wait(lambda: win.ctx.state.image is not None)
            shots.append(shoot(win, out, "2-image", mode, lang))
            win.next()
            wait(lambda: p[2].list.count() == 5 and not p[2].busy())
            item = next(p[2].list.item(i) for i in range(5) if p[2].list.item(i).data(Qt.ItemDataRole.UserRole).name == "sdcard")
            p[2].list.setCurrentItem(item)
            shots.append(shoot(win, out, "3-card", mode, lang))
            win.next()
            p[3].country.setCurrentIndex(p[3].country.findData("DE"))
            p[3].name.setText("Wohnzimmer")
            p[3].host.setText("wohnzimmer")
            p[3].wifi.setChecked(True)
            p[3].ssid.setText("Zuhause")
            p[3].psk.setText("example-passphrase")
            if full:
                shots.append(shoot(win, out, "4-setup", mode, lang))
            win.next()
            p[4].typed.setText("sdcard")
            if full:
                shots.append(shoot(win, out, "5-confirm", mode, lang))
            win.next()
            wait(lambda: win.index == 6)
            win.ctx.state.ca_der = b"simulated"                       # as after a successful (here: simulated) verification
            p[6].trust_status = ("trust.ok", {"fingerprint": "3F:A2:…:9C (simulated)"}, "ok")
            p[6].refresh()
            shots.append(shoot(win, out, "7-done", mode, lang))
            if full:
                env.set_card_meta(fail_after=2 * 1024 * 1024)
                win.go(5)
                wait(lambda: p[5].fail.isVisible())
                shots.append(shoot(win, out, "6-write-failed", mode, lang))
                dlg = SettingsDialog(win, win.ctx)
                dlg.resize(620, 520)
                dlg.show()
                shots.append(shoot(dlg, out, "settings", mode, lang))
                dlg.close()
            win.close()
        finally:
            env.close()
    return shots


def main(argv=None) -> int:
    out = (argv or sys.argv[1:] or ["reports/screenshots"])[0]
    os.makedirs(out, exist_ok=True)
    QApplication.instance() or QApplication([])
    shots = []
    for mode, lang in (("light", "en"), ("dark", "en"), ("light", "de"), ("dark", "de")):
        shots += run(out, mode, lang, full=(mode, lang) in (("light", "en"), ("dark", "de")))
    print("\n".join(shots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
