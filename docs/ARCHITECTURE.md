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
