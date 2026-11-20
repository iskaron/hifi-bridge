"""GUI (pytest-qt, QT_QPA_PLATFORM=offscreen): the whole wizard on SIMULATED cards through the real helper protocol - board -> official
image (download + signature + hash) -> card (refusals) -> device setup -> typed confirmation -> write/verify -> owner sheet + P7 trust.
Then the custom-image path, every failure/cancel path, the card swap between choosing and writing, and closing while writing."""
import os
import ssl
import stat

import pytest
from PySide6.QtCore import Qt

from btbridge import provisioning as P
from btbridge import trustproof
from hififlash import writer as W
from hififlash.helper import client as CL

TIMEOUT = 20000


def pick_board(qtbot, win, board="rpi3"):
    win.pages[0].radios[board].setChecked(True)
    qtbot.mouseClick(win.next_btn, Qt.MouseButton.LeftButton)
    assert win.index == 1


def verified_official(qtbot, win, row=0):
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() > 0, timeout=TIMEOUT)
    page.list.setCurrentRow(row)
    qtbot.mouseClick(page.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.ctx.state.image is not None, timeout=TIMEOUT)
    assert win.next_btn.isEnabled()
    qtbot.mouseClick(win.next_btn, Qt.MouseButton.LeftButton)
    assert win.index == 2


def choose_card(qtbot, win, name="sdcard"):
    page = win.pages[2]
    qtbot.waitUntil(lambda: page.list.count() == 5 and not page.busy(), timeout=TIMEOUT)
    item = next(page.list.item(i) for i in range(page.list.count()) if page.list.item(i).data(Qt.ItemDataRole.UserRole).name == name)
    page.list.setCurrentItem(item)
    assert win.next_btn.isEnabled()
    qtbot.mouseClick(win.next_btn, Qt.MouseButton.LeftButton)
    assert win.index == 3


def provision(qtbot, win, country="DE", wifi=("Home", "wifi-secret-123"), host="wohnzimmer"):
    page = win.pages[3]
    page.country.setCurrentIndex(page.country.findData(country))
    page.name.setText("Wohnzimmer")
    page.host.setText(host)
    if wifi:
        page.wifi.setChecked(True)
        page.ssid.setText(wifi[0])
        page.psk.setText(wifi[1])
    qtbot.mouseClick(win.next_btn, Qt.MouseButton.LeftButton)
    assert win.index == 4


def confirm_and_write(qtbot, win, typed="sdcard"):
    page = win.pages[4]
    assert not win.next_btn.isEnabled()
    qtbot.keyClicks(page.typed, typed)
    assert win.next_btn.isEnabled()
    qtbot.mouseClick(win.next_btn, Qt.MouseButton.LeftButton)
    assert win.index == 5


