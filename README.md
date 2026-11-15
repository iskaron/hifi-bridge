# Bluetooth Audio Bridge

A Bluetooth A2DP sink that publishes a phone's audio as a live stream to **one** Lyrion (LMS) player or existing sync group. To a guest it
is an ordinary Bluetooth speaker: pair, connect, press Play. The owner configures it in a mobile-first WebUI (German/English).

**Status: Phase 2a (software product layers) on top of the accepted phase-1 feasibility result.** The development machine has **no** Bluetooth,
Raspberry Pi or audio hardware (owner decision). Everything runs in Docker:
* **Real:** Lyrion 9.1.2, Squeezelite 1.9.9, Mosquitto 2.0.20, PipeWire 0.3.65/1.4.2 tools, FFmpeg codecs, and a Chromium browser.
* **Simulated:** the phone and BlueZ.

**Nothing has been verified on real Bluetooth, phones, Squeezebox hardware or a Pi.** The plan and the kit for that are in `docs/HARDWARE_VALIDATION_PLAN.md`
and `hwkit/`. Current results: `reports/HANDOFF_FOR_CHATGPT.md`.

## Layout
```
bridge/btbridge/   product code, Python stdlib only (+ optional jeepney):
                   core/ stream/ lms/  audio bridge (ownership rules, live stream, LMS control)
                   adapters/bluez/     BlueZ/PipeWire adapter (prepared, NOT hardware-verified)
                   config/ system/ service.py webui/ mqtt/ appliance.py   (phase 2a)
hwkit/             hardware validation kit (record on the Pi, analyse in Docker) - runbook hwkit/README.md
testkit/           lab-only tools: simulated phone, fake BlueZ, PipeWire harness, appliance_lab
tests/             unit/ integration/ pipewire/ hwkit/ browser/ fixtures/
docker/            lab images + compose (isolated `internal` network)
docs/              spec, decisions, findings, security, third-party, PipeWire, BlueZ design, hardware plan
reports/           handoff, progress log, evidence logs, curated screenshots
```

## Requirements
Docker + Docker Compose only (tested: Docker 29.1.3, Compose 2.40.3, x86-64). Nothing is installed on the host.

## Run the tests (all in Docker)
```bash
scripts/lab_up.sh             # lab: LMS, 4 Squeezelite players, Mosquitto
scripts/run_unit.sh           # unit tests (seconds)
scripts/run_integration.sh    # real LMS/Squeezelite/Mosquitto + fake-BlueZ D-Bus + WebUI end-to-end (~17 min)
scripts/test_outage.sh lms    # LMS outage scenario (also: player1)
scripts/run_pipewire.sh       # capture command lines against real PipeWire 0.3.65 and 1.4.2
scripts/run_hwkit.sh          # kit self-tests incl. real SBC/AAC codecs
scripts/run_browser.sh        # real Chromium: owner journey, light/dark, phone/desktop, axe-core audit -> reports/screenshots/
scripts/run_all.sh            # everything; LOG=... selects the log file
scripts/lab_down.sh
```

## Try the appliance in the lab
```bash
scripts/lab_up.sh && cd docker && docker compose --profile browser up -d appliance
docker compose exec appliance cat /data/setup-code.txt         # one-time setup code
docker compose run --rm -T runner curl -s http://appliance:8099/connect   # simulated phone: connect | play | pause | disconnect
```
The WebUI listens on `appliance:8080` inside the isolated lab network. To view it in a desktop browser, publish the port temporarily, for example
by adding `ports: ["127.0.0.1:8080:8080"]` to the `appliance` service. On a target device: `python3 -m btbridge.appliance --data <dir> --source bluez`
(untested on hardware).

## Design in one paragraph
Phone PCM goes through `LiveFeeder` (real-time silence fill, bounded drop) into a bounded `PcmRing`, then out over HTTP as `/s/<token>/<session>.wav|flac`,
and LMS plays it via `playlist play`. The `Controller` keeps separate state dimensions and handles the 2 s takeover filter, the 30 s pause grace,
immediate release on disconnect, and session-id ownership. It only ever plays its own URL and removes its own URL, and never powers players off.
`ApplianceService` adds persistent settings (applied in place), owner auth, monitoring and MQTT. The WebUI and MQTT only talk to the service. See
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md` and `docs/SECURITY.md`.

## Git
Local repository, development branch `develop`, Conventional Commits, repository-local identity per DECISIONS #22. No remote, tags or releases; nothing is published.

License intent: AGPL-3.0-only. The licence file has not been added yet; third-party components are listed in `docs/THIRD_PARTY.md`.
