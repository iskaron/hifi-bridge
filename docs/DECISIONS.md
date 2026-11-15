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

## Phase-1 review decisions (owner + ChatGPT, 2026-11-15)

Phase 1 is accepted as a **software-feasibility result** (simulated Bluetooth, real Docker LMS + Squeezelite). Real Bluetooth and
Raspberry Pi integration is explicitly **not yet verified**. Evidence: `docs/FINDINGS.md`, `reports/HANDOFF_FOR_CHATGPT.md`.

15. **Pause default = `drop_restart`** (our LMS item is removed at once -> silence in ~0.07 s; logical 30 s grace kept; resume starts a fresh
    session from live audio). `keepalive_silence` stays available as a non-default option.
16. **Start/Resume latency of ~1.6 s is accepted for now** (LMS 255 KB start threshold). **No Lyrion plugin for latency optimisation.**
    Revisit only if hardware measurements show it is a problem for guests.
17. **Players powered on by the bridge stay on.** The bridge never sends `power 0` / any power-off, also not after grace expiry, disconnect,
    audio toggle or target change. (Only `power 1` on takeover of a standby target.)
18. **ICY metadata stays off by default** until real older Squeezebox hardware has been tested for compatibility.
19. **Top product goal unchanged:** the Bluetooth-speaker experience - guest pairs, opens any audio app, hears music through Lyrion without
    operating Lyrion or Home Assistant.
20. **Development host has no Bluetooth/Pi hardware** (re-confirmed): no hardware discovery, no host installs, no hardware tests there;
    build and tests run in Docker only.
21. **Phase-1.5 scope (this work package):** git repository + conventional commits on a `develop` branch (nothing published, no releases),
    hardware-free preparation of the real BlueZ/PipeWire adapter, and a documented hardware validation plan (`docs/HARDWARE_VALIDATION_PLAN.md`).
    No Buildroot, OTA or full product build yet.

## Measurement-driven / not yet verified

- Startup latency (**software: ~1.6 s, accepted #16**), Pause/drain latency (**software: 0.07 s with #15**), reconnection and sample integrity (still to be measured on actual devices).
- End-to-end transport, stream format(s), LMS buffering, and legacy Squeezebox behavior.
- AVRCP playback status and absolute volume cross-device reliability.
- Pi Zero 2 W RF coexistence and resource margin.
- Safe Pi-specific boot/rollback route, true minimum media capacity.
- Whether all desired Lyrion behaviors are achievable with standard LMS APIs **without a custom server plugin**.

Do not turn measurements into artificial requirements. Ask for an explicit decision if a product constraint would need to change.

## Owner clarification, 2026-11-15: development host has no physical audio hardware

Phase 1 is **Docker-only**; do not probe or demand Bluetooth dongles, Pi hardware, sound cards, physical players or live LMS hosts. Build an actual in-Docker LMS and headless players, inject simulated phone Bluetooth audio through a replaceable adapter, and label the measured and unmeasured parts rigorously. Physical radio and device testing is a separate later-stage validation, not a phase-1 blocker.
