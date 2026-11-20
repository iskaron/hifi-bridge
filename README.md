# Hi-Fi Bridge

Bluetooth audio for Lyrion: a Bluetooth A2DP sink that publishes a phone's audio as a live stream to **one** Lyrion (LMS) player or an existing
sync group. To a guest it is an ordinary Bluetooth speaker: pair, connect, press Play. The owner configures it in a mobile-first WebUI, available
in German and English, reached under `hifi-bridge.local` over HTTPS with the device's own local CA.

"Hi-Fi Bridge" is the provisional product name (DECISIONS #34). "Bluetooth" is used only descriptively, and the official Bluetooth logo is not used.

> **Development status - please read.** Hi-Fi Bridge is unfinished software. Everything has been developed and tested **only in Docker,
> without any Bluetooth, Raspberry Pi or audio hardware**. It has **never** run on a Raspberry Pi, **never** received audio from a real
> phone over real Bluetooth, and the internal development images have been **built but never booted**. Network management, A/B updates,
> rollback and the flash tool are verified only against simulated radios, simulated partitions and simulated cards. There are no releases.
> Do not use it for anything important yet. The hardware gate (`docs/HARDWARE_GATE_CHECKLIST.md`) comes next.

**Try it now - interactive demo** (Docker + Compose + make, nothing else on the host; `docs/DEMO.md`, German: `docs/DEMO.de.md`).
**Requirements: Docker Engine >= 24.0 and Docker Compose (v2 plugin) >= 2.20** - see `docs/DOCKER_REQUIREMENTS.md` for how these minimums were
determined. `make doctor` checks them (and disk, ports); `make demo` checks them itself before it starts anything and builds or pulls
every missing image with visible progress (the first run takes several minutes; `HB_QUIET=1 make demo` prints step lines only).
```bash
make doctor        # is this computer ready? (Docker/Compose versions, make, disk space, ports, images) - changes nothing
make demo          # builds and starts everything, prints the URLs and the one-time setup code
make demo-status   # URLs, setup code, MQTT credentials, resources
make demo-stop     # stop (data is kept); make demo-reset deletes only the demo's own data
```
The demo runs the **real** bridge software (unchanged, with its BlueZ adapter), a real Lyrion Media Server with three Squeezelite rooms
(Wohnzimmer, Schlafzimmer, Arbeitszimmer), Mosquitto and optionally Home Assistant. Phones, the Bluetooth radio and the rooms' speakers are
**simulated**; a browser audio monitor lets you hear what the rooms output behind Lyrion and Squeezelite.

**Where the project stands:** phase 1 (audio feasibility) is accepted; phase 2a (WebUI, configuration, MQTT/Home Assistant, hardware kit) and
phase 2b (software completion and hardware readiness) are done in software (`docs/PHASE2B_PLAN.md`, `reports/PROGRESS_PHASE2B.md`):
* network management: Ethernet/Wi-Fi, IPv4/IPv6, automatic setup hotspot, firewall, mDNS, time sync, recovery (`docs/NETWORK.md`);
* OTA updates on RAUC: signed bundles, A/B slots, health check, rollback, configuration backup (`docs/OTA.md`);
* the graphical flash tool (PySide6, Linux/macOS/Windows, DE/EN), tested with simulated cards only (`docs/FLASH_TOOL.md`);
* internal Buildroot development images for Pi 3 B+, Pi Zero 2 W and Pi 4 - **built in Docker, never booted** (`docs/IMAGE_BUILD.md`,
  firmware licences: `docs/IMAGE_LICENSING.md`);
* device-side provisioning, CA trust proof and recovery.

Real in the tests: Lyrion 9.1.2, Squeezelite, Mosquitto 2.0.20, Home Assistant 2026.11.1, PipeWire 0.3.65/1.4.2, Avahi 0.8, rsyslog 8 (TLS),
FFmpeg codecs, Chromium and WebKit, Python 3.11/3.13/3.14. Simulated: phones and BlueZ. Hardware procedure: `docs/HARDWARE_TEST_PROCEDURE.md`
and `hwkit/`. Latest results: `reports/HANDOFF_FOR_CHATGPT.md`.

"Hi-Fi Bridge" is the provisional product name (DECISIONS #34). "Bluetooth" is used only descriptively; the official Bluetooth logo is not used.

## Layout
```
bridge/btbridge/   product code, Python stdlib only (+ optional jeepney; openssl CLI for certificates):
                   core/ stream/ lms/          audio bridge (ownership rules, live stream, LMS control)
                   adapters/bluez/             BlueZ/PipeWire adapter (prepared, NOT hardware-verified)
                   config/ system/ service.py webui/ mqtt/ net/ supervision.py appliance.py
hwkit/             hardware validation kit (preflight, guided session, traces, captures, analysis) + pi-setup/
os/                Buildroot br2-external tree (3 boards, read-only root, A/B + RAUC, firewall, image checks) - built in Docker, never booted
flashtool/         flash tool: PySide6 GUI, privileged helper, platform disk adapters, packaging (Linux built; Windows/macOS prepared)
packaging/         systemd units (verified with systemd-analyze) + respawn wrapper
testkit/           lab-only tools: simulated phone, fake BlueZ, PipeWire harness, appliance_lab
demo/              interactive demo (make demo): virtual bluetoothd, virtual phones, audio monitor, rooms, gateway - SIMULATED devices
tests/             unit/ integration/ pipewire/ hwkit/ browser/ homeassistant/ demo/ fixtures/
docker/            lab images + compose (isolated `internal` network); lab LMS state in .lab/ (host filesystem)
docs/              spec, decisions, security, local access/TLS, supervision, PipeWire, BlueZ, hardware plan and procedure
.github/workflows/ GitHub Actions CI: every suite below in Docker, plus a full-history Gitleaks secret scan
```

## Tests (all in Docker; Docker >= 24.0 + Compose >= 2.20 are the only host requirements)
Every script checks its prerequisites first and builds the project images it needs (`scripts/lib/common.sh`); third-party images are pinned by
tag and digest (`docker/image-pins.txt`, `docs/IMAGE_PINS.md`).
```bash
scripts/run_entry_tests.sh        # entry scripts (fake docker), compose isolation rules, image pins - no daemon needed
scripts/test_docker_versions.sh   # Docker Engine 24-29 x Compose matrix in disposable Docker-in-Docker sandboxes
scripts/test_entry_clean.sh       # fresh clone, no images: make demo + hwkit_bundle end to end in a sandbox
scripts/lab_up.sh                 # lab: LMS, 4 Squeezelite players, Mosquitto
scripts/run_static.sh             # shellcheck, Python compile, systemd-analyze verify, actionlint
scripts/run_unit.sh               # unit tests
scripts/run_integration.sh        # real LMS/Squeezelite/Mosquitto + fake-BlueZ D-Bus + WebUI end-to-end
scripts/test_outage.sh lms        # outage scenario (also: player1)
scripts/run_pipewire.sh           # capture command lines against real PipeWire 0.3.65 and 1.4.2
scripts/run_pipewire_repro.sh     # reproduce the two PipeWire harness flakes (start-up race, unload crash) in disposable containers
scripts/run_hwkit.sh              # kit self-tests incl. real SBC/AAC codecs
scripts/test_mdns.sh              # mDNS: built-in responder and the Avahi backend against a real Avahi
scripts/test_syslog.sh            # optional remote syslog -> REAL rsyslog over verified TLS
scripts/run_py314.sh              # unit suite on Python 3.14
scripts/run_security.sh           # bandit gate (no unjustified MEDIUM/HIGH finding)
scripts/run_coverage.sh [--integration]   # line+branch coverage -> reports/evidence/coverage.txt (see docs/TEST_COVERAGE.md)
scripts/run_os.sh                 # Buildroot defconfig + licence checks, real-RAUC trust matrix, card layout, boot robustness, image tools
scripts/run_net.sh                # network: real nft/dnsmasq/hostapd/wpa_supplicant/networkd/avahi in containers, live firewall + DHCP
scripts/run_ota.sh                # OTA against the real RAUC 1.13 service (signatures, two-copy U-Boot environment)
scripts/run_flashtool.sh [coverage|package]   # flash tool GUI (offscreen Qt) + helper, simulated disks only; Linux package build
scripts/os_build.sh <board> [clean] build legal sbom   # internal development image in Docker (hours; docs/IMAGE_BUILD.md)
scripts/os_check_image.sh <board> # image checks + secret scan of a built image (also os_check_ota.sh)
scripts/run_homeassistant.sh      # REAL Home Assistant 2026.11.1 + Mosquitto: discovery, availability, commands, telemetry, over-temperature
scripts/run_browser.sh            # Chromium/WebKit: keyboard-only wizard, a11y tree, axe-core, light/dark, phone/desktop, trusted HTTPS
scripts/run_demo_tests.sh         # demo unit tests (virtual bluetoothd against the real BlueZ adapter, phones, monitor, console)
scripts/test_demo.sh              # full demo stack on its own project: setup, audio chain, takeover, multiroom, UI, P7, MQTT
scripts/run_secrets.sh            # Gitleaks over the complete Git history
scripts/run_all.sh                # everything; LOG=... selects the log file
scripts/lab_down.sh
scripts/build_images.sh [--cached]   # rebuild all project images reproducibly (--pull --no-cache by default)
scripts/cleanup_own_images.sh [--apply]   # owner-run: lists (dry run) / removes only superseded images of THIS project
```

## First setup and recovery (console or SD card only - never via WebUI/MQTT)
* Provisioning file `hifi-provision.json` on the boot partition: flash tool or by hand (docs/PROVISIONING.md). No hotspot without an
  owner-known individual password.
* `python3 -m btbridge.recovery --data <dir> reset-owner-password | factory-reset --yes`.
* `python3 -m btbridge.bootfiles --data <dir> --boot <boot partition>` (or `appliance --boot-dir`) processes `hifi-recovery` and the
  provisioning file before start, and writes a non-secret `hifi-status.txt` (name, CA fingerprint).

## Try the appliance in the test lab (developers)
`make demo` is the comfortable way (above). The older lab setup still works:
```bash
scripts/lab_up.sh && cd docker && docker compose --profile browser up -d appliance
docker compose exec appliance cat /data/setup-code.txt                    # one-time setup code
docker compose run --rm -T runner curl -s http://appliance:8099/connect    # simulated phone: connect | play | pause | disconnect
```
On a target device (untested on hardware): `python3 -m btbridge.appliance --data <dir> --source bluez --http-port 80 --https-port 443`.

## Design in one paragraph
Phone PCM goes through `LiveFeeder` into a bounded `PcmRing`, then out over HTTP as `/s/<token>/<session>.wav|flac`, and LMS plays it via
`playlist play`. The `Controller` only ever plays its own URL and removes its own URL; it never stops anything else and never powers players
off. `ApplianceService` adds persistent settings (applied in place), owner authentication, monitoring, read-only-by-default MQTT/Home Assistant,
local access (mDNS, HTTPS, Host validation), health checks and crash recovery. The WebUI and MQTT only talk to the service. See
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/SECURITY.md` and `docs/LOCAL_ACCESS_TLS.md`.

## Git
Central repository: **https://github.com/iskaron/hifi-bridge** (remote `origin`, DECISIONS #70; private until the owner releases it). CI runs on GitHub Actions, official
releases and OTA bundles will be published as GitHub Releases. Development branch `develop`, Conventional Commits, repository-local
identity per DECISIONS #22. `main` is pushed only with the owner's approval; there are no tags or releases yet. The former GitLab server
remains as remote `gitlab`. Details: `docs/REPOSITORY.md`.
```bash
git clone -b develop git@github.com:iskaron/hifi-bridge.git
scripts/run_secrets.sh            # Gitleaks over the complete history (Docker)
```

## Licence and copyright
Copyright (C) 2026 Oliver Meyer and contributors.

Hi-Fi Bridge is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License, version 3
only** (AGPL-3.0-only), as published by the Free Software Foundation. It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE).

As required by AGPL-3.0 section 13, the bridge's WebUI links to this repository's source code in every page footer. Third-party components
and their licences: `docs/THIRD_PARTY.md`. The demo's music, radio programme and notification sounds are generated in code
(`demo/hifidemo/music.py`); that audio output is dedicated to the public domain (CC0-1.0). Security issues: see [SECURITY.md](SECURITY.md).
