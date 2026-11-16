# Hi-Fi Bridge

Bluetooth audio for Lyrion: a Bluetooth A2DP sink that publishes a phone's audio as a live stream to **one** Lyrion (LMS) player or an existing
sync group. To a guest it is an ordinary Bluetooth speaker: pair, connect, press Play. The owner configures it in a mobile-first WebUI, available
in German and English, reached under `hifi-bridge.local` over HTTPS with the device's own local CA.

"Hi-Fi Bridge" is the provisional product name (DECISIONS #34). "Bluetooth" is used only descriptively, and the official Bluetooth logo is not used.

**Status:** Phase 2a, the follow-up, stabilisation and phase-2b preparation packages (2026-11-16), on top of the accepted phase-1 result.
Phase 2b is **prepared, not built** (`docs/PHASE2B_PLAN.md`):
* a Buildroot external tree (`os/`) whose configuration is checked in Docker;
* the flash tool core (`flashtool/`), tested with simulated cards;
* device-side provisioning, CA trust proof and recovery.

**No image has been built or booted.** Next is the hardware gate (`docs/HARDWARE_GATE_CHECKLIST.md`). The development machine has **no** Bluetooth,
Raspberry Pi or audio hardware (owner decision). Everything runs in Docker:
* **Real:** Lyrion 9.1.2, Squeezelite 1.9.9, Mosquitto 2.0.20, Home Assistant 2026.11.1, PipeWire 0.3.65/1.4.2 tools, Avahi 0.8, rsyslog 8
  (TLS), FFmpeg codecs, Chromium and WebKit; Python 3.11 (lab) and 3.14 (Buildroot's version).
* **Simulated:** the phone and BlueZ.

**Nothing has been verified on real Bluetooth, phones, Squeezebox hardware or a Pi.** For that, see `docs/HARDWARE_TEST_PROCEDURE.md` and
`hwkit/`. Current results: `reports/HANDOFF_FOR_CHATGPT.md`.

## Layout
```
bridge/btbridge/   product code, Python stdlib only (+ optional jeepney; openssl CLI for certificates):
                   core/ stream/ lms/          audio bridge (ownership rules, live stream, LMS control)
                   adapters/bluez/             BlueZ/PipeWire adapter (prepared, NOT hardware-verified)
                   config/ system/ service.py webui/ mqtt/ net/ supervision.py appliance.py
hwkit/             hardware validation kit (preflight, guided session, traces, captures, analysis) + pi-setup/
os/                Buildroot br2-external skeleton (3 boards, update trust, card layout) - configuration-checked, NOT built (os/README.md)
flashtool/         flash/provisioning tool core (disk policy, signed manifest, write/verify, provisioning, CA trust) - no GUI yet
packaging/         systemd unit (verified with systemd-analyze) + respawn wrapper - preparation for phase 2b
testkit/           lab-only tools: simulated phone, fake BlueZ, PipeWire harness, appliance_lab
tests/             unit/ integration/ pipewire/ hwkit/ browser/ homeassistant/ fixtures/
docker/            lab images + compose (isolated `internal` network); lab LMS state in .lab/ (host filesystem)
docs/              spec, decisions, security, local access/TLS, supervision, PipeWire, BlueZ, hardware plan and procedure
.github/workflows/ CI definition (prepared; the repository is not published, so it has never run on GitHub)
```

## Tests (all in Docker; Docker + Compose are the only host requirements)
```bash
scripts/lab_up.sh                 # lab: LMS, 4 Squeezelite players, Mosquitto
scripts/run_static.sh             # shellcheck, Python compile, systemd-analyze verify, actionlint
scripts/run_unit.sh               # unit tests
scripts/run_integration.sh        # real LMS/Squeezelite/Mosquitto + fake-BlueZ D-Bus + WebUI end-to-end
scripts/test_outage.sh lms        # outage scenario (also: player1)
scripts/run_pipewire.sh           # capture command lines against real PipeWire 0.3.65 and 1.4.2
scripts/run_hwkit.sh              # kit self-tests incl. real SBC/AAC codecs
scripts/test_mdns.sh              # mDNS: built-in responder and the Avahi backend against a real Avahi
scripts/test_syslog.sh            # optional remote syslog -> REAL rsyslog over verified TLS
scripts/run_py314.sh              # unit suite on Python 3.14
scripts/run_security.sh           # bandit gate (no unjustified MEDIUM/HIGH finding)
scripts/run_coverage.sh [--integration]   # line+branch coverage -> reports/evidence/coverage.txt (see docs/TEST_COVERAGE.md)
scripts/run_os.sh                 # Buildroot defconfig + licence checks, real-RAUC trust matrix, card layout, flash tool (simulated cards)
scripts/run_homeassistant.sh      # REAL Home Assistant 2026.11.1 + Mosquitto: discovery, availability, commands, telemetry, over-temperature
scripts/run_browser.sh            # Chromium/WebKit: keyboard-only wizard, a11y tree, axe-core, light/dark, phone/desktop, trusted HTTPS
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

## Try the appliance in the lab
```bash
scripts/lab_up.sh && cd docker && docker compose --profile browser up -d appliance
docker compose exec appliance cat /data/setup-code.txt                    # one-time setup code
docker compose run --rm -T runner curl -s http://appliance:8099/connect    # simulated phone: connect | play | pause | disconnect
```
Inside the isolated lab network the WebUI is `http://hifi-bridge.local:8080` and `https://hifi-bridge.local:8443`. On a target device (untested on hardware):
`python3 -m btbridge.appliance --data <dir> --source bluez --http-port 80 --https-port 443`.

## Design in one paragraph
Phone PCM goes through `LiveFeeder` into a bounded `PcmRing`, then out over HTTP as `/s/<token>/<session>.wav|flac`, and LMS plays it via
`playlist play`. The `Controller` only ever plays its own URL and removes its own URL; it never stops anything else and never powers players
off. `ApplianceService` adds persistent settings (applied in place), owner authentication, monitoring, read-only-by-default MQTT/Home Assistant,
local access (mDNS, HTTPS, Host validation), health checks and crash recovery. The WebUI and MQTT only talk to the service. See
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/SECURITY.md` and `docs/LOCAL_ACCESS_TLS.md`.

## Git
Local repository, development branch `develop`, Conventional Commits, repository-local identity per DECISIONS #22. No remote, tags or releases;
nothing is published.

License intent: AGPL-3.0-only. The licence file has not been added yet; third-party components are listed in `docs/THIRD_PARTY.md`.
