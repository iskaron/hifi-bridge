"""Settings (language, theme, release source, publisher keys) and About (licences, LGPL notice for Qt/PySide6, source offer)."""
from __future__ import annotations

import html
import os
import sys

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout)

from btbridge import SOURCE_URL

from .. import __version__
from .. import catalog as C
from ..keystore import KeyStoreError, fingerprint
from . import theme

QT_SOURCE = "https://download.qt.io/official_releases/qt/"
PYSIDE_SOURCE = "https://download.qt.io/official_releases/QtForPython/pyside6/"
LGPL_URL = "https://www.gnu.org/licenses/lgpl-3.0.html"


class SettingsDialog(QDialog):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx
        tr = ctx.i18n.tr
        self.setWindowTitle(tr("settings.title"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.lang = QComboBox()
        for v in ("system", "en", "de"):
            self.lang.addItem(tr(f"settings.lang.{v}"), v)
        self.lang.setCurrentIndex(max(0, self.lang.findData(ctx.services.settings["lang"])))
        self.theme = QComboBox()
        for v in theme.MODES:
            self.theme.addItem(tr(f"settings.theme.{v}"), v)
        self.theme.setCurrentIndex(max(0, self.theme.findData(ctx.services.settings["theme"])))
        self.source = QLineEdit(ctx.services.settings["source"])
        for key, w in (("settings.lang", self.lang), ("settings.theme", self.theme), ("settings.source", self.source)):
            lab = QLabel(tr(key))
            lab.setBuddy(w)
            w.setAccessibleName(tr(key))
            form.addRow(lab, w)
        lay.addLayout(form)
        self.source_hint = QLabel(tr("settings.source.hint"))
        self.source_hint.setWordWrap(True)
        self.source_hint.setProperty("role", "muted")
        lay.addWidget(self.source_hint)
        keys_label = QLabel(tr("settings.keys"))
        self.keys = QListWidget()
        self.keys.setAccessibleName(tr("settings.keys"))
        keys_label.setBuddy(self.keys)
        lay.addWidget(keys_label)
        lay.addWidget(self.keys)
        row = QHBoxLayout()
        self.key_name = QLineEdit()
        self.key_name.setPlaceholderText(tr("settings.key.name"))
        self.key_name.setAccessibleName(tr("settings.key.name"))
        self.add = QPushButton(tr("settings.key.add"))
        self.remove = QPushButton(tr("settings.key.remove"))
        for w in (self.key_name, self.add, self.remove):
            row.addWidget(w)
        lay.addLayout(row)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)            # error details come from files the user picked
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        lay.addWidget(buttons)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.add.clicked.connect(self.add_key)
        self.remove.clicked.connect(self.remove_key)
        self.fill_keys()

    def fill_keys(self) -> None:
        tr = self.ctx.i18n.tr
        self.keys.clear()
        for k in self.ctx.services.keys.keys():
            item = QListWidgetItem(tr(f"settings.key.{k.kind}", name=k.name, fingerprint=k.fingerprint))
            item.setData(Qt.ItemDataRole.UserRole, k)
            self.keys.addItem(item)
        if not self.keys.count():
            self.keys.addItem(tr("settings.keys.none"))

    def add_key(self) -> None:
        tr, svc = self.ctx.i18n.tr, self.ctx.services
        path = svc.dialogs.open_file(tr("settings.key.add"), "PEM (*.pem *.pub);;*")
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                pem = fh.read(16384)
            fp = fingerprint(pem)
            name = self.key_name.text().strip() or os.path.splitext(os.path.basename(path))[0]
            # explicit, informed decision: the fingerprint is shown and must be confirmed (compare it with the publisher's)
            if not svc.dialogs.confirm(tr("settings.key.confirm.title"), tr("settings.key.confirm", name=name, fingerprint=fp)):
                return
            svc.keys.add_custom(name, pem)
            self.message.setText(tr("settings.key.added", name=name))
        except (OSError, KeyStoreError) as e:
            self.message.setText(tr("settings.key.error", detail=str(e)))
        self.fill_keys()

    def remove_key(self) -> None:
        tr = self.ctx.i18n.tr
        item = self.keys.currentItem()
        k = item.data(Qt.ItemDataRole.UserRole) if item else None
        if k is None or k.kind != "custom":
            self.message.setText(tr("settings.key.only_custom"))
            return
        self.ctx.services.keys.remove_custom(k.name)
        self.fill_keys()

    def save(self) -> None:
        tr, s = self.ctx.i18n.tr, self.ctx.services.settings
        source = self.source.text().strip() or C.DEFAULT_SOURCE
        try:
            C.check_url(source)
        except C.SourceError:
            self.message.setText(tr("settings.source.invalid"))
            return
        s.set("source", source)
        s.set("lang", self.lang.currentData())
        s.set("theme", self.theme.currentData())
        self.accept()


def about_html(tr) -> str:
    e = html.escape
    return (f"<h2 style='font-family:serif'>Hi-Fi Bridge – {e(tr('app.eyebrow'))}</h2>"
            f"<p>{e(tr('about.version', version=__version__))}<br>{e(tr('about.licence'))} "
            f"<a href='{SOURCE_URL}'>{e(SOURCE_URL)}</a></p>"
            f"<h3>Qt {e(qVersion())} / PySide6 {e(PYSIDE_VERSION)}</h3><p>{e(tr('about.qt'))}</p>"
            f"<p><a href='{LGPL_URL}'>{LGPL_URL}</a><br><a href='{QT_SOURCE}'>{QT_SOURCE}</a><br><a href='{PYSIDE_SOURCE}'>{PYSIDE_SOURCE}</a></p>"
            f"<h3>{e(tr('about.others'))}</h3><p>{e(tr('about.others.text'))}</p>"
            f"<p>{e(tr('about.notices', path=notices_path()))}</p>"
            f"<p>{e(tr('about.trademark'))}</p>")


def notices_path() -> str:
    """THIRD_PARTY_NOTICES.txt in a package (next to the executable; in a macOS .app under Contents/Resources, because Contents/MacOS
    holds only signed code), or the repository's docs/THIRD_PARTY.md in development."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if sys.platform == "darwin":
            return os.path.join(os.path.dirname(exe_dir), "Resources", "licenses", "THIRD_PARTY_NOTICES.txt")
        return os.path.join(exe_dir, "licenses", "THIRD_PARTY_NOTICES.txt")
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "THIRD_PARTY.md"))


class AboutDialog(QDialog):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        tr = ctx.i18n.tr
        self.setWindowTitle(tr("about.title"))
        self.resize(620, 560)
        lay = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setAccessibleName(tr("about.title"))
        self.text.setHtml(about_html(tr))
        lay.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.about_qt = buttons.addButton(tr("about.qt_button"), QDialogButtonBox.ButtonRole.HelpRole)
        self.about_qt.clicked.connect(lambda: QMessageBox.aboutQt(self))
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
