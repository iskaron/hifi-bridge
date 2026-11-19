"""GUI quality (offscreen): DE/EN completeness, the WebUI's colour tokens and WCAG contrast, the shared logo, accessibility (names, buddies,
keyboard), live language switching, the entry point (self-test, refusing root, the event loop) and the native dialog wrappers."""
import glob
import json
import os
import re
import runpy
import string

import pytest
from PySide6.QtCore import QLocale, Qt, QTimer
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QLabel, QLineEdit, QListWidget, QProgressBar, QTextBrowser,
                               QWidget)

import flashfixtures as F
from hififlash import boards as BD
from hififlash import catalog as C
from hififlash import disks as D
from hififlash import images as IM
from hififlash.gui import app as APP
from hififlash.gui import i18n as I
from hififlash.gui import services as SV
from hififlash.gui import theme as T
from hififlash.gui.window import LOGO
from hififlash.helper import protocol as PR

from test_wizard import TIMEOUT, choose_card, confirm_and_write, pick_board, provision, verified_official

GUI = os.path.join(os.path.dirname(I.__file__))
ROOT = os.path.abspath(os.path.join(GUI, "..", "..", ".."))
NOT_KEYS = {"settings.json", "logo.svg", "hifi-bridge-ca.pem", "hifi-bridge.local", "hififlash.helper", "image.path"}


def placeholders(s):
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def test_dictionaries_have_the_same_keys_and_placeholders():
    en, de = I.strings("en"), I.strings("de")
    assert set(en) == set(de)
    for k in en:
        assert placeholders(en[k]) == placeholders(de[k]), k
        assert de[k].strip() and en[k].strip()


def dynamic_keys():
    keys = set()
    for p in ("board", "image", "disk", "prov", "confirm", "write", "done"):
        keys |= {f"{p}.title", f"{p}.intro", f"{p}.step"}
    keys |= {f"board.{b.id}.desc" for b in BD.BOARDS}
    keys |= {f"disk.reason.{r}" for r in ("system_disk", "system_mount", "not_removable", "read_only", "in_use_by_volume_manager",
                                          "too_small_for_product", "smaller_than_image", "unusually_large_confirm_explicitly")}
    keys |= {f"write.err.{c}" for c in PR.ERROR_CODES + ("elevation_refused", "helper_died", "protocol")}
    keys |= {f"img.err.{c}" for c in ("unsigned", "untrusted_signature", "bad_manifest", "wrong_board", "hash_mismatch", "size_mismatch",
                                      "unreadable", "cancelled")}
    keys |= {f"src.err.{c}" for c in ("unreachable", "not_found_or_private", "forbidden", "http_error", "bad_response", "bad_url")}
    keys |= {f"image.{a}.{k}" for a in ("file", "browse") for k in ("image", "manifest", "signature")}
    keys |= {"image.stage.download", "image.stage.verify", "write.stage.prepare", "write.stage.write", "write.stage.verify",
             "confirm.kind.official", "confirm.kind.custom", "settings.key.official", "settings.key.custom", "image.ok.official",
             "image.ok.custom"}
    keys |= {"detail"}                                                    # the only key without a dot
    keys |= {f"settings.lang.{v}" for v in ("system", "en", "de")} | {f"settings.theme.{v}" for v in T.MODES}
    return keys


def test_every_used_key_exists_and_no_key_is_unused():
    literal = set()
    for f in glob.glob(os.path.join(GUI, "*.py")):
        literal |= set(re.findall(r'["\']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["\']', open(f).read())) - NOT_KEYS
    en = I.strings("en")
    missing = sorted(k for k in literal | dynamic_keys() if k not in en)
    assert not missing, missing
    unused = sorted(set(en) - literal - dynamic_keys())
    assert not unused, unused


def test_language_resolution():
    assert I.resolve("de") == "de" and I.resolve("en") == "en"
    assert I.resolve("system", QLocale(QLocale.Language.German, QLocale.Country.Austria)) == "de"
    assert I.resolve("xx", QLocale(QLocale.Language.French)) == "en"
    assert I.system_language() in I.LANGS
    i = I.I18n("fr")
    seen = []
    i.changed.connect(lambda: seen.append(1))
    i.set_lang("en"); i.set_lang("xx"); i.set_lang("de")
    assert i.lang == "de" and seen == [1]


