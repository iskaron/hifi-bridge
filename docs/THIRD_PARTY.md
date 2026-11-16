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
| `openssl` CLI | local CA and certificates for the WebUI | Apache-2.0 (OpenSSL 3) | needed for HTTPS in `local_ca`/`custom` mode (OPEN_QUESTIONS #29) |

## Development, lab and test only (never shipped)
| Component | Use | Licence |
|---|---|---|
| Debian bookworm/trixie images | lab containers | various (DFSG) |
| Lyrion Music Server 9.1.2 (`lmscommunity/lyrionmusicserver:stable`) | real LMS in the lab | GPL-2.0 |
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

The logo (`webui/static/logo.svg`) is original to this project. It deliberately does **not** use the Bluetooth® trademark logo. The provisional product name is
"Hi-Fi Bridge"; "Bluetooth" is used only descriptively (DECISIONS #34); trademark questions remain open before a public release.
