"""Collect the licence texts of everything a flash-tool package bundles (docs/THIRD_PARTY.md "Flash tool"), plus the LGPL notice and the
source offer for Qt/PySide6. Run inside the build environment: python3 collect_licenses.py <dist>/licenses

Copies, for each bundled Python distribution, every licence/notice file from its dist-info; Python's own licence; the project's AGPL
licence; and writes THIRD_PARTY_NOTICES.txt (the About dialog points to it)."""
from __future__ import annotations

import os
import re
import shutil
import sys
import sysconfig
from importlib import metadata

BUNDLED = ("PySide6", "PySide6_Essentials", "shiboken6", "cryptography", "cffi", "pycparser")
LICENCE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$|(^|/)licenses?/", re.I)
COMMON_LICENSES = "/usr/share/common-licenses"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

NOTICE = """Hi-Fi Bridge flash tool - third-party notices
=============================================

This program is free software under the GNU Affero General Public License v3.0 (LICENSE-hifi-bridge.txt).
Source code: https://github.com/iskaron/hifi-bridge

Qt and Qt for Python (PySide6, shiboken6) - GNU Lesser General Public License v3.0
----------------------------------------------------------------------------------
This package uses Qt {qt} and PySide6 {pyside} unmodified, under the LGPL-3.0. They are DYNAMICALLY linked and shipped as separate
shared libraries in this folder (one-folder build, never a single-file bundle), so you can replace them with your own (modified)
builds of the same version: replace the Qt/PySide6 libraries in this folder (on macOS inside the .app's Contents/Frameworks and
Contents/Resources). The LGPL-3.0 and GPL-3.0 texts are in this folder.
Corresponding source code:
  Qt:      https://download.qt.io/official_releases/qt/
  PySide6: https://download.qt.io/official_releases/QtForPython/pyside6/  (and https://code.qt.io/cgit/pyside/pyside-setup.git/)
On request, the publisher of this package provides the corresponding source of these libraries for at least three years.

Other components
----------------
{others}
"""


def dist_files(name: str) -> list[tuple[str, str]]:
    try:
        d = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return []
    out = []
    for f in d.files or []:
        if LICENCE_FILE.search(str(f)) and ".dist-info" in str(f):
            out.append((str(f), str(d.locate_file(f))))
    return out


def main(argv=None) -> int:
    out = (argv or sys.argv[1:])[0]
    os.makedirs(out, exist_ok=True)
    others = []
    for name in BUNDLED:
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        files = dist_files(name)
        version = metadata.version(name)
        lic = metadata.metadata(name).get("License-Expression") or metadata.metadata(name).get("License") or "see files"
        others.append(f"{name} {version}: {lic.splitlines()[0][:120]}")
        for rel, src in files:
            if os.path.isfile(src):
                dst = os.path.join(out, name, os.path.basename(rel))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
    # The PySide6/shiboken6 wheels carry NO licence texts (only "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only" in their metadata), so
    # the package ships the texts itself - from the build system's common licences (Debian base-files).
    for src, dst in (("LGPL-3", "LGPL-3.0.txt"), ("GPL-3", "GPL-3.0.txt"), ("GPL-2", "GPL-2.0.txt")):
        path = os.path.join(COMMON_LICENSES, src)
        if not os.path.isfile(path):
            raise SystemExit(f"missing {path}: the Qt/PySide6 licence texts must be shipped")
        shutil.copyfile(path, os.path.join(out, dst))
    py_licence = os.path.join(sysconfig.get_paths()["stdlib"], "LICENSE.txt")
    if os.path.isfile(py_licence):
        shutil.copyfile(py_licence, os.path.join(out, "Python-LICENSE.txt"))
        others.append(f"Python {sys.version.split()[0]}: PSF-2.0 (Python-LICENSE.txt)")
    shutil.copyfile(os.path.join(REPO, "LICENSE"), os.path.join(out, "LICENSE-hifi-bridge.txt"))
    import PySide6
    from PySide6.QtCore import qVersion
    with open(os.path.join(out, "THIRD_PARTY_NOTICES.txt"), "w") as fh:
        fh.write(NOTICE.format(qt=qVersion(), pyside=PySide6.__version__, others="\n".join(others) +
                               "\nOpenSSL (bundled in python-cryptography): Apache-2.0"))
    print(f"licences collected in {out}: " + ", ".join(sorted(os.listdir(out))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
