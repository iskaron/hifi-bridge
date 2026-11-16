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

## Phase-1.5 review decisions (owner + ChatGPT, 2026-11-15)

22. **Repository-local git identity: `Oliver Meyer <olm@cativa.net>`.** Checked on 2026-11-15: all 10 existing commits already carry exactly this
    author and committer (they used the same global identity), so no history rewrite was necessary. Re-check before any publication.
23. **`jeepney` is provisionally approved** as the D-Bus library. Buildroot compatibility, dependencies and resource use are still to be checked;
    the `BlueZBus` abstraction stays.
24. **Audio and playback rules unchanged** (#1-#21). Top goal re-confirmed: for a guest the device behaves like a Bluetooth speaker - connect,
    press Play, hear music; no Lyrion or Home Assistant knowledge needed.
25. **Phase 2a scope (this work package):** hardware validation kit, renewed PipeWire capture investigation in Docker, persistent configuration,
    pairing management, authenticated WebUI with setup wizard (DE/EN, responsive, light/dark, "Hi-Fi & elegant"), diagnostics, system monitoring,
    MQTT with Home Assistant discovery. **Not** in scope: productive Buildroot/A/B OTA implementation, the complete flash tool.

## Phase-2a implementation decisions (reversible engineering choices, not product changes; 2026-11-15)

26. **Runtime stays stdlib-only** (own small HTTP server for the WebUI, own MQTT 3.1.1 client); `jeepney` is the only optional runtime
    package. Reason: smallest Buildroot footprint, no dependency drift. The MQTT client is verified against real Mosquitto.
27. **WebUI = server-rendered pages + progressive JavaScript.** Every action works without JS; JS adds live status and the pairing countdown.
    Strict CSP without inline script. German/English dictionaries with completeness tests.
28. **Owner auth:** single owner account; random per-device setup code -> owner password (PBKDF2); in-memory sessions; CSRF token +
    origin checks. No default or shared credentials (PRODUCT_SPEC). Details: docs/SECURITY.md.
29. **Settings apply in place** (LMS server, target, format, token) - the runtime is never rebuilt, so a connected phone stays connected.
    Before a server AND a target are configured the bridge cannot send any LMS command.
30. **Default PCM capture = `parec --raw` with `node.dont-fallback`/`node.dont-reconnect`** (privacy: never capture another source),
    PipeWire graph must allow 44.1 kHz (docs/PIPEWIRE_CAPTURE.md).
31. **MQTT/HA:** topics `<base>/<id>/{availability,state,system,cmd/...}`, retained discovery for 14 entities; commands limited to pairing,
    bridge audio and the test tone. Telemetry every 5 min + at once on thermal state changes.
32. **Target selection** offers every player and each *existing* sync group (addressed via its first member); the bridge never changes groups.
33. **Hardware validation** is executed with `hwkit/`; results must be labelled REAL-HARDWARE and are rejected otherwise.

## Phase-2a review decisions (owner + ChatGPT, 2026-11-15) - follow-up package

34. **Provisional product name: "Hi-Fi Bridge".** "Bluetooth" may be used descriptively in the UI (e.g. "Bluetooth pairing"); never the
    official Bluetooth logo. Trademark and qualification questions must be settled before any public release. The owner-chosen *device name*
    (Bluetooth name, e.g. "Living room") is shown separately from the product name.
35. **Local access & HTTPS (hardware-independent foundations):** stable mDNS name (e.g. `hifi-bridge.local`), local certificate management,
    own certificates and optional reverse proxies, certificate renewal, documented trust setup, DNS-rebinding protection via Host validation.
    No public domain or cloud. mDNS alone creates no trust - a traceable security/setup concept is required; **no silent TLS exceptions**.
36. **MQTT default is read-only.** Discovery for status, diagnostic and system entities. Control functions (pairing window, test tone, bridge
    playback) are **separately and explicitly** enabled and **off by default**. Broker auth/TLS/ACL respected. System telemetry ~every 5 min.
    A critical CPU temperature is published at once as a warning, **independent of the telemetry cycle**; local over-temperature checks run
    often enough.
37. **A reproducible Docker test with a real Home Assistant + Mosquitto** is required (discovery and entity types, device mapping, units,
    availability, reconnect, restart, removal of stale entities, default-disabled commands, separately enabled commands, telemetry, immediate
    over-temperature warning). No integration may be claimed before it actually ran.
38. **WebUI:** design approved; improve long mobile settings pages, balanced desktop card layouts, clear product-vs-device naming, keyboard and
    screen-reader accessibility as far as software-testable. DE/EN, responsive, light/dark stay.
39. **Prepare:** robust service supervision and restart strategy, CI for all tests, hardware kit completed as far as possible without hardware,
    documented hardware test procedure, consistent result labels (UNIT, INTEGRATION, SIMULATED, REAL-HARDWARE).
40. **Boundaries unchanged:** no productive Buildroot, OTA or flash tool (concepts only); no push, tag, publication or history rewrite;
    local commits on `develop` as `Oliver Meyer <olm@cativa.net>`; never present simulated results as hardware validation.

## Implementation decisions of the follow-up package (reversible engineering choices; 2026-11-15)

41. **Local CA per device with critical name constraints** (local names + private IPs), server certificates 397 days, renewal 30 days before
    expiry or on name/address change; own certificate and reverse-proxy modes; certificates via the openssl CLI.
42. **HTTP stays available for bootstrap** (/trust, CA download); **HTTPS-only can only be enabled from an HTTPS session**; no HSTS yet.
43. **Own stdlib mDNS responder** by default (interop-tested with Avahi), `--no-mdns` for an OS-level publisher.
44. **MQTT read-only variants** of disabled controls are binary sensors; the inactive variant is removed with an empty retained config.
    Thermal state has its own retained topic, checked every 10 s.
45. **Supervision:** systemd `Type=notify` + watchdog driven by internal heartbeats; crash-recovery journal; respawn wrapper for other inits.
46. **Settings UI** = overview + one page per section; errors are `role=alert` and receive focus.
47. **Lab LMS state on the host filesystem** (`.lab/`), independent of Docker's data filesystem.

## Review decisions for the stabilisation package (owner + ChatGPT, 2026-11-16)

48. **HSTS: off by default for the local WebUI.** No automatic HSTS for `hifi-bridge.local`, a captive portal or local certificates. Possibly
    later as an option for deliberately configured HTTPS installations. No silent TLS exceptions and no weakening of certificate checks.
    (Regression tests: no `Strict-Transport-Security` header in any mode.)
49. **mDNS: Avahi is the preferred production solution.** Check its Buildroot suitability and resource use. The own responder is used only where
    it has a demonstrated technical advantage (today: runtimes without an Avahi daemon, e.g. the Docker lab or a future Docker runtime).
    Existing functionality, discovery tests and clean goodbye stay.
50. **OpenSSL is allowed** for cryptography, TLS and certificate management. Check Buildroot availability, licence compatibility and dependencies;
    keep the package set small without weakening security; never replace it with own cryptography.

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