def css_tokens(block):
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{6})", block))


def test_colours_are_the_webui_tokens_and_meet_wcag():
    css = open(os.path.join(ROOT, "bridge", "btbridge", "webui", "static", "app.css")).read()
    light = css_tokens(css.split("@media")[0])
    dark = css_tokens(css.split(':root[data-theme="dark"]')[1].split("}")[0])
    assert light == T.LIGHT and dark == T.DARK
    for t in (T.LIGHT, T.DARK):
        for fg in ("ink", "muted", "ok", "warn", "danger", "accent"):
            for bg in ("bg", "surface", "raised"):
                assert T.contrast(t[fg], t[bg]) >= 4.5, (fg, bg, T.contrast(t[fg], t[bg]))
        assert T.contrast(t["accent-ink"], t["accent"]) >= 4.5 and T.contrast(t["focus"], t["bg"]) >= 3.0
    assert open(LOGO).read() == open(os.path.join(ROOT, "bridge", "btbridge", "webui", "static", "logo.svg")).read()


def test_theme_modes(qapp, monkeypatch):
    assert T.tokens_for("light") is T.LIGHT and T.tokens_for("dark") is T.DARK
    assert isinstance(T.system_is_dark(), bool)
    monkeypatch.setattr(T, "system_is_dark", lambda app=None: True)
    assert T.tokens_for("system") is T.DARK and T.apply(qapp, "system") is T.DARK
    assert qapp.palette().window().color().name() == T.DARK["bg"]
    monkeypatch.setattr(T, "system_is_dark", lambda app=None: False)
    assert T.apply(qapp, "system") is T.LIGHT and "QPushButton[primary=\"true\"]" in qapp.styleSheet()


FOCUSABLE = (QAbstractButton, QLineEdit, QComboBox, QListWidget, QTextBrowser)


def unnamed(root: QWidget):
    buddies = {lab.buddy() for lab in root.findChildren(QLabel) if lab.buddy() is not None}
    out = []
    for w in root.findChildren(QWidget):
        if isinstance(w, FOCUSABLE) and w.isVisibleTo(root) and w.focusPolicy() & Qt.FocusPolicy.TabFocus:
            if not w.accessibleName().strip() and w not in buddies and not (isinstance(w, QAbstractButton) and w.text()):
                out.append(type(w).__name__)
    for bar in root.findChildren(QProgressBar):
        if not bar.accessibleName():
            out.append("QProgressBar")
    return out


def test_every_control_on_every_page_has_an_accessible_name(qtbot, win, env):
    assert unnamed(win) == []
    pick_board(qtbot, win)
    win.pages[1].custom.setChecked(True)
    assert unnamed(win) == []
    win.pages[1].official.setChecked(True)
    verified_official(qtbot, win)
    assert unnamed(win) == []
    choose_card(qtbot, win)
    assert unnamed(win) == []
    provision(qtbot, win)
    assert unnamed(win) == [] and win.pages[4].typed.accessibleName().startswith("To confirm")
    confirm_and_write(qtbot, win)
    qtbot.waitUntil(lambda: win.index == 6, timeout=TIMEOUT)
    assert unnamed(win) == []


def test_keyboard_only_operation_and_focus_order(qtbot, win):
    rb = win.pages[0].radios["rpi3"]
    rb.setFocus()
    qtbot.keyClick(rb, Qt.Key.Key_Space)
    assert rb.isChecked() and win.next_btn.isEnabled()
    chain, w = [], win.focusWidget()
    for _ in range(5000):                                                  # one full Tab cycle: focusable, visible, enabled widgets
        w = w.nextInFocusChain()
        if w is rb:
            break
        if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisible() and w.isEnabled():
            chain.append(w)
    assert chain[0] is win.next_btn                                        # from the chosen board, Tab goes straight to Next
    assert win.settings_btn in chain and win.about_btn in chain and win.back_btn not in chain     # Back is disabled on step 1
    assert chain.index(win.settings_btn) < chain.index(win.about_btn)
    win.next_btn.setFocus()
    qtbot.keyClick(win.next_btn, Qt.Key.Key_Space)
    assert win.index == 1


