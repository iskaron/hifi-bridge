"""The wizard pages: board -> image -> card -> device setup -> confirmation -> writing -> done. Pages hold no write logic; they call the
core and hand the write job to the privileged helper. Every text comes from the DE/EN dictionaries and is re-applied on a language
change (``Page.bind``/``refresh``)."""
from __future__ import annotations

import html
import os
import re
import ssl
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPdfWriter, QTextDocument
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QRadioButton, QStackedWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from btbridge import provisioning as P
from btbridge import trustproof
from btbridge.config import schema
from btbridge.net import netconfig as NC

from .. import __version__
from .. import boards as BD
from .. import catalog as C
from .. import disks as D
from .. import images as IM
from .. import provision as FP
from ..helper import client as CL
from ..helper import protocol as PR
from . import ownersheet
from .tasks import Task
from .theme import serif_font

COUNTRIES = ("AT", "AU", "BE", "BG", "CA", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT",
             "JP", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "NZ", "PL", "PT", "RO", "SE", "SI", "SK", "US", "ZA")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,250}[A-Za-z0-9])?(:[0-9]{1,5})?$")
MB = 1000 ** 2


@dataclass
class State:
    board: BD.Board | None = None
    image: IM.VerifiedImage | None = None
    disk: D.Disk | None = None
    allow_large: bool = False
    doc: dict | None = None
    trust_key: bytes | None = None
    result: dict | None = None
    ca_der: bytes | None = None


def set_state(label: QLabel, state: str) -> None:
    """Colour a status label (ok / warn / error / '') through the style sheet."""
    label.setProperty("state", state)
    label.style().unpolish(label)
    label.style().polish(label)


