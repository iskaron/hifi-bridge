## Phase-1 evidence scope override (owner clarification)

Phase 1 is a **hardware-free Docker software integration spike**, not a physical Bluetooth proof. The gate checks synthetic/simulated A2DP input through the shared bridge pipeline, a real LMS process in a Docker container, and one or more software Squeezelite players. Validate stream/control, play/pause, ownership, reconnect and timing instrumentations in-container. A phase-1 handoff may be successful with explicitly deferred Bluetooth radio, Android/iPhone pairing/AVRCP volume, physical Squeezebox compatibility, Pi image boots and acoustic latency. Never conflate these deferred hardware acceptance tests with failed Docker tests; record them as unverified and required before product release.

# Acceptance and validation plan

## Phase 1 — audio feasibility evidence

- [ ] A documented reproducible synthetic live stream reaches real LMS and at least one available Squeezelite player, **or** report precisely why environment makes this untestable.
- [ ] A normal Android phone and an iPhone can each pair individually and stream audio when test devices are available; no phone app required. Record what remains untested.
- [ ] Play/Pause/Resume/Disconnect exercised; session-state behavior observed; measure Play-to-audible and Pause-to-silence on actual speakers when a capture rig exists.
- [ ] Source ownership race tests: switch from bridge to radio, then BT timeout/disconnect; never stop radio. Include async stale events.
- [ ] Paired one-source policy, conditional notification filter, inactive target behavior and server outage behavior tested where supported.
- [ ] Group and legacy Squeezebox streaming **explicitly verified or explicitly unverified** by model/firmware; don't extrapolate from Squeezelite to every player.
- [ ] Explain selected format, buffering strategy, bitrate, compatibility evidence, needed LMS settings/plugin (if any).
- [ ] Note device platforms, OS versions, versions/commands, test scope, exact observations and reproducible recordings/logs with tokens redacted.
- [ ] Design an executable simulated test harness for missing hardware as far as possible, with clear mock/real demarcation.
- [ ] Handoff report to ChatGPT and stop before phase 2.

## Hardware validation gate (after phase 1, before/within phase 2; owner-run)

Everything marked deferred in phase 1 is specified in `docs/HARDWARE_VALIDATION_PLAN.md` (test sets A-G with IDs and pass criteria): BlueZ/PipeWire bring-up,
pairing, AVRCP/notification traces, volume, capture-rig timing, soak/resource figures per Pi board, real Squeezeboxes by model/firmware, guest walk-through.
None of it has been executed; no compatibility may be claimed before it is.

## Phase 2 — product (after approval)

- [ ] Full reproducible Buildroot images for each **actually verified** target; no false compatibility badges.
- [ ] Secure onboarding/flash and discoverability/one-time credentials; accessible responsive WebUI, German/English, Hi-Fi/elegant UI.
- [ ] One-click manual 5-min pairing, remembered devices, live audio activity/status, test audio refuses occupied source.
- [ ] MQTT/HA optional discovery + controls, 5-min telemetry and immediate state/thermal events.
- [ ] Explicit preemption/ownership and volume behavior across phone devices and group rooms.
- [ ] Signed OTA with A/B rollback and configuration persistence; update interruption/recovery tests on board, manual update approval.
- [ ] Network fallback, IPv4/IPv6 compatibility and country rules; offline audio operation.
- [ ] Power, boot, memory, CPU, storage, RF coexistence, measured resource budgets; actual storage minimum and recommendation.
- [ ] Automated test suite/CI, documentation, security review; license compliance and third-party notices.
- [ ] Platform flash utility (Windows/macOS/Linux), recovery and upgrade directions, tested installers or clearly marked gaps.

## Reporting convention

Each result labeled `REAL-HARDWARE`, `REAL-LMS`, `INTEGRATION`, `SIMULATED`, `UNIT`, `STATIC-ONLY` or `UNTESTED`. A green simulated test does **not** imply physical compatibility. If no access to real devices, do all independent work and flag the hardware gate to owner.
