# Open questions and empirical findings

Add numbered items as implementation discovers genuine specification conflicts or test-dependent decisions. Do not ask the owner for every technical implementation detail. Prefer reversible choices and mark them. First known research questions:

1. Which LMS live-stream transport and codec strategy works reliably with synchronized Squeezelite and older physical Squeezeboxes, without a custom plugin?
2. Can AVRCP reliably discriminate media playback vs notifications on the target phones? How must false positives be handled?
3. Can Pause stop audible LMS output promptly despite buffering? Which tested strategy yields the best UX?
4. Can group volume reflect phone volume while maintaining relative speaker levels and preventing loudness jumps?
5. Which Pi boot A/B method actually supports verified rollback for each target board?
6. What is the measured A/B+data+OTA storage requirement — not guessed 32 GB?
7. Which bootstrap authentication method is robust for Ethernet, Wi-Fi setup and headless recovery without universal passwords?

These questions are research tasks, not permission to change accepted requirements without owner approval.

---
## Findings and new questions from Phase 1 (2026-11-15; evidence labels in docs/FINDINGS.md)

1. **LMS live-stream transport (answers Q1 for software players):** WAV/PCM endless HTTP URL via standard JSON-RPC works bit-exact with Squeezelite,
   in sync groups, and for a PCM+MP3-capability emulation; FLAC also works (LMS transcodes for PCM-only players). No LMS plugin needed. Real Squeezebox hardware: UNVERIFIED.
2. **Q2 (AVRCP vs notifications):** only simulated. Core handles AVRCP-present and AVRCP-absent phones; needs real Android/iPhone traces (docs/BLUEZ_ADAPTER_DESIGN.md checklist).
3. **Q3 (Pause latency):** answered for LMS+Squeezelite in software: `drop_restart` silences in 0.06-0.08 s; keepalive 1.6 s. **Owner decision:** keep `drop_restart` as default?
4. **Startup latency ~1.6 s is an LMS/Squeezelite buffering floor** (255 KB threshold). Reducing it would need an LMS plugin protocol handler or accepting a custom setup -
   conflicts with "no custom LMS plugin unless shown insufficient". **Owner decision:** accept ~1.6 s (spec says seconds are acceptable) or authorize a plugin investigation later.
5. **Q4 (volume):** relative `mixer volume +/-N` preserves per-member balance on a sync group (lab). Absolute-volume behaviour of real phones is open. Ceiling (85) and max step (10) are provisional.
6. **Power state:** the bridge wakes a standby target (`power 1`) on takeover but does not power it off afterwards. Is leaving it on acceptable? (reversible choice made: leave on.)
7. **Ownership race window:** LMS has no compare-and-swap; the residual window is observe -> `play` during an intentional takeover. All releases are URL-addressed (`playlist deleteitem <url>`), verified safe against a foreign source.
8. **User pressing Stop in LMS** while the bridge streams is treated as a manual source change (ownership lost, not resurrected) unless an outage was observed since session start.
9. **ICY metadata on WAV** works with Squeezelite; legacy hardware may mishandle in-band metadata on PCM streams -> default **off** (`icy_metadata=False`) until verified.
10. **Slow-clock phones** cause rare ~40 ms silence gaps; resampling-based drift compensation is a possible phase-2 improvement.
11. **Token in URL:** by design it passes through LMS/player logs (LMS playlist holds the URL). Rotation affects only new sessions; a running session continues.
12. Docs inconsistency noted: ACCEPTANCE.md phase-1 item "Android phone and iPhone pair" cannot be done in phase 1 per the owner clarification -> recorded as deferred hardware validation, not a failure.

---
## Resolutions from the phase-1 review (owner + ChatGPT, 2026-11-15) - see docs/DECISIONS.md #15-#21

| Item | Resolution |
|---|---|
| Q3 / finding 3 - pause strategy default | **Decided: `drop_restart`.** `keepalive_silence` remains an option. Still to confirm on hardware (E2 in docs/HARDWARE_VALIDATION_PLAN.md). |
| Finding 4 - ~1.6 s start/resume | **Accepted for now; no Lyrion plugin.** Revisit only if hardware measurements/guest walk-through show a problem. |
| Finding 6 - power state | **Decided: bridge-woken players stay on; the bridge never sends power-off.** Pinned by unit tests. |
| Finding 9 - ICY metadata | **Decided: default off** until real older Squeezebox hardware has been tested (F2). |
| Doc inconsistency (finding 12) | Acceptance wording for phone pairing in phase 1 is superseded by the hardware validation gate (docs/ACCEPTANCE.md). |

Still open (need hardware, none executed): Q2 (AVRCP vs notifications), Q4 (volume), Q1 for real Squeezeboxes, Q5-Q7 (phase 2 topics).

## New questions from the BlueZ/PipeWire preparation (2026-11-15)
13. **PipeWire capture of a phone's A2DP audio:** node name/properties for a phone acting as A2DP *source*, and whether `pw-cat --record ... -` writes headerless PCM
    (0.3.65 has no `--raw`). Alternative prepared: `parec` via pipewire-pulse. -> A3/A4. Fallback if neither is suitable: BlueZ `MediaEndpoint1` + in-process SBC decode.
14. **D-Bus library for the appliance image:** jeepney (pure Python, MIT) was used for the lab-verified backend; its availability/size in a Buildroot image is unchecked.
    The `BlueZBus` interface isolates this choice. Decide in phase 2 once the image toolchain is known.
15. **Is `--experimental` required** for `MediaPlayer1` on the shipped BlueZ version? (A1) Without it AVRCP status/metadata may be missing and fusion must rely on transport + PCM only.
16. **Pairing agent policy** (Just-Works, authorise only inside the 5-minute window or for paired devices): does this work with iOS and Android in practice, including service
    authorisation on reconnect? (B1-B3)
17. **Unknown UUIDs at connect time:** the translator treats a device without a UUID list as a phone. Real behaviour of `Connected` vs `ServicesResolved` ordering needs a real trace (A6/C).
18. **Reconnect semantics after bluetoothd restart** are specified (release, re-register agent, re-sync) but only exercised against the fake (A7).
