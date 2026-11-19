"""GUI (offscreen): image sources and custom images with explicitly added publisher keys, download cancel, source failures, the card list
(errors, large-disk opt-in, a card that disappears), every provisioning validator (shared with the device), settings and about."""
import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

import flashfixtures as F
from btbridge import provisioning as P
from hififlash.gui import dialogs as DLG
from hififlash.gui.dialogs import AboutDialog, SettingsDialog

from test_wizard import TIMEOUT, choose_card, pick_board, verified_official


def to_custom(qtbot, win):
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    page.custom.setChecked(True)
    assert page.stack.currentIndex() == 1 and page.verify.text() == "Verify" and not page.verify.isEnabled()
    return page


def test_custom_image_needs_an_explicitly_trusted_publisher(qtbot, win, env, tmp_path):
    page = to_custom(qtbot, win)
    mine, pub = F.keypair()
    im = F.image(tmp_path, "hifi-bridge-rpi3", "9.9.9-own", key=mine)
    man = tmp_path / "own.manifest.json"
    man.write_bytes(im["raw"])
    (tmp_path / "own.manifest.json.sig").write_bytes(im["sig"])
    env.dialogs.open_paths += [im["path"], str(man)]
    browse = {b.accessibleName(): b for b in page.findChildren(type(page.verify))}
    qtbot.mouseClick(browse["Choose the image file"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(browse["Choose the manifest file"], Qt.MouseButton.LeftButton)
    assert page.paths["image"].text() == im["path"] and page.paths["signature"].text() == str(man) + ".sig"   # signature found
    for b in page.findChildren(type(page.verify)):
        if b.accessibleName() == "Choose the signature file":
            qtbot.mouseClick(b, Qt.MouseButton.LeftButton)                                      # dialog cancelled: unchanged
    assert page.verify.isEnabled()
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.result.property("state") == "error", timeout=TIMEOUT)
    assert "not from a trusted publisher" in page.result.text() and not win.next_btn.isEnabled()
    # the owner adds the key explicitly in Settings (fingerprint shown and confirmed)
    keyfile = tmp_path / "workshop.pem"
    keyfile.write_bytes(pub)
    dlg = SettingsDialog(win, win.ctx)
    dlg.key_name.setText("Workshop")
    env.dialogs.open_paths.append(str(keyfile))
    env.dialogs.confirms.append(False)
    dlg.add_key()                                                                # declined: not added
    assert [k.kind for k in env.keys.keys()] == ["official"]
    env.dialogs.open_paths.append(str(keyfile))
    dlg.add_key()
    assert "fingerprint" in env.dialogs.asked[-1].lower() and "“Workshop” is now" in dlg.message.text()
    assert any("CUSTOM" in dlg.keys.item(i).text() for i in range(dlg.keys.count()))
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.ctx.state.image is not None, timeout=TIMEOUT)
    assert page.result.property("state") == "warn" and "CUSTOM publisher" in page.result.text() and win.next_btn.isEnabled()
    # without a signature file, or with an unreadable manifest
    os.remove(str(man) + ".sig")                                                  # otherwise it is picked up again automatically
    page.paths["signature"].setText("")
    assert page.paths["signature"].text() == ""
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "no signature" in page.result.text(), timeout=TIMEOUT)
    page.paths["manifest"].setText(str(tmp_path / "missing.json"))
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "cannot be read" in page.result.text(), timeout=TIMEOUT)


def test_settings_keys_errors_and_removal(qtbot, win, env, tmp_path):
    dlg = SettingsDialog(win, win.ctx)
    assert "official" in dlg.keys.item(0).text()
    dlg.keys.setCurrentRow(0)
    dlg.remove_key()
    assert "Only custom keys" in dlg.message.text()
    junk = tmp_path / "junk.pem"
    junk.write_text("not a key")
    env.dialogs.open_paths.append(str(junk))
    dlg.add_key()
    assert "not added" in dlg.message.text()
    dlg.add_key()                                                                # file dialog cancelled
    _, pub = F.keypair()
    good = tmp_path / "Bench key.pem"
    good.write_bytes(pub)
    env.dialogs.open_paths.append(str(good))
    dlg.add_key()                                                                # name taken from the file name
    assert any(k.name == "Bench key" for k in env.keys.keys())
    dlg.keys.setCurrentRow([i for i in range(dlg.keys.count()) if "Bench key" in dlg.keys.item(i).text()][0])
    dlg.remove_key()
    assert [k.kind for k in env.keys.keys()] == ["official"]
    os.remove(os.path.join(env.tmp, "official", "hifi-bridge-test.pem"))
    dlg.fill_keys()
    assert "No trusted publisher yet" in dlg.keys.item(0).text()
    dlg.keys.setCurrentRow(0)
    dlg.remove_key()
    assert "Only custom keys" in dlg.message.text()