def test_switching_the_language_live_retranslates_everything(qtbot, win, env):
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    win.i18n.set_lang("de")
    assert page.heading.text() == "System-Image wählen" and win.next_btn.text() == "Weiter" and win.back_btn.text() == "Zurück"
    assert "Board" in win.step_labels[0].text() and "Image(s)" in page.result.text() and page.verify.text() == "Herunterladen und prüfen"
    assert win.sim.text().startswith("SIMULATION") and win.eyebrow.text() == "FLASH-WERKZEUG"
    win.i18n.set_lang("en")
    assert page.heading.text() == "Choose the system image"


def test_closing_during_a_download_cancels_it(qtbot, win, env):
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    page.list.setCurrentRow(0)
    import threading
    gate = threading.Event()
    orig = env.services.fetch_manifest
    env.services.fetch_manifest = lambda e: (gate.wait(10), orig(e))[1]
    page.run_verify()
    assert win.any_busy()
    win.close()
    assert not win.isVisible() and page.task.cancel.is_set()
    gate.set()
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)


# ------------------------------------------------------------------ entry point and services
def test_app_self_test_root_refusal_and_event_loop(qtbot, env, monkeypatch, capsys):
    assert APP.main(["--self-test", "--lang", "de", "--theme", "dark"], services=env.services) == 0
    timer = QApplication.instance()._hififlash_gc
    assert timer.isActive() and timer.interval() == 2000 and not __import__("gc").isenabled()
    assert "self-test ok: 7 pages, language de" in capsys.readouterr().out
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert APP.main([], services=env.services) == 2 and "do not run the GUI as root" in capsys.readouterr().err
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    app = QApplication.instance()
    loops = []
    monkeypatch.setattr(QApplication, "exec", lambda *a: loops.append(1) or 0)   # no real event loop inside pytest-qt
    before = {id(w) for w in app.topLevelWidgets()}
    assert APP.main(["--theme", "system"], services=env.services) == 0 and loops == [1]
    shown = [w for w in app.topLevelWidgets() if id(w) not in before and w.isVisible()]
    assert len(shown) == 1 and shown[0].windowTitle() == "Hi-Fi Bridge – Flash-Werkzeug"          # --lang de of the call above
    monkeypatch.setattr(T, "system_is_dark", lambda app=None: True)
    app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)           # "follow the system" re-applies the theme
    assert app.palette().window().color().name() == T.DARK["bg"]
    shown[0].close()
    called = []
    monkeypatch.setattr(APP, "main", lambda: called.append(1) or 0)
    with pytest.raises(SystemExit):
        runpy.run_module("hififlash.gui", run_name="__main__")
    assert called == [1]


def test_default_services_and_settings(tmp_path):
    s = SV.default_services(simulate=str(tmp_path), config_dir=str(tmp_path / "c"), cache_dir=str(tmp_path / "k"))
    assert s.simulated and s.list_disks() == [] and s.settings["source"] == C.DEFAULT_SOURCE and s.keys.keys() == []
    real = SV.default_services(platform="linux")                            # wired, but nothing is run
    assert not real.simulated and callable(real.channel_factory) and real.cache_dir.endswith(os.path.join("hifi-bridge-flash", "images"))
    assert SV.user_dirs()[0].endswith("hifi-bridge-flash")
    f = tmp_path / "s.json"
    f.write_text("[1]")
    assert SV.Settings(str(f))["lang"] == "system"
    f.write_text('{"lang": "de", "theme": 5, "unknown": "x"}')
    st = SV.Settings(str(f))
    assert st["lang"] == "de" and st["theme"] == "system"
    st.set("theme", "dark")
    assert json.loads(f.read_text())["theme"] == "dark"
    assert SV.Settings(str(tmp_path / "missing" / "x.json"))["source"] == C.DEFAULT_SOURCE


