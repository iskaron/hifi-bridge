# PyInstaller spec of the flash tool (docs/FLASH_TOOL.md §7). ONE-FOLDER build on purpose (LGPL-3.0, docs/THIRD_PARTY.md): Qt and PySide6
# stay separate shared libraries next to the executables, so a user can replace them with their own builds. No one-file bundle.
#
# Two executables in the same folder:
#   hifi-bridge-flash   the GUI (unprivileged)
#   hififlash-helper    the privileged write helper - WITHOUT Qt (excluded), the smallest possible elevated code
# Build (Linux, in Docker):  scripts/run_flashtool.sh package
# Windows / macOS: run the same spec on the native platform (docs/FLASH_TOOL.md §7) - NOT executed in this repository yet.
import os
import sys

HERE = os.path.abspath(SPECPATH)                       # noqa: F821  (PyInstaller: the directory of this spec file)
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PATHS = [os.path.join(REPO, "flashtool"), os.path.join(REPO, "bridge")]
GUI_DIR = os.path.join(REPO, "flashtool", "hififlash", "gui")
DATAS = [(os.path.join(GUI_DIR, "i18n"), "hififlash/gui/i18n"), (os.path.join(GUI_DIR, "assets"), "hififlash/gui/assets"),
         (os.path.join(REPO, "flashtool", "hififlash", "keys"), "hififlash/keys")]
WIN = sys.platform == "win32"
ICO = os.path.join(HERE, "windows", "hifi-bridge-flash.ico")      # to be rendered from logo.svg on Windows (not in the repository)
MAC = sys.platform == "darwin"
sys.path.insert(0, os.path.join(REPO, "flashtool"))
from hififlash import __version__                       # noqa: E402
VERSION = __version__.split("-")[0]                     # CFBundleShortVersionString of the macOS .app: numbers only ("0.1.0-dev" -> "0.1.0")

gui = Analysis([os.path.join(HERE, "entry_gui.py")], pathex=PATHS, datas=DATAS,                  # noqa: F821
               hiddenimports=["PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtPrintSupport", "hififlash.platforms.linux",
                              "hififlash.platforms.darwin", "hififlash.platforms.windows", "hififlash.platforms.win32api",
                              "hififlash.platforms.simulated", "hififlash.platforms.posixio"],
               excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
                         "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtNetwork"])
helper = Analysis([os.path.join(HERE, "entry_helper.py")], pathex=PATHS, datas=[],                # noqa: F821
                  hiddenimports=["hififlash.platforms.linux", "hififlash.platforms.darwin", "hififlash.platforms.windows",
                                 "hififlash.platforms.win32api", "hififlash.platforms.simulated", "hififlash.platforms.posixio"],
                  excludes=["tkinter", "PySide6", "shiboken6", "cryptography"])

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="hifi-bridge-flash", console=False,   # noqa: F821
              icon=ICO if WIN and os.path.isfile(ICO) else None)
helper_exe = EXE(PYZ(helper.pure), helper.scripts, [], exclude_binaries=True, name="hififlash-helper", console=True,   # noqa: F821
                 uac_admin=WIN, manifest=os.path.join(HERE, "windows", "hififlash-helper.manifest") if WIN else None)
coll = COLLECT(gui_exe, gui.binaries, gui.datas, helper_exe, helper.binaries, helper.datas, name="hifi-bridge-flash")   # noqa: F821

if MAC:
    # signing, notarisation, entitlements: flashtool/packaging/macos/ and docs/MACOS_SIGNING.md
    app = BUNDLE(coll, name="Hi-Fi Bridge Flash.app", bundle_identifier="io.github.iskaron.hifibridge.flash", version=VERSION,   # noqa: F821
                 info_plist={"CFBundleDisplayName": "Hi-Fi Bridge Flash", "NSHighResolutionCapable": True,
                             "LSMinimumSystemVersion": "12.0"})
