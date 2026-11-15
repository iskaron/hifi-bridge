# PHASE 1 — Autonomous audio feasibility and quality spike

You are the **lead developer and test engineer** for Bluetooth Audio Bridge. Read `CLAUDE.md`, `AGENTS.md`, `docs/PRODUCT_SPEC.md`, `docs/DECISIONS.md`, `docs/PLAYBACK_STATES.md`, `docs/ARCHITECTURE.md`, and `docs/ACCEPTANCE.md` before writing production code.

## Goal

Produce a reproducible, running audio-path prototype (or a precise, evidence-backed feasibility blocker) that demonstrates the guest experience: simulated ordinary-phone A2DP source -> bridge -> real Docker LMS -> software Squeezelite player(s) or synced group. Real Android/iPhone hardware tests are a *later separate validation* rather than a phase-1 requirement. The owner should not need to babysit development. Make supported technical decisions autonomously. Do **not** start full Buildroot, WebUI, MQTT or OTA implementations in this first assignment unless absolutely necessary to prove audio.

## Execution instructions

1. Inspect repository and Docker/Compose availability **only**; do not probe host Bluetooth/audio/Raspberry Pi hardware, `/dev`, USB devices or local-network LMS services. The development workstation has no relevant physical devices. Research authoritative LMS/BlueZ APIs where uncertain. All work, including tooling and tests, runs inside Docker.
2. Build a fully containerized fixture with **real LMS server and headless software Squeezelite player(s)**. First verify synthetic live audio over external HTTP URL playback, including command flow, media format and sync. Then connect a **simulated A2DP source** through a hardware-independent receiver interface. Architect the separate real BlueZ/PipeWire adapter, but do not attempt physical integration on this machine.
3. Implement robust separated media state/ownership logic with tests for Play, Pause, grace 30 s, disconnect, manual source takeover, fresh Play to retake, server recovery, active source exclusivity, pairing window logic where feasible. For missing hardware, implement realistic unit/integration simulation; label it honestly.
4. Research and, where possible, test phone metadata/AVRCP and volume, the ~2 s filter only during takeover, initial buffering, live stream silence/timeout and source competition.
5. Evaluate LMS synchronization with multiple containerized software players and investigate legacy format compatibility using documented capabilities or available software emulation. Mark real legacy hardware compatibility unverified; do not scan for or request physical devices.
6. Instrument software timing and captured/discarded PCM output in Docker. There is **no physical audio test rig**; never seek one or claim real audible output timing. Don't optimize to a fabricated response-time cutoff. Compare baseline and reasonable improvements; preserve sample integrity and a bounded memory footprint.
7. Add repeatable test commands/scripts and README with requirements, setup, manual tests and failure evidence. Keep owner secrets, home server access and public infrastructure untouched.
8. Keep working through independent tasks. **Do not stop hourly or ask to approve minor choices.** Stop only on meaningful completion or an actual external blocker. Document failures and what you attempted, not just positive outcomes.
9. Write comprehensive `reports/HANDOFF_FOR_CHATGPT.md` using `docs/HANDOFF_TEMPLATE.md`. Print a concise owner-facing handoff summary, stop. Do not automatically proceed to phase 2.

## Explicit acceptance gate

A theoretically plausible architecture or mock-only passing tests are insufficient. Require **real Docker LMS + software player integration with simulated Bluetooth audio input** as the phase-1 software proof. This is not evidence of physical Bluetooth pairing, actual phones, legacy devices or speaker output; explicitly defer those to hardware testing after the software milestone. The review determines when to move to full product work.