def test_native_dialog_wrappers(qapp, monkeypatch):
    from PySide6 import QtPrintSupport
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    d = SV.Dialogs()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: ("/x/y.img", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    assert d.open_file("t", "*") == "/x/y.img" and d.save_file("t", "n", "*") is None
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    assert d.confirm("t", "x")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    d.open_url("https://hifi-bridge.local/")
    assert opened == ["https://hifi-bridge.local/"]

    class FakePrintDialog:
        answer = 1

        def __init__(self, printer, parent):
            pass

        def exec(self):
            return FakePrintDialog.answer
    monkeypatch.setattr(QtPrintSupport, "QPrintDialog", FakePrintDialog)
    monkeypatch.setattr(QtPrintSupport, "QPrinter", lambda: "printer")
    printed = []
    doc = type("Doc", (), {"print_": lambda self, p: printed.append(p)})()
    assert d.print_document(doc) and printed == ["printer"]
    FakePrintDialog.answer = 0
    assert not d.print_document(doc)


def test_error_text_covers_every_error_family():
    from hififlash.gui.pages import error_text
    tr = I.I18n("en").tr
    assert "not a valid http(s)" in error_text(tr, C.SourceError("bad_url"))
    assert "(HTTP 500)" in error_text(tr, C.SourceError("http_error", "HTTP 500"))
    assert "another board" in error_text(tr, IM.ImageError("wrong_board", "hifi-bridge-rpi4"))
    assert "continues next time" in error_text(tr, C.DownloadCancelled("x"))
    from hififlash.helper.client import HelperFailed
    assert "write-protected" in error_text(tr, HelperFailed("disk_refused", "m", ["read_only"]))
    assert D.Disk("/dev/sdb", "sdb", 1).label().startswith("sdb")


def test_owner_sheet_variants():
    from hififlash import provision as FP
    from hififlash.gui import ownersheet as OS
    tr = I.I18n("de").tr
    doc, _ = FP.new_document("DE", hotspot=False, wifi=("Zuhause", "wifi-secret-123"))
    text = OS.to_text(doc, tr)
    assert "Einrichtungs-WLAN" not in text and "Zuhause" in text and "wifi-secret-123" not in text and "10.42.0.1" not in text
    assert "Hi-Fi Bridge" in OS.rows(doc, tr)[0][1] and OS.address(doc) == "hifi-bridge.local"


def test_remaining_page_branches(qtbot, win, env, tmp_path):
    win.back()                                                                   # nothing before step 1
    assert win.index == 0
    page0 = win.pages[0]
    page0.radios["rpi3"].setChecked(True)
    page0._choose(win.ctx.state.board)                                            # choosing the same board again changes nothing
    assert win.ctx.state.board.id == "rpi3"
    pre = F.image(tmp_path, "hifi-bridge-rpi3", "0.6.0-rc1")                      # an unsigned pre-release is listed and labelled
    env.server.publish("v0.6.0-rc1", [pre], prerelease=True)
    win.next()
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 2, timeout=TIMEOUT)
    labels = [page.list.item(i).text() for i in range(2)]
    assert any("pre-release" in t and "unsigned" in t for t in labels)
    page.list.setCurrentRow([i for i, t in enumerate(labels) if "0.5.0" in t][0])
    page.run_verify()
    qtbot.waitUntil(lambda: win.ctx.state.image is not None, timeout=TIMEOUT)
    win.next()
    env.set_card_meta(mounts=[])
    disks = win.pages[2]
    qtbot.waitUntil(lambda: disks.list.count() == 5 and not disks.busy(), timeout=TIMEOUT)
    choose_card(qtbot, win)
    provision(qtbot, win)
    assert "will be unmounted" not in win.pages[4].warning.text()                 # nothing mounted: no unmount note
    win.pages[5].request_cancel()                                                 # no write running: nothing to cancel
    assert win.pages[5].task is None


def test_app_without_options_keeps_the_saved_theme(qtbot, env, capsys):
    env.services.settings.values["theme"] = "dark"
    assert APP.main(["--self-test"], services=env.services) == 0
    assert QApplication.instance().palette().window().color().name() == T.DARK["bg"]