def test_settings_change_language_theme_and_source(qtbot, win, env):
    dlg = SettingsDialog(win, win.ctx)
    dlg.source.setText("file:///etc/passwd")
    dlg.save()
    assert "http(s)" in dlg.message.text() and dlg.result() == 0
    dlg.source.setText("")
    dlg.lang.setCurrentIndex(dlg.lang.findData("de"))
    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    dlg.save()
    assert env.services.settings["source"].startswith("https://api.github.com/repos/iskaron/hifi-bridge")
    win.apply_settings()
    assert win.next_btn.text() == "Weiter" and win.windowTitle() == "Hi-Fi Bridge – Flash-Werkzeug"
    from PySide6.QtWidgets import QApplication
    assert QApplication.instance().palette().window().color().name() == "#111113"
    assert '"lang": "de"' in open(os.path.join(env.tmp, "cfg", "settings.json")).read()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget() and QApplication.activeModalWidget().reject())
    win.open_settings()                                                          # cancelled dialog: nothing changes
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget() and QApplication.activeModalWidget().accept())
    win.open_settings()


def test_about_shows_the_lgpl_notice_and_sources(qtbot, win, monkeypatch):
    html_text = DLG.about_html(win.i18n.tr)
    for needle in ("Lesser General Public License v3.0", "replace", "download.qt.io", "PySide6", "Affero", "gnu.org/licenses/lgpl-3.0"):
        assert needle in html_text
    dlg = AboutDialog(win, win.ctx)
    seen = []
    monkeypatch.setattr(QMessageBox, "aboutQt", lambda parent: seen.append(parent))
    qtbot.mouseClick(dlg.about_qt, Qt.MouseButton.LeftButton)
    assert seen == [dlg]
    assert DLG.notices_path().endswith(os.path.join("docs", "THIRD_PARTY.md")) and os.path.isfile(DLG.notices_path())
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert DLG.notices_path().endswith(os.path.join("licenses", "THIRD_PARTY_NOTICES.txt"))
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget() and QApplication.activeModalWidget().reject())
    win.open_about()


@pytest.mark.parametrize("status,text", [(404, "not found or is private"), (403, "refused access"), (500, "answered with an error")])
def test_release_source_failures_are_explained(qtbot, win, env, status, text):
    env.server.status = status
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: text in page.result.text(), timeout=TIMEOUT)
    assert page.result.property("state") == "error" and page.list.count() == 0 and not win.next_btn.isEnabled()
    env.server.status = None
    qtbot.mouseClick(page.reload, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)


def test_untrusted_official_release_is_refused_before_downloading(qtbot, win, env):
    os.remove(os.path.join(env.tmp, "official", "hifi-bridge-test.pem"))         # e.g. the official key is not published yet
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    page.list.setCurrentRow(0)
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "not from a trusted publisher" in page.result.text(), timeout=TIMEOUT)
    assert not any(r["path"].endswith(".img.xz") for r in env.server.requests)    # the image itself was never downloaded


def test_download_cancel_and_download_error(qtbot, win, env):
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    page.list.setCurrentRow(0)
    orig = env.services.fetch_manifest

    def cancel_then_fetch(entry):
        page.task.cancel.set()
        return orig(entry)
    env.services.fetch_manifest = cancel_then_fetch
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    assert page.cancel.isEnabled() and not page.verify.isEnabled()
    qtbot.waitUntil(lambda: "continues next time" in page.result.text(), timeout=TIMEOUT)
    def fetch_then_drop(entry):
        raw = orig(entry)
        env.server.drop_after = 1000                                              # the IMAGE download breaks after 1000 bytes
        return raw
    env.services.fetch_manifest = fetch_then_drop
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Download failed" in page.result.text(), timeout=TIMEOUT)
    env.services.fetch_manifest = orig
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)                     # resumes and verifies
    qtbot.waitUntil(lambda: win.ctx.state.image is not None, timeout=TIMEOUT)
    page._progress("download", 5, 0)                                              # unknown total does not divide by zero
    assert page.bar.value() == 0


def test_unexpected_errors_are_shown_with_their_type(qtbot, win, env):
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    page.list.setCurrentRow(0)
    env.services.fetch_manifest = lambda entry: (_ for _ in ()).throw(RuntimeError("boom"))
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Unexpected error: RuntimeError: boom" in page.result.text(), timeout=TIMEOUT)