def test_happy_path_official_image_to_trusted_device(qtbot, win, env):
    assert not win.next_btn.isEnabled() and not win.back_btn.isEnabled()
    pick_board(qtbot, win)
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)          # only the rpi3 image of the release is listed
    assert "hifi-bridge-hifi-bridge-rpi3-0.5.0.img.xz" in page.list.item(0).text()
    verified_official(qtbot, win)
    assert "official publisher" in page.result.text() and page.result.property("state") == "ok"
    disk_page = win.pages[2]
    qtbot.waitUntil(lambda: disk_page.list.count() == 5, timeout=TIMEOUT)
    selectable = [disk_page.list.item(i).data(Qt.ItemDataRole.UserRole).name for i in range(5)
                  if disk_page.list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable]
    assert selectable == ["sdcard"]                                             # system, small, large, read-only: refused
    texts = "\n".join(disk_page.list.item(i).text() for i in range(5))
    for reason in ("system disk", "smaller than 2 GB", "unusually large", "write-protected", "not removable"):
        assert reason in texts
    choose_card(qtbot, win)
    assert "will be unmounted" in disk_page.details.text()
    provision(qtbot, win)
    confirm = win.pages[4]
    assert "sdcard" in confirm.prompt.text() and "Wi-Fi “Home”" in confirm.summary.text() and "official" in confirm.summary.text()
    confirm_and_write(qtbot, win)
    qtbot.waitUntil(lambda: win.index == 6, timeout=TIMEOUT)
    # the card holds the image and the provisioning document in the slot
    s = win.ctx.state
    m = env.rpi3["manifest"]
    with open(env.sd, "rb") as fh:
        raw = fh.read(m["size"])
    off = m["slot"]["offset"]
    assert raw[off:off + P.SLOT_SIZE] == P.render(s.doc) and raw[:off] == env.rpi3["data"][:off]
    assert s.doc["wifi"] == {"ssid": "Home", "psk": "wifi-secret-123"} and s.doc["local_hostname"] == "wohnzimmer"
    assert P.validate(s.doc) == []
    # owner sheet: what the owner needs, never the trust key or the home Wi-Fi password
    done = win.pages[6]
    sheet = done.sheet.toPlainText()
    assert s.doc["setup_code"] in sheet and s.doc["hotspot"]["psk"] in sheet and "wohnzimmer.local" in sheet
    assert s.doc["trust_key"] not in sheet and "wifi-secret-123" not in sheet
    txt, pdf = os.path.join(env.tmp, "sheet.txt"), os.path.join(env.tmp, "sheet.pdf")
    env.dialogs.save_paths += [txt, pdf]
    qtbot.mouseClick(done.save_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(done.save_btn, Qt.MouseButton.LeftButton)
    assert s.doc["setup_code"] in open(txt).read() and open(pdf, "rb").read(5) == b"%PDF-"
    assert stat.S_IMODE(os.stat(txt).st_mode) == 0o600 and stat.S_IMODE(os.stat(pdf).st_mode) == 0o600
    qtbot.mouseClick(done.save_btn, Qt.MouseButton.LeftButton)                   # dialog cancelled: nothing happens
    qtbot.mouseClick(done.print_btn, Qt.MouseButton.LeftButton)
    assert s.doc["setup_code"] in env.dialogs.printed[0]
    # P7: the device proves the CA with the trust key from the card; HTTPS is then checked against only that CA
    assert done.address.text() == "wohnzimmer.local" and not done.save_ca.isEnabled()
    der = b"0\x82fake-ca"
    seen = {}

    def fetch(url, key):
        seen["fetch"] = (url, key)
        return der

    env.services.fetch_verified_ca = fetch
    env.services.check_https = lambda url, ca: seen.setdefault("https", (url, ca))
    key = s.trust_key
    qtbot.mouseClick(done.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: s.ca_der is not None, timeout=TIMEOUT)
    assert seen == {"fetch": ("http://wohnzimmer.local", key), "https": ("https://wohnzimmer.local", der)}
    assert trustproof.fingerprint(der) in done.status.text() and s.trust_key is None and not done.verify.isEnabled()
    pem = os.path.join(env.tmp, "ca.pem")
    env.dialogs.save_paths.append(pem)
    qtbot.mouseClick(done.save_ca, Qt.MouseButton.LeftButton)
    assert ssl.PEM_cert_to_DER_cert(open(pem).read()) == der
    qtbot.mouseClick(done.save_ca, Qt.MouseButton.LeftButton)                    # cancelled
    qtbot.mouseClick(done.open_web, Qt.MouseButton.LeftButton)
    assert env.dialogs.urls == ["https://wohnzimmer.local/"]
    qtbot.mouseClick(done.again, Qt.MouseButton.LeftButton)
    assert win.index == 0 and win.ctx.state.doc is None


def test_trust_failures_and_a_bad_address(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win, wifi=None, host="")
    confirm_and_write(qtbot, win)
    qtbot.waitUntil(lambda: win.index == 6, timeout=TIMEOUT)
    done = win.pages[6]
    assert "hifi-bridge.local" == done.address.text() and "on a device in the same network" in done.sheet.toPlainText()
    assert "Wi-Fi" not in done.sheet.toPlainText().split("First start")[0]
    done.address.setText("evil host/; rm")
    qtbot.mouseClick(done.verify, Qt.MouseButton.LeftButton)
    assert "Enter a host name" in done.status.text()

    def attacker(url, key):
        raise trustproof.TrustProofError("the device could not prove the trust key - do NOT trust this certificate")
    env.services.fetch_verified_ca = attacker
    done.address.setText("hifi-bridge.local:8080")
    qtbot.mouseClick(done.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: done.status.property("state") == "error", timeout=TIMEOUT)
    assert "do NOT trust" in done.status.text() and win.ctx.state.trust_key is not None and not done.open_web.isEnabled()
    env.services.fetch_verified_ca = lambda url, key: (_ for _ in ()).throw(OSError())
    qtbot.mouseClick(done.verify, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "OSError" in done.status.text(), timeout=TIMEOUT)


def test_card_swapped_after_choosing_is_never_written(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win)
    env.set_card_meta(serial="ANOTHER-CARD")                                     # the owner swapped the card
    with open(env.sd, "r+b") as fh:
        fh.write(b"\x55" * 4096)
    confirm_and_write(qtbot, win)
    page = win.pages[5]
    qtbot.waitUntil(lambda: page.fail.isVisible(), timeout=TIMEOUT)
    assert "Another card was inserted" in page.message.text() and win.index == 5
    with open(env.sd, "rb") as fh:
        assert fh.read(4096) == b"\x55" * 4096
    os.remove(env.sd)                                                             # and when the card is gone entirely
    qtbot.mouseClick(page.retry, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "no longer connected" in page.message.text(), timeout=TIMEOUT)
    assert win.back_btn.isEnabled()
    qtbot.mouseClick(win.back_btn, Qt.MouseButton.LeftButton)
    assert win.index == 4


def test_write_failure_invalidates_and_retry_succeeds(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win)
    env.set_card_meta(fail_after=3 * 1024 * 1024)
    confirm_and_write(qtbot, win)
    page = win.pages[5]
    qtbot.waitUntil(lambda: page.fail.isVisible(), timeout=TIMEOUT)
    assert "Writing failed" in page.message.text() and "made unbootable" in page.message.text()
    with open(env.sd, "rb") as fh:
        assert fh.read(W.INVALIDATE_BYTES) == bytes(W.INVALIDATE_BYTES)
    env.set_card_meta(fail_after=None)
    qtbot.mouseClick(page.retry, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.index == 6, timeout=TIMEOUT)


def test_cancel_while_writing_and_elevation_refused(qtbot, win, env):
    import threading
    gate = threading.Event()
    orig = env.adapter.prepare

    def slow_prepare(disk):
        gate.wait(10)
        return orig(disk)
    env.adapter.prepare = slow_prepare
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win)
    confirm_and_write(qtbot, win)
    page = win.pages[5]
    assert page.busy() and not win.back_btn.isEnabled() and not win.next_btn.isVisible()
    qtbot.mouseClick(page.cancel, Qt.MouseButton.LeftButton)
    assert page.stage.text() == "Cancelling…"
    page._progress("write", 1, 2)                                                 # late progress keeps "Cancelling…"
    assert page.stage.text() == "Cancelling…"
    gate.set()
    qtbot.waitUntil(lambda: page.fail.isVisible(), timeout=TIMEOUT)
    assert "Cancelled" in page.message.text()
    env.services.channel_factory = lambda: CL.Channel(__import__("io").BytesIO(b""), __import__("io").BytesIO(), lambda: None)
    qtbot.mouseClick(page.retry, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Administrator permission was not granted" in page.message.text(), timeout=TIMEOUT)
    env.services.channel_factory = lambda: CL.Channel(__import__("io").BytesIO(
        b'{"event":"ready","v":1}\n{"event":"error","code":"strange","message":"m","reasons":["read_only"]}\n'),
        __import__("io").BytesIO(), lambda: None)
    qtbot.mouseClick(page.retry, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "internal error" in page.message.text() and "write-protected" in page.message.text(), timeout=TIMEOUT)


def test_closing_while_writing_asks_and_cancels(qtbot, win, env):
    import threading
    gate = threading.Event()
    orig = env.adapter.prepare
    env.adapter.prepare = lambda disk: (gate.wait(10), orig(disk))[1]
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win)
    confirm_and_write(qtbot, win)
    env.dialogs.confirms += [False, True]
    win.close()
    assert win.isVisible() and win.pages[5].busy()                                # declined: keeps writing
    win.close()
    assert win.isVisible() and win._close_after_write
    gate.set()
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=TIMEOUT)


