"""Everything the GUI talks to, injectable for tests: settings, trusted keys, the release source and download cache, unprivileged disk
enumeration, the helper channel, the P7 trust check and the native dialogs. Tests replace the dialogs and use SIMULATED cards and a
loopback release server; nothing here starts a process by itself (the helper is started only by ``channel_factory`` when writing)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from .. import catalog as C
from .. import trust as FT
from ..helper import client as CL
from ..keystore import KeyStore
from ..platforms import adapter_for

DEFAULTS = {"lang": "system", "theme": "system", "source": C.DEFAULT_SOURCE}


class Settings:
    """``settings.json`` in the user's config directory (language, theme, release source). Unknown or invalid values fall back."""

    def __init__(self, path: str):
        self.path = path
        self.values = dict(DEFAULTS)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k in DEFAULTS:
                    if isinstance(data.get(k), str):
                        self.values[k] = data[k]
        except (OSError, ValueError):
            pass

    def __getitem__(self, k: str) -> str:
        return self.values[k]

    def set(self, k: str, v: str) -> None:
        self.values[k] = v
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.values, fh, indent=1)


class Dialogs:
    """Native dialogs (file choosers, confirmation, printing, opening URLs). Tests use a scripted fake with the same methods."""

    def __init__(self, parent_getter: Callable[[], object] = lambda: None):
        self.parent = parent_getter

    def open_file(self, title: str, filters: str) -> str | None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self.parent(), title, "", filters)
        return path or None

    def save_file(self, title: str, name: str, filters: str) -> str | None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self.parent(), title, name, filters)
        return path or None

    def confirm(self, title: str, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox
        return QMessageBox.question(self.parent(), title, text) == QMessageBox.StandardButton.Yes

    def print_document(self, document) -> bool:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter()
        if QPrintDialog(printer, self.parent()).exec():
            document.print_(printer)
            return True
        return False

    def open_url(self, url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))


@dataclass
class Services:
    settings: Settings
    keys: KeyStore
    cache_dir: str
    list_disks: Callable[[], list]
    channel_factory: Callable[[], CL.Channel]
    dialogs: Dialogs = field(default_factory=Dialogs)
    opener: Callable = C.urllib.request.urlopen
    fetch_verified_ca: Callable = FT.fetch_verified_ca
    check_https: Callable = FT.check_https
    simulated: bool = False

    def list_entries(self) -> list[C.Entry]:
        return C.list_entries(self.settings["source"], opener=self.opener)

    def fetch_manifest(self, entry: C.Entry):
        return C.fetch_manifest(entry, opener=self.opener)

    def downloader(self) -> C.Downloader:
        return C.Downloader(self.cache_dir, opener=self.opener)


def user_dirs(app_name: str = "hifi-bridge-flash") -> tuple[str, str]:
    """(config dir, cache dir) from QStandardPaths."""
    from PySide6.QtCore import QStandardPaths
    base_cfg = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    base_cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    return os.path.join(base_cfg, app_name), os.path.join(base_cache, app_name)


def default_services(simulate: str | None = None, config_dir: str | None = None, cache_dir: str | None = None,
                     platform: str = sys.platform) -> Services:
    cfg, cache = user_dirs()
    cfg, cache = config_dir or cfg, cache_dir or cache
    adapter, _ = adapter_for(platform, simulate)
    return Services(settings=Settings(os.path.join(cfg, "settings.json")), keys=KeyStore(os.path.join(cfg, "publishers")),
                    cache_dir=os.path.join(cache, "images"), list_disks=adapter.list_disks,
                    channel_factory=CL.channel_factory(platform, simulate), simulated=bool(simulate))
