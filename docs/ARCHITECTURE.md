# Working architecture and hypotheses

Proposed flow: `Phone BT A2DP -> Linux/BlueZ + selected audio framework -> audio receive / session tracking -> bounded live buffer / optional encoding -> authenticated local HTTP media endpoint -> LMS external URL playback -> Squeezebox/Squeezelite (existing group)`.

This is a **hypothesis to prove**, not a declaration of LMS/legacy hardware support. Start by testing an endless synthetic audio stream into LMS. Then test a real Bluetooth source. If LMS can only accept a different streaming mode, document evidence and propose the smallest compatible design. Do not introduce a custom LMS plugin unless standard URL-based playback has been shown insufficient and the tradeoff is recorded.

Plan explicit interfaces and modules (names provisional):

- A2DP media receiver + AVRCP/transport metadata adapter
- Playback state machine + source ownership + pause/notification rules
- Live stream transport/transcoding adapter (bounded latency/buffering)
- LMS discovery/control/status adapter; target selection, group aware
- WebUI/admin service (pairing management, setup, status, diagnostics)
- MQTT/HA adapter, optional and entirely nonblocking
- OS/hardware adapter (Buildroot services, Wi-Fi config, watchdog, A/B boot)
- Update and recovery manager

**Risk map:**

- Running LMS live HTTP stream: player buffering/timeout, format support, sync groups, fallback transcoding.
- BT sound detection: A2DP transport state vs AVRCP vs simple waveform energy; pauses, silent intros, notifications.
- Pause latency: LMS/player output buffering can continue after phone stops sending; measure actual speaker output.
- Phone volume: AVRCP Absolute Volume availability and group volume semantics vary.
- Zero 2 W: shared 2.4GHz WLAN/BT, CPU/RAM (512MB) and buffering constraints. Pi 3 B+ with Ethernet is good reference.
- HTTP local token: exposure through LMS player logs and URLs, stream URL rotation impact on current clients.
- Immutable filesystem/OTA: real board support, rollback semantics, signature validation and power failures.

The owner expects a *shipping-quality* appliance eventually, but audio feasibility is a review gate. Spend phase 1 time on real playback behavior, measurement instrumentation, emulator/dummy stream tools, and reproducible evidence, not on full OTA/UI implementation.

## Phase-1 / review status of the architecture (2026-11-15)

Implemented and tested (software only): `Source adapter interface -> MediaFusion -> LiveFeeder/PcmRing -> HTTP live endpoint -> LMS (JSON-RPC)`,
with the ownership `Controller`. Layering is deliberate so a future Docker runtime or another host service can reuse the core:

```
[phone]  --A2DP/AVRCP-->  bluetoothd  <--D-Bus--  BlueZBus (JeepneyBus | fake)  -->  BluezModel (pure) -+
   |  decoded audio                                                                                      +--> BluezAdapter --> AudioSourceAdapter callbacks
   +--> PipeWire/WirePlumber node --> PcmCapture (PipeWireCapture | test double) ----------------------+          |
                                                                                                                  v
                                   BridgeRuntime: MediaFusion, Controller (state/ownership), LiveFeeder+PcmRing, StreamServer  --> LMS --> players
```
Boundaries: core (`btbridge/core`, `stream`, `lms`) has no hardware or D-Bus dependency; `adapters/base.py` is the only contract between "source" and
"bridge"; the simulated source (`testkit`) and the BlueZ adapter implement the same contract. The BlueZ side is prepared but **not verified on hardware**
(docs/BLUEZ_ADAPTER_DESIGN.md, docs/HARDWARE_VALIDATION_PLAN.md). Still design-only: WebUI, MQTT, OS/Buildroot services, OTA (phase 2).
