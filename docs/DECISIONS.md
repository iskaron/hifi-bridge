# Decision register

## Locked product decisions

1. Guest experience: **discover/pair, connect, Play**, with normal phone apps; guests never interact with LMS or HA.
2. Owner starts time-limited pairing in a quickly reachable authenticated WebUI; paired-device management and persistent devices.
3. One active BT source, configurable Bluetooth name; pairing and playback permission are separate controls.
4. Lyrion selected server and chosen player/existing group persist. No automatic alternative target, no sync regrouping.
5. Bridge must never stop unrelated LMS playback, including after late timers/disconnects. Bluetooth may override LMS by a genuinely new media session (default enabled); no automatic restoration of old track.
6. No arbitrary numeric response-time requirement before actual prototype measurements. Play may take seconds; Pause should feel quick.
7. Conditional ~2 s notification filter when interruption of existing LMS source is at stake; no default delay for idle target.
8. Pause logical timeout 30 s; disconnect immediate bridge stop; stale audio not replayed after LMS downtime.
9. Stream local + tokenized; mobile-first WebUI with Hi-Fi & elegant consistent CI; no shared passwords.
10. Buildroot appliance, target Pi Zero 2 W / 3 B+ / 4; full signed A/B OTA and rollback, optional update check, never auto-install updates.
11. MQTT optional, HA autodiscovery, system metrics 5 min, critical thermal warning immediate.
12. Quality first, integration/hardware evidence required. Phase 1 audio proof, review gate, then phase 2 autonomous full build.
13. Licensing intent AGPL-3.0-only; planned GitHub repository private first, not assumed present.
14. Future Docker support by architecture, not initial implementation.

## Measurement-driven / not yet verified

- Startup latency, Pause/drain latency, reconnection and sample integrity (measure on actual devices).
- End-to-end transport, stream format(s), LMS buffering, and legacy Squeezebox behavior.
- AVRCP playback status and absolute volume cross-device reliability.
- Pi Zero 2 W RF coexistence and resource margin.
- Safe Pi-specific boot/rollback route, true minimum media capacity.
- Whether all desired Lyrion behaviors are achievable with standard LMS APIs **without a custom server plugin**.

Do not turn measurements into artificial requirements. Ask for an explicit decision if a product constraint would need to change.

## Owner clarification, 2026-11-15: development host has no physical audio hardware

Phase 1 is **Docker-only**; do not probe or demand Bluetooth dongles, Pi hardware, sound cards, physical players or live LMS hosts. Build an actual in-Docker LMS and headless players, inject simulated phone Bluetooth audio through a replaceable adapter, and label the measured and unmeasured parts rigorously. Physical radio and device testing is a separate later-stage validation, not a phase-1 blocker.
