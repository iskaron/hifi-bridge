# Third-party components (licence intent of this project: AGPL-3.0-only, DECISIONS #13)

## Runtime on the device (bridge package)
| Component | Use | Licence | Notes |
|---|---|---|---|
| Python 3 standard library | everything (HTTP server, MQTT client, D-Bus types, config, auth) | PSF | no other Python packages needed |
| jeepney 0.8 (optional) | D-Bus client for the BlueZ adapter (provisionally approved, DECISIONS #23) | MIT | only imported with `--source bluez`; Buildroot availability and size unchecked |
| `parec` (pulseaudio-utils) or `pw-cat` (PipeWire) | PCM capture subprocess | LGPL-2.1+ / MIT | called as external programs; target image decision in phase 2b |
| BlueZ, PipeWire, WirePlumber | OS services on the device | GPL-2.0+ / MIT / MIT | not bundled by this repository |
| `flac` binary (optional) | FLAC stream encoding | GPL-2.0+ (tools) | only with `stream_format=flac` |

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

The logo (`webui/static/logo.svg`) is original to this project. It deliberately does **not** use the Bluetooth® trademark logo. Whether the product
name may contain the word "Bluetooth" is an open question (OPEN_QUESTIONS #19).
