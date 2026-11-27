# Third-party components (licence intent of this project: AGPL-3.0-only, DECISIONS #13)

## Runtime on the device (bridge package)
| Component | Use | Licence | Notes |
|---|---|---|---|
| Python 3 standard library | everything (HTTP server, MQTT client, D-Bus types, config, auth) | PSF | no other Python packages needed |
| jeepney 0.8 (optional) | D-Bus client for the BlueZ adapter (provisionally approved, DECISIONS #23) | MIT | only imported with `--source bluez`; Buildroot availability and size unchecked |
| `parec` (pulseaudio-utils) or `pw-cat` (PipeWire) | PCM capture subprocess | LGPL-2.1+ / MIT | called as external programs; target image decision in phase 2b |
| BlueZ, PipeWire, WirePlumber | OS services on the device | GPL-2.0+ / MIT / MIT | not bundled by this repository |
| Avahi (avahi-daemon, D-Bus API) | production mDNS (DECISIONS #49) | LGPL-2.1+ | controlled via jeepney; built-in responder as fallback |
| `flac` binary (optional) | FLAC stream encoding | GPL-2.0+ (tools) | only with `stream_format=flac` |
| `lame` binary (optional; LAME 3.100) | live MP3 320 kbit/s CBR stream encoding, run as a child process per stream connection (DECISIONS #147) | LGPL-2.0+ | only with `stream_format=mp3`; Debian `lame`, Buildroot `lame` (in the images); not linked into the bridge |
| `openssl` CLI | local CA and certificates for the WebUI | Apache-2.0 (OpenSSL 3) | needed for HTTPS in `local_ca`/`custom` mode (OPEN_QUESTIONS #29) |

## Device image (Buildroot; internal development builds only, nothing published)
The card image is a collection of separately licensed packages (Buildroot 2026.02.x). Per board, the complete list with versions, licences
and source URLs is the committed SBOM and licence report: `reports/evidence/os_sbom_<board>.cdx.json` (CycloneDX 1.6, from
`utils/generate-cyclonedx`) and `reports/evidence/os_licenses_<board>.md` (policy: `os/tools/license_report.py`). `make legal-info`
(sources + licence texts) is produced by every build but not committed (size). The non-free firmware (Raspberry Pi boot firmware,
Broadcom/Cypress/Synaptics Wi-Fi and Bluetooth firmware) is reviewed in **docs/IMAGE_LICENSING.md**: redistributable unmodified, only for
the hardware, with the licence texts, which every image carries in `licenses/` on the boot partition.

## Development, lab and test only (never shipped)
| Component | Use | Licence |
|---|---|---|
| Debian bookworm/trixie images | lab containers | various (DFSG) |
| Lyrion Music Server 9.1.2 (`lmscommunity/lyrionmusicserver:9.1.2@sha256 pin`) | real LMS in the lab | GPL-2.0 |
| Squeezelite 1.9.9 | software players in the lab | GPL-3.0 |
| Eclipse Mosquitto 2.0.20 | MQTT broker in the lab | EPL-2.0 / EDL-1.0 |
| numpy, pytest | analysis, test runner | BSD-3-Clause / MIT |
| FFmpeg 5.1 | real SBC/AAC encode/decode for kit self-tests and phone tracks | LGPL-2.1+/GPL-2.0+ (Debian build) |
| Playwright 1.49.1 + Chromium | real-browser UI tests | Apache-2.0 / BSD-3-Clause |
| axe-core (via axe-playwright-python 0.1.4) | automated WCAG audit | MPL-2.0 (axe-core), MIT (wrapper) |
| Alpine 3.20 | chown helper in a script | MIT and others |
| Home Assistant 2026.11.1 (`ghcr.io/home-assistant/home-assistant`) | real HA integration tests | Apache-2.0 |
| Avahi 0.8 (Debian) | mDNS interoperability peer | LGPL-2.1+ |
| ShellCheck 0.10.0, actionlint 1.7.7 | static checks | GPL-3.0 / MIT |
| systemd (Debian 13) | `systemd-analyze verify` of the unit file | LGPL-2.1+ |
| rsyslog 8 + rsyslog-gnutls (Debian 13) | TLS syslog peer for the remote-syslog test | GPL-3.0+/ASL-2.0 |
| python:3.14-slim, pytest 8.4.2, numpy 2.3.5, jeepney 0.9.0 | Python 3.14 compatibility run | PSF / MIT / BSD-3-Clause / MIT |
| coverage.py (Debian python3-coverage) | coverage measurement | Apache-2.0 |
| Buildroot 2026.02.x (`679b9ea`, fetched into `.lab/`, not redistributed) | configuration checks, `show-info` licence reports, internal development image builds (`scripts/os_build.sh`) | GPL-2.0+ |
| Debian 13 OS tools in `btbridge-os`: RAUC 1.13, genimage 18, mtools 4.0.48, dosfstools 4.2, e2fsprogs 1.47.2, squashfs-tools 4.6.1, u-boot-tools 2025.01, fdisk (util-linux), OpenSSL 3.5 | image-layout, keyring and signature tests | LGPL-2.1 / GPL-2.0 / GPL-3.0+ / GPL-3.0+ / GPL-2.0 / GPL-2.0+ / GPL-2.0+ / GPL-2.0+ / Apache-2.0 |
| python3-cryptography 43 (Debian) | flash tool: manifest signature check (also a future runtime dependency of the flash tool) | Apache-2.0 or BSD-3-Clause |
| `btbridge-flash:dev` (Debian 13 + pip, exact pins): PySide6 6.12.0, cryptography 50.0.2, pytest 8.4.2, pytest-qt 4.5.0, coverage 7.16.2, PyInstaller 6.22.3; fonts-dejavu-core, fonts-urw-base35 | flash tool GUI tests (offscreen), screenshots, the Linux package build | LGPL-3.0/GPL / Apache-2.0 or BSD / MIT / MIT / Apache-2.0 / GPL-2.0 with bootloader exception / Bitstream Vera + public domain / AGPL-3.0 (URW base35) |

The logo (`webui/static/logo.svg`) is original to this project. It deliberately does **not** use the Bluetooth® trademark logo. The provisional product name is
"Hi-Fi Bridge"; "Bluetooth" is used only descriptively (DECISIONS #34); trademark questions remain open before a public release.

## Interactive demo (`make demo`, docs/DEMO.md; never shipped on a device)
Nothing below is redistributed by this repository: the demo image `hifi-demo:dev` is built locally from Debian packages, the other images are
pulled from their publishers. Versions as tested on 2026-11-17.
| Component | Use in the demo | Licence | Source |
|---|---|---|---|
| Debian 13 "trixie" base, Python 3.13 | demo image | various free licences (Debian) / PSF | debian:trixie-slim |
| PipeWire 1.4.2, WirePlumber, pipewire-pulse, pulseaudio-utils | the bridge's capture path (`pw-cat --raw`) | MIT / LGPL-2.1+ | Debian packages |
| dbus-daemon | private D-Bus for the simulated Bluetooth stack | AFL-2.1 or GPL-2.0+ | Debian package |
| Squeezelite 2.0 | the three simulated rooms' players | GPL-3.0-or-later | Debian package |
| flac, mpg123, vorbis-tools | decoding the user's own files (local only) | GPL-2.0+ / BSD (libFLAC), LGPL-2.1, GPL-2.0 | Debian packages |
| NumPy, jeepney, python-cryptography | music generation, D-Bus, flash-tool core | BSD-3-Clause, MIT, Apache-2.0 or BSD | Debian packages |
| Lyrion Music Server (`lmscommunity/lyrionmusicserver:9.1.2@sha256 pin`) | the real Lyrion server | GPL-2.0 and others | Docker Hub |
| Eclipse Mosquitto 2.0.20 | MQTT broker | EPL-2.0 or EDL-1.0 | Docker Hub |
| Home Assistant 2026.11.1 (optional profile) | MQTT discovery in a real Home Assistant | Apache-2.0 | ghcr.io |
| Playwright, axe-core (stack test only) | browser tests and WCAG audit | Apache-2.0, MPL-2.0 | Microsoft Playwright image, PyPI |
| Gitleaks v8.24.3 (CI and `scripts/run_secrets.sh`) | secret scan of the Git history | MIT | ghcr.io |
| Demo music, radio programme, notification sounds | generated in code (`demo/hifidemo/music.py`) | output: CC0-1.0 | this project |

## Flash tool (owner's computer; `flashtool/`, docs/FLASH_TOOL.md)
Shipped in the flash-tool packages (one-folder PyInstaller build, `flashtool/packaging/`). The licence texts are collected into
`licenses/` of every package by `flashtool/packaging/collect_licenses.py`, and the About dialog points to `THIRD_PARTY_NOTICES.txt`.

| Component | Use | Licence | How the obligations are met |
|---|---|---|---|
| Qt 6.12 (QtCore, QtGui, QtWidgets, QtSvg, QtSvgWidgets, QtPrintSupport + platform plugins), via the PySide6 wheels | GUI toolkit | LGPL-3.0 (option chosen; also GPL-2.0/3.0) | **dynamic linking**, one-folder build (never a single-file bundle), the Qt shared libraries stay separate files that the user can replace with own builds of the same version; LGPL-3.0 + GPL-3.0 texts shipped; notice + replacement hint in `THIRD_PARTY_NOTICES.txt` and in the About dialog ("About Qt" included); source: download.qt.io links + written offer to provide the corresponding source for three years |
| PySide6 / shiboken6 6.12.0 | Python bindings | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (LGPL chosen) | as Qt. **Finding:** the 6.12 wheels contain no licence text files (only the metadata expression), so the package ships the texts itself (Debian `common-licenses`) |
| python-cryptography 50.0.2 (+ cffi, pycparser) | manifest signature check | Apache-2.0 OR BSD-3-Clause (cffi/pycparser: MIT, BSD-3-Clause) | licence files from their dist-info copied into `licenses/` |
| OpenSSL 3 (inside the cryptography wheel) | crypto backend | Apache-2.0 | named in the notices |
| CPython 3.13 runtime | interpreter | PSF-2.0 | `Python-LICENSE.txt` |
| PyInstaller bootloader | starter executables | GPL-2.0 with the bootloader exception (allows any licence for the bundled program) | no obligation for the bundle beyond the notice |
| Hi-Fi Bridge flash tool itself | — | AGPL-3.0-only | `LICENSE-hifi-bridge.txt` + source link in the About dialog |

Runtime prerequisites that are **not** bundled on Linux: the desktop's X11/xcb or Wayland libraries (e.g. `libxcb-cursor0` for Qt ≥ 6.5),
fontconfig, `pkexec` (polkit) and `lsblk` (util-linux). Not yet done (G3/G4): a licence review of the actually built Windows and macOS
bundles (they include platform plugins and, on Windows, the MSVC runtime), and code-signing certificates (owner decision).