def test_successful_write_after_a_close_request_closes_the_window(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    choose_card(qtbot, win)
    provision(qtbot, win)
    confirm_and_write(qtbot, win)
    win._close_after_write = True
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=TIMEOUT)


def test_back_navigation_and_board_change_drops_the_image(qtbot, win, env):
    pick_board(qtbot, win)
    verified_official(qtbot, win)
    assert not win.back_btn.isEnabled() or not win.pages[2].busy()               # no Back while the card list is loading
    qtbot.waitUntil(lambda: win.back_btn.isEnabled(), timeout=TIMEOUT)
    qtbot.mouseClick(win.back_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(win.back_btn, Qt.MouseButton.LeftButton)
    assert win.index == 0
    win.pages[0].radios["rpi3"].setChecked(True)                                 # same board: the image stays
    assert win.ctx.state.image is not None
    win.pages[0].radios["rpi4"].setChecked(True)
    assert win.ctx.state.image is None
    win.next()
    page = win.pages[1]
    qtbot.waitUntil(lambda: page.list.count() == 1, timeout=TIMEOUT)
    assert "rpi4" in page.list.item(0).text() and not win.next_btn.isEnabled()
    win.next()                                                                    # incomplete: nothing happens
    assert win.index == 1
    win.back()
    win.pages[0].radios["rpizero2w"].setChecked(True)
    win.next()
    qtbot.waitUntil(lambda: "no image for this board" in page.result.text(), timeout=TIMEOUT)