def test_card_list_errors_large_disks_and_vanishing_cards(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    page = win.pages[2]
    qtbot.waitUntil(lambda: page.list.count() == 5 and not page.busy(), timeout=TIMEOUT)
    assert page.details.text() == "Choose a card."
    page.large.setChecked(True)
    names = [page.list.item(i).data(Qt.ItemDataRole.UserRole).name for i in range(5)
             if page.list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable]
    assert names == ["sdc", "sdcard"]
    choose_card(qtbot, win, "sdc")
    win.back()
    assert win.index == 2
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    page.large.setChecked(False)                                                  # the large disk is no longer allowed: deselected
    assert win.ctx.state.disk is None and not win.next_btn.isEnabled()
    choose_card(qtbot, win, "sdcard")
    win.back()
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    os.remove(env.sd)                                                              # the chosen card is pulled
    qtbot.mouseClick(page.reload, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.busy() and page.list.count() == 4, timeout=TIMEOUT)
    assert win.ctx.state.disk is None and "No suitable card" in page.details.text()
    env.services.list_disks = lambda: (_ for _ in ()).throw(OSError(13, "lsblk failed"))
    qtbot.mouseClick(page.reload, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.details.property("state") == "error", timeout=TIMEOUT)
    assert "lsblk failed" in page.details.text() and page.list.count() == 0
    page._select(None)
    assert not page.is_complete()


@pytest.mark.parametrize("field,value,msg", [
    ("name", "x" * 200, "Not allowed as a device name"), ("host", "-bad_host-", "letters, digits and hyphens"),
    ("ssid", "", "1 to 32 bytes"), ("ssid", "x" * 33, "1 to 32 bytes"), ("psk", "short", "8 to 63"), ("psk", "ü" * 10, "8 to 63"),
    ("hs_psk", "short", "12 to 63"), ("hs_psk", "x" * 64, "12 to 63")])
def test_provisioning_uses_the_device_validators(qtbot, win, env, field, value, msg):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    page = win.pages[3]
    assert not win.next_btn.isEnabled()                                           # the country is required (no guessing)
    page.country.setCurrentIndex(page.country.findData("AT"))
    page.wifi.setChecked(True)
    page.ssid.setText("Home")
    page.psk.setText("wifi-secret-123")
    getattr(page, field).setText(value)
    win.next()
    assert win.index == 3 and msg in page.errors[f"prov.{field}"].text() and page.errors[f"prov.{field}"].isVisibleTo(page)
    getattr(page, field).setText({"name": "Küche", "host": "kueche", "ssid": "Home", "psk": "a" * 64, "hs_psk": "my-own-hotspot-pw"}[field])
    win.next()
    assert win.index == 4 and P.validate(win.ctx.state.doc) == []
    assert not page.errors[f"prov.{field}"].isVisibleTo(page)


def test_provisioning_variants(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    page = win.pages[3]
    page.country.setCurrentIndex(page.country.findData("CH"))
    page.hotspot.setChecked(False)
    assert not page.hs_psk.isEnabled() and not page.ssid.isEnabled()
    page.show_psk.setChecked(True)
    assert page.psk.echoMode() == page.psk.EchoMode.Normal
    page.show_psk.setChecked(False)
    win.next()
    doc = win.ctx.state.doc
    assert "hotspot" not in doc and "wifi" not in doc and doc["country"] == "CH" and len(win.ctx.state.trust_key) == 32
    assert "Ethernet only" in win.pages[4].summary.text()
    first = doc["setup_code"]
    win.back()
    page.country.setCurrentIndex(0)
    assert not win.next_btn.isEnabled()
    page.country.setCurrentIndex(page.country.findData("CH"))
    page.hotspot.setChecked(True)
    page.hs_psk.setText("owner-chosen-pw")
    win.next()
    assert win.ctx.state.doc["hotspot"]["psk"] == "owner-chosen-pw" and win.ctx.state.doc["setup_code"] != first   # fresh secrets
    page._error("prov.country", "prov.err.country")
    assert "Choose the country" in page.errors["prov.country"].text()


def test_confirm_page_escapes_owner_input(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    page = win.pages[3]
    page.country.setCurrentIndex(page.country.findData("DE"))
    page.wifi.setChecked(True)
    page.ssid.setText("<b>x</b>")
    page.psk.setText("wifi-secret-123")
    win.next()
    assert "&lt;b&gt;x&lt;/b&gt;" in win.pages[4].summary.text()
    win.pages[4].typed.setText("sdcard ")                                         # surrounding spaces are tolerated
    assert win.pages[4].is_complete()
    win.pages[4].typed.setText("sdc")
    assert not win.pages[4].is_complete()
    win.ctx.state.disk = None
    win.pages[4].refresh()                                                         # incomplete state: nothing to show, no crash


def test_untrusted_device_strings_are_never_rendered_as_rich_text(qtbot, win, env):
    env.set_card_meta(model="<b>SUITABLE</b><img src='/etc/passwd'>")              # a USB device chooses its own model string
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    win.back()
    page = win.pages[2]
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    assert page.details.textFormat() == Qt.TextFormat.PlainText and "<b>SUITABLE</b>" in page.details.text()
    for lab in (win.pages[1].result, win.pages[4].warning, win.pages[4].prompt, win.pages[5].message, win.pages[6].status):
        assert lab.textFormat() == Qt.TextFormat.PlainText
