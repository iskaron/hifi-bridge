# Bluetooth Audio Bridge

A Bluetooth A2DP sink that publishes the phone's audio as a live stream to **one** Lyrion (LMS) player or existing sync group. To a guest it is an ordinary
Bluetooth speaker: pair, connect, press Play.

**Status: Phase 1 `audio-spike` accepted as a software-only feasibility result (owner review 2026-11-15); phase-1.5 work package: BlueZ/PipeWire adapter prepared without hardware.** There is **no** Bluetooth/Pi/audio hardware on the development host. Everything runs in Docker with a
**simulated** phone (A2DP/AVRCP) feeding the real pipeline, a **real Lyrion Media Server 9.1.2** and **real Squeezelite 1.9.9** players. This is *software
integration verified*; the real BlueZ/PipeWire adapter exists but is **not verified on any hardware**. Not proof of real phones, real radios, real Squeezeboxes, speaker timing or Pi boot (all *deferred hardware validation*).
Read `reports/HANDOFF_FOR_CHATGPT.md` for results and `docs/FINDINGS.md` for evidence.

## Layout
```
bridge/btbridge/   core library (stdlib only): core/ (state machine, ring, fusion, volume, pairing), stream/ (HTTP live endpoint, feeder), lms/ (JSON-RPC client, discovery), adapters/ (interface + `bluez/` adapter: hardware-free prepared, NOT hardware-verified)
testkit/btkit/     lab-only tools (numpy): simulated A2DP source, deterministic test signal, PCM tap ("virtual DAC"), analyzers, scenarios, demo
tests/unit/        UNIT tests (fake clock, fake LMS, scripted BlueZ events)   tests/integration/  real LMS + Squeezelite, simulated Bluetooth; fake-BlueZ D-Bus tests (test_08/09)
docker/            Dockerfile.lab + compose.yml (isolated `internal` network: no LAN/internet)
scripts/           run/measure scripts      measurements/  JSON evidence      reports/  handoff + full run log
```

## Requirements
Docker + Docker Compose only (tested: Docker 29.1.3, Compose 2.40.3, x86-64). Nothing is installed on the host; first run pulls `lmscommunity/lyrionmusicserver:stable`
(~230 MB) and `debian:bookworm-slim`. The lab network is `internal` (containers cannot reach the host LAN or internet).

## Run
```bash
scripts/lab_up.sh                 # build image, start LMS + player1..3 + legacy1 (PCM/MP3-only capability emulation)
scripts/run_unit.sh               # 88 unit tests, seconds
scripts/run_integration.sh        # 47 integration tests incl. fake-BlueZ D-Bus; ~16 min (use -k <name> or -m "not slow" to select)
scripts/test_outage.sh lms        # host-orchestrated: stop/start the LMS container while the simulated phone plays
scripts/test_outage.sh player1    # same for the player container
scripts/run_all.sh                # everything; log in $LOG (default reports/evidence/full_run.log); latest: reports/evidence/full_run_phase1_5.log
scripts/demo.sh                   # manual walkthrough with the simulated phone (HTTP control on demo:8099)
scripts/lab_down.sh               # delete containers/volumes
```
Measurement outputs land in `measurements/*.json` (each stamped with LMS/Squeezelite versions and a "NOT acoustic" note).

## Manual tests (lab)
After `scripts/demo.sh`: `docker compose -f docker/compose.yml run --rm -T runner curl -s http://demo:8099/<cmd>` with `connect`, `play`, `pause`, `notify`, `volume?v=100`,
`diag`, `disconnect`, `status`. Observe LMS with `docker compose -f docker/compose.yml run --rm -T runner python3 -c "..."` (see `btkit.lms_rpc`). To play "someone else's music"
on player1 use JSON-RPC `playlist play <url>`; the bridge must then go passive and never stop it.

## Design in one paragraph
Phone PCM -> `LiveFeeder` (keeps the timeline real-time: silence fill, bounded drop) -> bounded `PcmRing` -> HTTP `/s/<token>/<session>.wav|flac` -> LMS `playlist play`.
`Controller` implements the separate state dimensions, the 2 s takeover filter (only when a foreign source plays), 30 s pause grace, immediate disconnect release,
session-id ownership and the invariant "only `play` our URL / `deleteitem` our URL". See `docs/ARCHITECTURE.md`, `docs/PLAYBACK_STATES.md`, `docs/FINDINGS.md`.

## Failure evidence / negative results
`docs/FINDINGS.md` lists bugs the tests caught and the experiments that did not work (`icy-br`/pre-roll startup tuning:
`measurements/NEGATIVE_startup_policy_icybr_preroll.json`).

## Git
Local repository, branches `main` (phase-1 baseline) and `develop` (work), Conventional Commits. Nothing is published; no remote, tags or releases.

## Real hardware
Not available on the development host. The BlueZ/PipeWire side is documented in `docs/BLUEZ_ADAPTER_DESIGN.md`; what has to be tested on a Raspberry Pi with Android,
iPhone and real Squeezeboxes is in `docs/HARDWARE_VALIDATION_PLAN.md` (none of it executed). `python -m btbridge --source bluez` only works on a device with a system bus.

License intent: AGPL-3.0-only (not yet added; no publication).
