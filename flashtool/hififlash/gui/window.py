"""Main window: header (logo, product name), step list, the wizard pages, Back/Next. Writing can only be left by finishing, failing or
cancelling; closing the window while writing asks first and cancels (the helper then invalidates the card)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import theme
from .dialogs import AboutDialog, SettingsDialog
from .i18n import I18n, resolve
from .pages import BoardPage, ConfirmPage, DiskPage, DonePage, ImagePage, ProvisionPage, State, WritePage, set_state
from .services import Services

LOGO = os.path.join(os.path.dirname(__file__), "assets", "logo.svg")


@dataclass
class Ctx:
    i18n: I18n
    services: Services
    state: State


class MainWindow(QMainWindow):
    def __init__(self, services: Services, i18n: I18n | None = None):
        super().__init__()
        self.services = services
        self.i18n = i18n or I18n(resolve(services.settings["lang"]))
        self.ctx = Ctx(self.i18n, services, State())
        self._close_after_write = False
        self.resize(QSize(1040, 760))
        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        # header
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 12, 20, 12)
        logo = QSvgWidget(LOGO)
        logo.setFixedSize(40, 40)
        logo.setAccessibleName("Hi-Fi Bridge logo")
        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QLabel("Hi-Fi Bridge")
        name.setObjectName("brand")
        name.setFont(theme.serif_font(16))
        self.eyebrow = QLabel()
        self.eyebrow.setObjectName("eyebrow")
        brand.addWidget(name)
        brand.addWidget(self.eyebrow)
        hl.addWidget(logo)
        hl.addLayout(brand)
        hl.addStretch(1)
        self.sim = QLabel()
        set_state(self.sim, "warn")
        self.sim.setVisible(services.simulated)
        hl.addWidget(self.sim)
        self.settings_btn = QPushButton()
        self.about_btn = QPushButton()
        hl.addWidget(self.settings_btn)
        hl.addWidget(self.about_btn)
        rl.addWidget(header)
        # body: steps + pages
        body = QHBoxLayout()
        body.setSpacing(0)
        steps = QFrame()
        steps.setObjectName("steps")
        steps.setFixedWidth(210)
        sl = QVBoxLayout(steps)
        sl.setContentsMargins(12, 18, 12, 18)
        self.pages = [BoardPage(self.ctx), ImagePage(self.ctx), DiskPage(self.ctx), ProvisionPage(self.ctx), ConfirmPage(self.ctx),
                      WritePage(self.ctx), DonePage(self.ctx)]
        self.step_labels = []
        for _ in self.pages:
            lab = QLabel()
            self.step_labels.append(lab)
            sl.addWidget(lab)
        sl.addStretch(1)
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)
            p.changed.connect(self.update_nav)
        body.addWidget(steps)
        body.addWidget(self.stack, 1)
        rl.addLayout(body, 1)
        # footer
        footer = QFrame()
        footer.setObjectName("footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 10, 20, 10)
        self.back_btn = QPushButton()
        self.next_btn = QPushButton()
        self.next_btn.setProperty("primary", True)
        self.next_btn.setDefault(True)
        fl.addWidget(self.back_btn)
        fl.addStretch(1)
        fl.addWidget(self.next_btn)
        rl.addWidget(footer)
        self.setCentralWidget(root)
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        self.settings_btn.clicked.connect(self.open_settings)
        self.about_btn.clicked.connect(self.open_about)
        self.pages[5].finished.connect(self._written)
        self.pages[6].restart.connect(self.restart)
        self.i18n.changed.connect(self.retranslate)
        self.retranslate()
        self.go(0)

    # -- navigation
    @property
    def index(self) -> int:
        return self.stack.currentIndex()

    def page(self):
        return self.pages[self.index]

    def go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.pages[i].enter()
        self.update_nav()
        self.pages[i].heading.setFocus(Qt.FocusReason.OtherFocusReason)

    def next(self) -> None:
        p = self.page()
        if not p.is_complete() or p.busy() or not p.leave_forward():
            return
        self.go(self.index + 1)

    def back(self) -> None:
        if self.index > 0 and not self.page().busy() and self.index < 6:
            self.go(self.index - 1)

    def _written(self) -> None:
        if self._close_after_write:
            self.close()
            return
        self.go(6)

    def restart(self) -> None:
        self.ctx.state.__init__()
        self.go(0)

    def any_busy(self) -> bool:
        return any(p.busy() for p in self.pages)

    def update_nav(self) -> None:
        p, i = self.page(), self.index
        self.back_btn.setEnabled(0 < i < 6 and not p.busy())
        self.next_btn.setVisible(i < 5)
        self.next_btn.setEnabled(p.is_complete() and not p.busy())
        self.next_btn.setText(self.i18n.tr(p.next_key))
        self.next_btn.setAccessibleName(self.next_btn.text())
        for n, lab in enumerate(self.step_labels):
            lab.setProperty("current", n == i)
            lab.setProperty("done", n < i)
            lab.style().unpolish(lab)
            lab.style().polish(lab)

    def retranslate(self) -> None:
        tr = self.i18n.tr
        self.setWindowTitle(tr("app.title"))
        self.eyebrow.setText(tr("app.eyebrow").upper())
        self.sim.setText(tr("app.simulated"))
        for b, key in ((self.settings_btn, "nav.settings"), (self.about_btn, "nav.about"), (self.back_btn, "nav.back")):
            b.setText(tr(key))
            b.setAccessibleName(tr(key))
        for n, (lab, p) in enumerate(zip(self.step_labels, self.pages), 1):
            lab.setText(f"{n}  {tr(p.title_key + '.step')}")
        self.update_nav()

    # -- settings, about, theme
    def apply_theme(self) -> None:
        theme.apply(QApplication.instance(), self.services.settings["theme"])

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.ctx)
        if dlg.exec():
            self.apply_settings()

    def apply_settings(self) -> None:
        self.apply_theme()
        self.i18n.set_lang(resolve(self.services.settings["lang"]))
        self.pages[1].entries = []                              # the source may have changed
        self.page().refresh()
        self.update_nav()

    def open_about(self) -> None:
        AboutDialog(self, self.ctx).exec()

    # -- closing
    def closeEvent(self, event) -> None:
        write = self.pages[5]
        if write.busy():
            if self.services.dialogs.confirm(self.i18n.tr("close.title"), self.i18n.tr("close.writing")):
                self._close_after_write = True
                write.request_cancel()
                write.task.failed.connect(lambda _: self.close())
            event.ignore()
            return
        for p in self.pages:
            if p.busy() and getattr(p, "task", None):
                p.task.cancel.set()
        event.accept()
