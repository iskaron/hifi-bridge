"""Entry point of the GUI (``hifi-bridge-flash`` in the packages; ``python3 -m hififlash.gui`` in development).

    hifi-bridge-flash [--simulate DIR] [--lang en|de] [--theme light|dark|system] [--self-test]

``--simulate DIR`` lists and writes only SIMULATED cards (``DIR/*.card`` regular files) through the real helper protocol, in process;
``--self-test`` builds the whole window offscreen-capable and exits 0 (package smoke test). The GUI never runs as root/administrator."""
from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import theme
from .i18n import I18n, resolve
from .services import default_services
from .window import LOGO, MainWindow


def main(argv=None, services=None) -> int:
    ap = argparse.ArgumentParser(prog="hifi-bridge-flash")
    ap.add_argument("--simulate", metavar="DIR")
    ap.add_argument("--lang", choices=("en", "de", "system"))
    ap.add_argument("--theme", choices=theme.MODES)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not a.self_test:
        print("hifi-bridge-flash: do not run the GUI as root - only the write helper is elevated", file=sys.stderr)
        return 2
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Hi-Fi Bridge")
    app.setWindowIcon(QIcon(LOGO))
    services = services or default_services(simulate=a.simulate)
    if a.lang:
        services.settings.values["lang"] = a.lang
    if a.theme:
        services.settings.values["theme"] = a.theme
    win = MainWindow(services, I18n(resolve(services.settings["lang"])))
    win.apply_theme()
    app.styleHints().colorSchemeChanged.connect(lambda _: win.apply_theme())       # "follow the system"
    if a.self_test:
        app.processEvents()
        print(f"self-test ok: {len(win.pages)} pages, language {win.i18n.lang}")
        return 0
    win.show()
    return app.exec()