def plain(label: QLabel) -> QLabel:
    """Labels that show untrusted text (a card's model string comes from the device; error details from the network) must never be
    interpreted as rich text - QLabel would otherwise auto-detect HTML."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def panel(kind: str) -> QFrame:
    f = QFrame()
    f.setProperty("panel", kind)
    QVBoxLayout(f)
    return f


def error_text(tr, e: Exception) -> str:
    """A translated message for every failure the core can report (the English detail is appended for support)."""
    if isinstance(e, C.SourceError):
        return tr(f"src.err.{e.code}") + (f" ({e.detail})" if e.detail else "")
    if isinstance(e, IM.ImageError):
        return tr(f"img.err.{e.code}") + (f" ({e.detail})" if e.detail else "")
    if isinstance(e, C.DownloadCancelled):
        return tr("img.err.cancelled")
    if isinstance(e, C.DownloadError):
        return tr("img.err.download", detail=str(e))
    if isinstance(e, CL.HelperFailed):
        key = f"write.err.{e.code}" if e.code in PR.ERROR_CODES + ("elevation_refused", "helper_died", "protocol") else "write.err.internal"
        reasons = ", ".join(tr(f"disk.reason.{r}") for r in e.reasons)
        return tr(key) + (f" ({reasons})" if reasons else "") + "\n" + tr("detail", detail=e.message)
    return tr("err.unexpected", detail=f"{type(e).__name__}: {e}")


class Page(QWidget):
    changed = Signal()
    title_key = ""
    next_key = "nav.next"

    def __init__(self, ctx):
        super().__init__()
        self.ctx, self.state = ctx, ctx.state
        self._binds: list = []
        self.setObjectName("page")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(28, 22, 28, 22)
        self.lay.setSpacing(12)
        self.heading = QLabel()
        self.heading.setFont(serif_font(20))
        self.heading.setAccessibleName("heading")
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setProperty("role", "muted")
        self.bind(self.heading, f"{self.title_key}.title")
        self.bind(self.intro, f"{self.title_key}.intro")
        self.lay.addWidget(self.heading)
        self.lay.addWidget(self.intro)
        ctx.i18n.changed.connect(self.retranslate)

    def tr(self, key: str, **kw) -> str:
        return self.ctx.i18n.tr(key, **kw)

    def bind(self, widget, key: str, method: str = "setText", **kw):
        self._binds.append((widget, method, key, kw))
        getattr(widget, method)(self.tr(key, **kw))
        if method == "setText" and isinstance(widget, (QPushButton, QCheckBox, QRadioButton)):
            self._binds.append((widget, "setAccessibleName", key, kw))
            widget.setAccessibleName(self.tr(key, **kw))
        return widget

    def labelled(self, key: str, widget: QWidget) -> QLabel:
        lab = QLabel()
        self.bind(lab, key)
        self.bind(widget, key, "setAccessibleName")
        lab.setBuddy(widget)
        return lab

    def retranslate(self) -> None:
        for w, method, key, kw in self._binds:
            getattr(w, method)(self.tr(key, **kw))
        self.refresh()

    def refresh(self) -> None:
        """Dynamic texts (status, lists) - pages override it."""

    def is_complete(self) -> bool:
        return True

    def busy(self) -> bool:
        return False

    def enter(self) -> None:
        self.refresh()

    def leave_forward(self) -> bool:
        return True


# ------------------------------------------------------------------ 1 board
class BoardPage(Page):
    title_key = "board"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.group = QButtonGroup(self)
        self.radios = {}
        for b in BD.BOARDS:
            card = QFrame()
            card.setProperty("card", True)
            cl = QVBoxLayout(card)
            rb = QRadioButton(b.name)
            rb.setFont(serif_font(13))
            rb.setAccessibleName(b.name)
            desc = QLabel()
            desc.setWordWrap(True)
            desc.setProperty("role", "muted")
            self.bind(desc, f"board.{b.id}.desc")
            self.bind(rb, f"board.{b.id}.desc", "setAccessibleDescription")
            cl.addWidget(rb)
            cl.addWidget(desc)
            self.group.addButton(rb)
            self.radios[b.id] = rb
            rb.toggled.connect(lambda on, b=b: self._choose(b) if on else None)
            self.lay.addWidget(card)
        note = QLabel()
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        self.bind(note, "board.note")
        self.lay.addWidget(note)
        self.lay.addStretch(1)

    def _choose(self, b: BD.Board) -> None:
        if self.state.board != b:
            self.state.board = b
            if self.state.image and self.state.image.board != b:
                self.state.image = None
        self.changed.emit()

    def is_complete(self) -> bool:
        return self.state.board is not None


# ------------------------------------------------------------------ 2 image
class ImagePage(Page):
    title_key = "image"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.entries: list[C.Entry] = []
        self.task: Task | None = None
        self.status: tuple = ("image.none", {}, "")
        modes = QHBoxLayout()
        self.official = self.bind(QRadioButton(), "image.official")
        self.custom = self.bind(QRadioButton(), "image.custom")
        self.official.setChecked(True)
        modes.addWidget(self.official)
        modes.addWidget(self.custom)
        modes.addStretch(1)
        self.lay.addLayout(modes)
        self.stack = QStackedWidget()
        # official releases
        off = QWidget()
        ol = QVBoxLayout(off)
        ol.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.source = QLabel()
        self.source.setProperty("role", "muted")
        self.source.setWordWrap(True)
        self.reload = self.bind(QPushButton(), "image.reload")
        row.addWidget(self.source, 1)
        row.addWidget(self.reload)
        ol.addLayout(row)
        self.list = QListWidget()
        self.bind(self.list, "image.list", "setAccessibleName")
        ol.addWidget(self.list)
        self.stack.addWidget(off)
        # custom image
        cus = QWidget()
        grid = QGridLayout(cus)
        grid.setContentsMargins(0, 0, 0, 0)
        self.paths = {}
        for i, (k, filt) in enumerate((("image", "img"), ("manifest", "manifest"), ("signature", "sig"))):
            edit = QLineEdit()
            btn = self.bind(QPushButton(), "image.browse")
            self.bind(btn, f"image.browse.{k}", "setAccessibleName")
            grid.addWidget(self.labelled(f"image.file.{k}", edit), i, 0)
            grid.addWidget(edit, i, 1)
            grid.addWidget(btn, i, 2)
            btn.clicked.connect(lambda _=False, k=k: self._browse(k))
            edit.textChanged.connect(self._custom_changed)
            self.paths[k] = edit
        self.stack.addWidget(cus)
        self.lay.addWidget(self.stack, 1)
        # action + result
        act = QHBoxLayout()
        self.verify = self.bind(QPushButton(), "image.download")
        self.verify.setProperty("primary", True)
        self.cancel = self.bind(QPushButton(), "nav.cancel")
        self.cancel.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bind(self.bar, "image.progress", "setAccessibleName")
        act.addWidget(self.verify)
        act.addWidget(self.cancel)
        act.addWidget(self.bar, 1)
        self.lay.addLayout(act)
        self.result = plain(QLabel())
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result.setAccessibleName("status")
        self.lay.addWidget(self.result)
        self.official.toggled.connect(self._mode)
        self.reload.clicked.connect(self.load)
        self.verify.clicked.connect(self.run_verify)
        self.cancel.clicked.connect(lambda: self.task and self.task.cancel.set())
        self.list.currentRowChanged.connect(lambda _: self._buttons())

    # -- helpers
    def _mode(self) -> None:
        self.stack.setCurrentIndex(0 if self.official.isChecked() else 1)
        self.refresh()
        self.changed.emit()

    def _browse(self, kind: str) -> None:
        filters = {"image": "image (*.img *.img.xz *.img.gz *.xz *.gz);;*", "manifest": "manifest (*.json);;*", "signature": "signature (*.sig);;*"}
        path = self.ctx.services.dialogs.open_file(self.tr(f"image.file.{kind}"), filters[kind])
        if path:
            self.paths[kind].setText(path)

    def _custom_changed(self) -> None:
        m, s = self.paths["manifest"].text().strip(), self.paths["signature"]
        if m and not s.text().strip() and os.path.isfile(m + ".sig"):
            s.setText(m + ".sig")
        self._buttons()

    def _set_status(self, key: str, state: str = "", **kw) -> None:
        self.status = (key, kw, state)
        self.refresh()

    def _visible_entries(self) -> list[C.Entry]:
        b = self.state.board
        return [e for e in self.entries if b and e.image.name.startswith(f"hifi-bridge-{b.compatible}-")]

    def refresh(self) -> None:
        self.source.setText(self.tr("image.source", source=self.ctx.services.settings["source"]))
        self.verify.setText(self.tr("image.download" if self.official.isChecked() else "image.verify"))
        self.verify.setAccessibleName(self.verify.text())
        row = self.list.currentRow()
        self.list.clear()
        for e in self._visible_entries():
            text = self.tr("image.entry", release=e.release, name=e.image.name, size=f"{e.image.size / MB:.0f}")
            if e.prerelease:
                text += "  ·  " + self.tr("image.prerelease")
            if e.signature is None:
                text += "  ·  " + self.tr("image.unsigned")
            self.list.addItem(text)
        self.list.setCurrentRow(row)
        key, kw, state = self.status
        if key == "raw":
            self.result.setText(kw["text"])
        else:
            self.result.setText(self.tr(key, **kw))
        set_state(self.result, state)
        self._buttons()

    def _buttons(self) -> None:
        busy = self.busy()
        for w in (self.verify, self.reload, self.official, self.custom, self.list, *self.paths.values()):
            w.setEnabled(not busy)
        self.cancel.setEnabled(busy)
        if not busy:
            self.verify.setEnabled(self._can_verify())

    def _can_verify(self) -> bool:
        if self.official.isChecked():
            return 0 <= self.list.currentRow() < len(self._visible_entries())
        return bool(self.paths["image"].text().strip() and self.paths["manifest"].text().strip())

    def busy(self) -> bool:
        return bool(self.task and self.task.running)

    def is_complete(self) -> bool:
        return bool(self.state.image and self.state.image.board == self.state.board) and not self.busy()

    def enter(self) -> None:
        if self.state.image is None and not self.busy():
            n = len(self._visible_entries())
            self.status = (("image.loaded" if n else "image.none_for_board") if self.entries else "image.none", {"count": n}, "")
        if self.official.isChecked() and not self.entries and not self.busy():
            self.load()
        self.refresh()

    # -- work
    def _start(self, fn, ok, status_key: str) -> None:
        self.task = Task(fn, self)
        self.task.progress.connect(self._progress)
        self.task.succeeded.connect(ok)
        self.task.failed.connect(self._failed)
        self.bar.setValue(0)
        self.task.start()
        self._set_status(status_key)
        self.changed.emit()

    def load(self) -> None:
        self._start(lambda cancel, progress: self.ctx.services.list_entries(), self._loaded, "image.loading")

    def _loaded(self, entries) -> None:
        self.entries = entries
        self._set_status("image.loaded" if self._visible_entries() else "image.none_for_board",
                         count=len(self._visible_entries()))
        self.changed.emit()

    def _progress(self, stage: str, done, total) -> None:
        self.bar.setValue(int(1000 * done / total) if total else 0)
        self.result.setText(self.tr(f"image.stage.{stage}", done=f"{done / MB:.0f}", total=f"{total / MB:.0f}"))
        set_state(self.result, "")

    def _failed(self, e: Exception) -> None:
        self.state.image = None
        self.status = ("raw", {"text": error_text(self.tr, e)}, "error")
        self.refresh()
        self.changed.emit()

    def run_verify(self) -> None:
        s, board = self.ctx.services, self.state.board
        self.state.image = None
        if self.official.isChecked():
            entry = self._visible_entries()[self.list.currentRow()]

            def fn(cancel, progress):
                raw, sig = s.fetch_manifest(entry)
                m, pub = IM.check_manifest(raw, sig, s.keys, board)          # refuse an untrusted image BEFORE downloading it
                path = s.downloader().fetch(entry.image, cancel, lambda d, t: progress("download", d, t))
                IM.hash_image(path, m, cancel, lambda d, t: progress("verify", d, t))
                return IM.VerifiedImage(path, m, raw, pub, board)
        else:
            img, man, sigp = (self.paths[k].text().strip() for k in ("image", "manifest", "signature"))

            def fn(cancel, progress):
                try:
                    with open(man, "rb") as fh:
                        raw = fh.read(C.MAX_MANIFEST + 1)
                    sig = None
                    if sigp:
                        with open(sigp, "rb") as fh:
                            sig = fh.read(C.MAX_SIG + 1)
                except OSError as e:
                    raise IM.ImageError("unreadable", str(e)) from None
                return IM.verify_image(img, raw, sig, s.keys, board, cancel, lambda d, t: progress("verify", d, t))
        self._start(fn, self._verified, "image.checking")

    def _verified(self, v: IM.VerifiedImage) -> None:
        self.state.image = v
        key = "image.ok.official" if v.publisher.kind == "official" else "image.ok.custom"
        self._set_status(key, "ok" if v.publisher.kind == "official" else "warn", version=v.manifest["version"],
                         publisher=v.publisher.name, fingerprint=v.publisher.fingerprint[:23] + "…")
        self.bar.setValue(1000)
        self.changed.emit()


# ------------------------------------------------------------------ 3 card
class DiskPage(Page):
    title_key = "disk"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.disks: list[D.Disk] = []
        self.task: Task | None = None
        self.error = ""
        row = QHBoxLayout()
        self.reload = self.bind(QPushButton(), "disk.reload")
        self.large = self.bind(QCheckBox(), "disk.large")
        row.addWidget(self.reload)
        row.addWidget(self.large)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bind(self.list, "disk.list", "setAccessibleName")
        self.lay.addWidget(self.list, 1)
        self.details = plain(QLabel())
        self.details.setWordWrap(True)
        self.details.setAccessibleName("status")
        self.lay.addWidget(self.details)
        self.reload.clicked.connect(self.load)
        self.large.toggled.connect(self._large)
        self.list.currentItemChanged.connect(self._select)

    def _size(self) -> int:
        return self.state.image.manifest["size"] if self.state.image else 0

    def _large(self, on: bool) -> None:
        self.state.allow_large = on
        self.refresh()
        self.changed.emit()

    def load(self) -> None:
        self.task = Task(lambda cancel, progress: self.ctx.services.list_disks(), self)
        self.task.succeeded.connect(self._loaded)
        self.task.failed.connect(self._failed)
        self.reload.setEnabled(False)
        self.task.start()

    def _loaded(self, disks) -> None:
        self.disks, self.error = list(disks), ""
        if self.state.disk and not any(D.same_disk(self.state.disk, d) for d in self.disks):
            self.state.disk = None                                    # the selected card is gone or was replaced
        self.reload.setEnabled(True)
        self.refresh()
        self.changed.emit()

    def _failed(self, e: Exception) -> None:
        self.disks, self.error, self.state.disk = [], error_text(self.tr, e), None
        self.reload.setEnabled(True)
        self.refresh()
        self.changed.emit()

    def _select(self, item: QListWidgetItem | None) -> None:
        d = item.data(Qt.ItemDataRole.UserRole) if item else None
        if d is not None and not D.problems(d, self._size(), self.state.allow_large):
            self.state.disk = d
        self._details()
        self.changed.emit()

    def _details(self) -> None:
        d = self.state.disk
        if self.error:
            self.details.setText(self.error)
            set_state(self.details, "error")
        elif not self.disks or not any(not D.problems(x, self._size(), self.state.allow_large) for x in self.disks):
            self.details.setText(self.tr("disk.none"))
            set_state(self.details, "warn")
        elif d is None:
            self.details.setText(self.tr("disk.choose"))
            set_state(self.details, "")
        else:
            text = self.tr("disk.selected", label=d.label())
            if d.mounts:
                text += "\n" + self.tr("disk.mounted", mounts=", ".join(d.mounts))
            self.details.setText(text)
            set_state(self.details, "")

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        current = None
        for d in self.disks:
            probs = D.problems(d, self._size(), self.state.allow_large)
            status = self.tr("disk.ok") if not probs else self.tr("disk.refused", reasons=", ".join(self.tr(f"disk.reason.{r}") for r in probs))
            item = QListWidgetItem(f"{d.label()}\n{status}")
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{d.label()}. {status}")
            if probs:
                item.setFlags(Qt.ItemFlag.NoItemFlags)                 # shown with its reasons, never selectable
            self.list.addItem(item)
            if self.state.disk and D.same_disk(self.state.disk, d) and not probs:
                current = item
        if self.state.disk and current is None:
            self.state.disk = None
        self.list.setCurrentItem(current)
        self.list.blockSignals(False)
        self._details()

    def busy(self) -> bool:
        return bool(self.task and self.task.running)

    def is_complete(self) -> bool:
        d = self.state.disk
        return d is not None and not D.problems(d, self._size(), self.state.allow_large)

    def enter(self) -> None:
        self.refresh()
        self.load()


# ------------------------------------------------------------------ 4 device setup (provisioning)
class ProvisionPage(Page):
    title_key = "prov"

    def __init__(self, ctx):
        super().__init__(ctx)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.errors: dict[str, QLabel] = {}
        self.name = QLineEdit()
        self.name.setPlaceholderText("Hi-Fi Bridge")
        self.host = QLineEdit()
        self.host.setPlaceholderText("hifi-bridge")
        self.country = QComboBox()
        self.country.addItem("", "")
        for code in COUNTRIES:
            self.country.addItem(code, code)
        self.wifi = self.bind(QCheckBox(), "prov.wifi")
        self.ssid = QLineEdit()
        self.psk = QLineEdit()
        self.psk.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_psk = self.bind(QCheckBox(), "prov.show")
        self.hotspot = self.bind(QCheckBox(), "prov.hotspot")
        self.hotspot.setChecked(True)
        self.hs_psk = QLineEdit()
        self.hs_psk.setEchoMode(QLineEdit.EchoMode.Password)
        for key, w in (("prov.name", self.name), ("prov.host", self.host), ("prov.country", self.country)):
            form.addRow(self.labelled(key, w), self._with_error(key, w))
        form.addRow(self.wifi)
        for key, w in (("prov.ssid", self.ssid), ("prov.psk", self.psk)):
            form.addRow(self.labelled(key, w), self._with_error(key, w))
        form.addRow("", self.show_psk)
        form.addRow(self.hotspot)
        form.addRow(self.labelled("prov.hs_psk", self.hs_psk), self._with_error("prov.hs_psk", self.hs_psk))
        self.lay.addLayout(form)
        note = QLabel()
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        self.bind(note, "prov.note")
        self.lay.addWidget(note)
        self.lay.addStretch(1)
        self.wifi.toggled.connect(self._toggle)
        self.hotspot.toggled.connect(self._toggle)
        self.show_psk.toggled.connect(lambda on: self.psk.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.country.currentIndexChanged.connect(lambda _: self.changed.emit())
        self._toggle()

    def _with_error(self, key: str, w: QWidget) -> QWidget:
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        err = QLabel()
        err.setWordWrap(True)
        err.setVisible(False)
        set_state(err, "error")
        bl.addWidget(w)
        bl.addWidget(err)
        self.errors[key] = err
        return box

    def _toggle(self) -> None:
        for w in (self.ssid, self.psk, self.show_psk):
            w.setEnabled(self.wifi.isChecked())
        self.hs_psk.setEnabled(self.hotspot.isChecked())

    def is_complete(self) -> bool:
        return bool(self.country.currentData())

    def _error(self, key: str, msg_key: str | None) -> bool:
        lab = self.errors[key]
        lab.setText(self.tr(msg_key) if msg_key else "")
        lab.setVisible(bool(msg_key))
        return msg_key is None

    def _schema_ok(self, key: str, value: str) -> bool:
        try:
            schema.validate({key: value})
            return True
        except (ValueError, TypeError):
            return False

    def leave_forward(self) -> bool:
        name, host = self.name.text().strip(), self.host.text().strip()
        country = self.country.currentData()
        ssid, psk, hs = self.ssid.text(), self.psk.text(), self.hs_psk.text()
        wifi = self.wifi.isChecked()
        ok = all([
            self._error("prov.name", None if not name or self._schema_ok("device_name", name) else "prov.err.name"),
            self._error("prov.host", None if not host or self._schema_ok("local_hostname", host) else "prov.err.host"),
            self._error("prov.country", None if NC.country_ok(country) else "prov.err.country"),
            self._error("prov.ssid", None if not wifi or NC.ssid_ok(ssid) else "prov.err.ssid"),
            self._error("prov.psk", None if not wifi or NC.wifi_psk_ok(psk) else "prov.err.psk"),
            self._error("prov.hs_psk", None if not (self.hotspot.isChecked() and hs) or NC.hotspot_psk_ok(hs) else "prov.err.hs_psk"),
        ])
        if not ok:
            return False
        doc, key = FP.new_document(country, device_name=name or None, wifi=(ssid, psk) if wifi else None, hotspot=self.hotspot.isChecked(),
                                   hotspot_psk=hs if self.hotspot.isChecked() and hs else None, tool_version=f"hififlash-gui {__version__}",
                                   local_hostname=host or None)
        self.state.doc, self.state.trust_key = doc, key
        return True


# ------------------------------------------------------------------ 5 confirmation
class ConfirmPage(Page):
    title_key = "confirm"
    next_key = "confirm.write"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setAccessibleName("summary")
        self.lay.addWidget(self.summary)
        self.warn = panel("warn")
        self.warning = plain(QLabel())
        self.warning.setWordWrap(True)
        set_state(self.warning, "warn")
        self.warn.layout().addWidget(self.warning)
        self.lay.addWidget(self.warn)
        self.typed = QLineEdit()
        self.prompt = plain(QLabel())
        self.prompt.setBuddy(self.typed)
        self.lay.addWidget(self.prompt)
        self.lay.addWidget(self.typed)
        self.lay.addStretch(1)
        self.typed.textChanged.connect(lambda _: self.changed.emit())

    def refresh(self) -> None:
        s, e = self.state, html.escape
        if not (s.board and s.image and s.disk and s.doc):
            return
        net = []
        if "wifi" in s.doc:
            net.append(self.tr("confirm.net.wifi", ssid=e(s.doc["wifi"]["ssid"])))
        if "hotspot" in s.doc:
            net.append(self.tr("confirm.net.hotspot", ssid=e(s.doc["hotspot"]["ssid"])))
        rows = [("confirm.board", e(s.board.name)),
                ("confirm.image", e(f"{s.image.manifest['version']} ({os.path.basename(s.image.path)})")),
                ("confirm.publisher", e(self.tr(f"confirm.kind.{s.image.publisher.kind}", name=s.image.publisher.name))),
                ("confirm.disk", e(s.disk.label())),
                ("confirm.device", e(s.doc.get("device_name") or "Hi-Fi Bridge")),
                ("confirm.network", "<br>".join(net) or e(self.tr("confirm.net.ethernet")))]
        self.summary.setText("<table cellspacing=4>" + "".join(f"<tr><td><b>{e(self.tr(k))}</b></td><td>{v}</td></tr>" for k, v in rows)
                             + "</table>")
        warning = self.tr("confirm.warning", disk=s.disk.label())
        if s.disk.mounts:
            warning += " " + self.tr("disk.mounted", mounts=", ".join(s.disk.mounts))
        self.warning.setText(warning)
        self.prompt.setText(self.tr("confirm.type", name=D.confirm_token(s.disk)))
        self.typed.setAccessibleName(self.prompt.text())

    def enter(self) -> None:
        self.typed.clear()
        self.refresh()

    def is_complete(self) -> bool:
        return bool(self.state.disk and D.confirmed(self.state.disk, self.typed.text()))


# ------------------------------------------------------------------ 6 writing
class WritePage(Page):
    title_key = "write"
    finished = Signal()

    def __init__(self, ctx):
        super().__init__(ctx)
        self.task: Task | None = None
        self.stage = QLabel()
        self.stage.setFont(serif_font(14))
        self.stage.setAccessibleName("stage")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bind(self.bar, "write.progress", "setAccessibleName")
        self.amount = QLabel()
        self.amount.setProperty("role", "muted")
        self.cancel = self.bind(QPushButton(), "nav.cancel")
        self.cancel.setProperty("danger", True)
        self.fail = panel("error")
        self.message = plain(QLabel())
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setAccessibleName("error")
        set_state(self.message, "error")
        self.retry = self.bind(QPushButton(), "write.retry")
        self.fail.layout().addWidget(self.message)
        self.fail.layout().addWidget(self.retry)
        self.fail.setVisible(False)
        for w in (self.stage, self.bar, self.amount, self.cancel, self.fail):
            self.lay.addWidget(w)
        self.lay.addStretch(1)
        self.cancel.clicked.connect(self.request_cancel)
        self.retry.clicked.connect(self.start)
        self.stage_key = "write.stage.prepare"

    def busy(self) -> bool:
        return bool(self.task and self.task.running)

    def is_complete(self) -> bool:
        return False

    def refresh(self) -> None:
        self.stage.setText(self.tr(self.stage_key))

    def enter(self) -> None:
        self.start()

    def request_cancel(self) -> None:
        if self.task:
            self.task.cancel.set()
            self.stage_key = "write.cancelling"
            self.refresh()

    def start(self) -> None:
        s, svc = self.state, self.ctx.services
        disk, image = s.disk, s.image
        prov = P.render(s.doc)

        def fn(cancel, progress):
            fresh = next((d for d in svc.list_disks() if d.path == disk.path), None)      # identity re-check BEFORE elevation
            if fresh is None:
                raise CL.HelperFailed("disk_gone", "the selected card is no longer connected - nothing was written")
            if not D.same_disk(disk, fresh):
                raise CL.HelperFailed("disk_changed", "the selected card was replaced by another device - nothing was written")
            req = PR.write_request(disk, image.path, image.manifest, prov, s.allow_large)
            return CL.run_session(svc.channel_factory(), req, progress, cancel)
        self.fail.setVisible(False)
        self.cancel.setEnabled(True)
        self.bar.setValue(0)
        self.amount.setText("")
        self.stage_key = "write.stage.elevate"
        self.refresh()
        self.task = Task(fn, self)
        self.task.progress.connect(self._progress)
        self.task.succeeded.connect(self._done)
        self.task.failed.connect(self._failed)
        self.task.start()
        self.changed.emit()

    def _progress(self, stage: str, done, total) -> None:
        if self.stage_key != "write.cancelling":
            self.stage_key = f"write.stage.{stage}" if stage in ("prepare", "write", "verify") else self.stage_key
        self.bar.setValue(int(1000 * done / total) if total else 0)
        self.amount.setText(self.tr("write.amount", done=f"{done / MB:.0f}", total=f"{total / MB:.0f}"))
        self.refresh()

    def _done(self, result: dict) -> None:
        self.state.result = result
        self.cancel.setEnabled(False)
        self.changed.emit()
        self.finished.emit()

    def _failed(self, e: Exception) -> None:
        self.cancel.setEnabled(False)
        self.stage_key = "write.failed"
        self.message.setText(error_text(self.tr, e))
        self.fail.setVisible(True)
        self.refresh()
        self.changed.emit()


# ------------------------------------------------------------------ 7 done
class DonePage(Page):
    title_key = "done"
    restart = Signal()

    def __init__(self, ctx):
        super().__init__(ctx)
        self.task: Task | None = None
        self.trust_status: tuple = ("trust.idle", {}, "")
        self.sheet = QTextBrowser()
        self.sheet.setOpenLinks(False)
        self.bind(self.sheet, "done.sheet", "setAccessibleName")
        self.lay.addWidget(self.sheet, 2)
        row = QHBoxLayout()
        self.print_btn = self.bind(QPushButton(), "done.print")
        self.save_btn = self.bind(QPushButton(), "done.save")
        row.addWidget(self.print_btn)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        trust = QFrame()
        trust.setProperty("card", True)
        tl = QVBoxLayout(trust)
        tt = QLabel()
        tt.setFont(serif_font(13))
        self.bind(tt, "trust.title")
        ti = QLabel()
        ti.setWordWrap(True)
        ti.setProperty("role", "muted")
        self.bind(ti, "trust.intro")
        self.address = QLineEdit()
        arow = QHBoxLayout()
        arow.addWidget(self.labelled("trust.address", self.address))
        arow.addWidget(self.address, 1)
        self.verify = self.bind(QPushButton(), "trust.verify")
        self.verify.setProperty("primary", True)
        arow.addWidget(self.verify)
        self.status = plain(QLabel())
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setAccessibleName("status")
        brow = QHBoxLayout()
        self.save_ca = self.bind(QPushButton(), "trust.save_ca")
        self.open_web = self.bind(QPushButton(), "trust.open")
        brow.addWidget(self.save_ca)
        brow.addWidget(self.open_web)
        brow.addStretch(1)
        for w in (tt, ti):
            tl.addWidget(w)
        tl.addLayout(arow)
        tl.addWidget(self.status)
        tl.addLayout(brow)
        self.lay.addWidget(trust)
        self.again = self.bind(QPushButton(), "done.again")
        self.lay.addWidget(self.again, 0, Qt.AlignmentFlag.AlignLeft)
        self.print_btn.clicked.connect(self.print_sheet)
        self.save_btn.clicked.connect(self.save_sheet)
        self.verify.clicked.connect(self.run_trust)
        self.save_ca.clicked.connect(self.save_certificate)
        self.open_web.clicked.connect(lambda: self.ctx.services.dialogs.open_url(f"https://{self.address.text().strip()}/"))
        self.again.clicked.connect(self.restart.emit)

    def enter(self) -> None:
        self.address.setText(ownersheet.address(self.state.doc))
        self.trust_status = ("trust.idle", {}, "")
        self.refresh()

    def refresh(self) -> None:
        if self.state.doc:
            self.sheet.setHtml(ownersheet.to_html(self.state.doc, self.tr) + "<hr>" + "<p>" + html.escape(self.tr("done.next")) + "</p>")
        key, kw, st = self.trust_status
        self.status.setText(kw["text"] if key == "raw" else self.tr(key, **kw))
        set_state(self.status, st)
        busy = bool(self.task and self.task.running)
        self.verify.setEnabled(not busy and self.state.trust_key is not None)
        self.save_ca.setEnabled(self.state.ca_der is not None)
        self.open_web.setEnabled(self.state.ca_der is not None)

    def _document(self) -> QTextDocument:
        d = QTextDocument()
        d.setHtml(ownersheet.to_html(self.state.doc, self.tr))
        return d

    def print_sheet(self) -> None:
        self.ctx.services.dialogs.print_document(self._document())

    def save_sheet(self) -> None:
        path = self.ctx.services.dialogs.save_file(self.tr("done.save"), self.tr("done.sheet_file"), "PDF (*.pdf);;Text (*.txt)")
        if not path:
            return
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)        # it holds the setup code and the hotspot password
        os.close(fd)
        if path.lower().endswith(".txt"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(ownersheet.to_text(self.state.doc, self.tr))
        else:
            writer = QPdfWriter(path)
            self._document().print_(writer)
            del writer
        os.chmod(path, 0o600)

    def run_trust(self) -> None:
        host = self.address.text().strip()
        if not HOST_RE.match(host):
            self.trust_status = ("trust.bad_address", {}, "error")
            self.refresh()
            return
        key, svc = self.state.trust_key, self.ctx.services

        def fn(cancel, progress):
            der = svc.fetch_verified_ca(f"http://{host}", key)
            svc.check_https(f"https://{host}", der)                 # HTTPS against ONLY this CA, host name checked
            return der
        self.task = Task(fn, self)
        self.task.succeeded.connect(self._trusted)
        self.task.failed.connect(self._untrusted)
        self.task.start()
        self.trust_status = ("trust.checking", {}, "")
        self.refresh()

    def _trusted(self, der: bytes) -> None:
        self.state.ca_der = der
        self.state.trust_key = None                                  # the key has done its job (docs/TRUST_BOOTSTRAP.md)
        self.trust_status = ("trust.ok", {"fingerprint": trustproof.fingerprint(der)}, "ok")
        self.refresh()

    def _untrusted(self, e: Exception) -> None:
        detail = str(e) or type(e).__name__
        self.trust_status = ("raw", {"text": self.tr("trust.failed", detail=detail)}, "error")
        self.refresh()

    def save_certificate(self) -> None:
        path = self.ctx.services.dialogs.save_file(self.tr("trust.save_ca"), "hifi-bridge-ca.pem", "PEM (*.pem *.crt)")
        if path:
            with open(path, "w") as fh:
                fh.write(ssl.DER_cert_to_PEM_cert(self.state.ca_der))
