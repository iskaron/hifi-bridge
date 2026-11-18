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
13. Licensing intent AGPL-3.0-only; planned GitHub repository private first, not assumed present. *(Repository location superseded by #69, then #70.)*
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

## Review decisions on the phase-2b proposals (owner + ChatGPT, 2026-11-16)

51. **P1 approved as the starting point:** Buildroot 2026.02 LTS with one shared `br2-external` tree for Pi Zero 2 W, 3 B+ and 4.
52. **P2 approved:** Avahi publishes the name and services in the image.
53. **P3: RAUC preferred.** U-Boot boot-count and rollback are **provisional only**. Feasibility must be shown per board; alternative boot
    mechanisms (e.g. the firmware's `tryboot`/`autoboot.txt`) stay open.
54. **P4 approved as the basic structure:** boot + rootfs-A + rootfs-B + data. File systems and sizes are chosen after measurement and
    according to RAUC compatibility.
55. **P5 revised:** development and release signing keys stay separate, **but** official signed updates must be installable on
    correspondingly configured self-built images. The trust configuration must be explicit, secure and documented. Release keys must never
    be present in ordinary build or PR jobs. (Implemented: docs/UPDATE_TRUST.md.)
56. **P6 revised:** no setup hotspot without an individual password the owner knows. Provisioning via the flash tool or a documented manual
    bootstrap file. Secrets on the FAT boot partition must be evaluated for residual data, takeover, failure cases and deletion.
    (Implemented: docs/PROVISIONING.md.)
57. **P7: the device CA stays exclusively on the device.** The timing contradiction (CA created at first boot vs. fingerprint shown by the
    flash tool) must be removed and a usable, secure out-of-band trust flow defined. (Implemented: docs/TRUST_BOOTSTRAP.md.)
58. **P8 approved:** physical SD recovery; check that a request runs exactly once and survives power loss.
59. **P9: PySide6 provisionally approved** for the flash tool; observe LGPL duties, packaging, disk detection and safe write/verify flows.
60. **Test coverage goal unchanged:** as complete as possible for own testable code; 90 % was an intermediate measurement, not a ceiling.

## Implementation decisions of the phase-2b preparation package (reversible; 2026-11-16)

61. **Update trust is a mandatory build choice** (`official` / `own` / `official+own`; no default). `post-build.sh` builds
    `/etc/rauc/keyring.pem` from public CA certificates only and records the choice in `/etc/hifi-bridge/update-trust.json`. Release bundles
    are produced by **re-signing** a development/CI-signed bundle offline (`rauc resign`), so building never needs the release key.
    Signing certificates must carry the codeSigning purpose (`check-purpose=codesign`).
62. **Provisioning slot:** the image ships `hifi-provision.json` as a fixed 4096-byte placeholder file on the FAT; the manifest records its
    byte offset, so the flash tool writes it on the raw card without mounting a filesystem. The device scrubs it in place after use.
63. **P7 flow = trust key + HMAC proof:** the flash tool puts a random 32-byte trust key on the card; after first boot it fetches the CA from
    `/trust/proof` with a fresh nonce and checks `HMAC-SHA256(trust key, nonce, SHA-256(CA))`; then it verifies HTTPS against only that CA.
    The device deletes the trust key when the owner password is set. Fallback: the fingerprint in `hifi-status.txt`.
64. **A separate `hifi-bridge-boot.service`** is the only unit that may write `/boot` (no network, exposure 0.6). It processes recovery,
    provisioning, creates the CA and writes the status file before the bridge starts.
65. **MBR partition table** (the Pi 3 / Zero 2 W boot ROM cannot read GPT); the data partition is grown on first boot with `sfdisk`, the
    file system with `x-systemd.growfs` (systemd-repart is GPT-only).
66. **No `dtoverlay=miniuart-bt`** in the product's firmware configuration (Buildroot's example sets it): Bluetooth keeps the PL011 UART.
    To be confirmed by the hardware gate.
67. **One OS tooling image** (`btbridge-os`) for Buildroot configuration checks, licence reports, RAUC/genimage tests and the flash tool
    tests. No board image is built in this package.
68. **Signal handlers never take locks** (`supervision.StopSignal`): they set a flag that the main thread polls (≤ 0.2 s). Reason: a real
    deadlock with back-to-back SIGTERMs (see docs/SERVICE_SUPERVISION.md).

## Central Git server (owner, 2026-11-17)
69. **Central repository `https://git.cativa.net/hifi-bridge/hifi-bridge.git`** (remote `origin`; a GitLab server). `main` and `develop` were pushed with their full, unchanged
    history. From now on, development commits on `develop` may be pushed to `origin/develop` automatically after the relevant tests
    pass and the commits have been checked for secrets. Pushing `main`, merging into `main`, force-push, tags, releases and history
    rewrites still need the owner's explicit approval. Supersedes the repository location in #13 and the "no push" boundaries of #21/#40.
    *(Location superseded by #70; the push rules stay.)*

## Migration to GitHub (owner, 2026-11-17)
70. **GitHub is the final platform: `git@github.com:iskaron/hifi-bridge.git`** (remote `origin`; the GitLab server stays as remote `gitlab`).
    * The repository stays **private** until the owner explicitly releases it. No automatic publication, no visibility change by agents.
    * **GitHub Actions** is the CI (`.github/workflows/ci.yml`, all suites in Docker + a full-history Gitleaks scan); **GitHub Releases** is
      the release platform. Releases and tags are created only by the owner.
    * **Official OTA source:** GitHub Releases of `iskaron/hifi-bridge`, fixed in images that accept official bundles
      (`update-trust.json` → `official_source`). The source is transport only; trust comes from the RAUC signature (docs/UPDATE_TRUST.md).
      Release signing keys never go into GitHub (no Actions secret); bundles are re-signed offline and uploaded by the owner.
    * Push rules of #69 apply unchanged to the new `origin`. History, identity (#22) and AGPL-3.0-only (#13) are unchanged; the hardware
      gate stays.

## Interactive demo and public-repository preparation (2026-11-17)
71. **Interactive Docker demo (`make demo`, docs/DEMO.md)** - a tool to try the product, never shipped on a device:
    * the bridge runs **unchanged** with `--source bluez` against a SIMULATED bluetoothd (`demo/hifidemo/vbluez.py`, built on the fake BlueZ)
      on a private D-Bus, with real PipeWire; virtual phones act only "over the air" (pairing agent, Device1/MediaTransport1/MediaPlayer1,
      A2DP audio into a `bluez_input.<MAC>` node). The product logic is never bypassed;
    * real Lyrion + three Squeezelite rooms; the audio monitor taps each player's PCM output (virtual DAC) behind Lyrion and Squeezelite;
      its added latency is measured live and documented;
    * the flash-tool core provisions setup code and trust key; the demo pre-sets `extra_hostnames=127.0.0.1` (an owner setting) so the
      notebook's browser can use HTTPS with the device CA;
    * isolation: own compose project, one image (`hifi-demo:dev`), every service on an `internal` network **without a host address**
      (`inhibit_ipv4`), only a gateway publishing to 127.0.0.1, ports 18080/18443/18100/19000/18123 by default, nothing privileged.
      Reason for `inhibit_ipv4`: on the development host the wizard's UDP discovery got answers from a real Lyrion server on the host
      through the host's address on an internal Docker network (read-only listing, nothing changed); the test lab got the same fix;
    * demo audio is generated in code (CC0-1.0 output); user files are decoded locally; Home Assistant is an optional profile.
72. **Public-repository readiness:** `LICENSE` (official AGPL-3.0 text; `bridge/LICENSE` identical copy for Buildroot, checked statically),
    copyright line in the README, AGPL section 13 source offer as a link in every WebUI footer (`btbridge.SOURCE_URL`), `SECURITY.md`,
    CI hardened for public pull requests (only `pull_request`, read-only token, no secrets, SHA-pinned actions + Dependabot). Changing the
    visibility, repository settings and releases remain the owner's (#69/#70).

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

## Phase 2b - software completion and hardware readiness (owner, 2026-11-18)
73. **Hardware-gate exception for software only:** the owner allows the software implementation of phase 2b (flash tool GUI, network
    management, OTA/update management, review and coverage work) **and complete internal Buildroot development builds** before gate G1 is
    passed. The exception covers software development, builds and hardware-free tests only. **All hardware gates (G1-G4) stay open**;
    no simulated, emulated or build-only result counts as hardware validation. Images built under this exception are internal,
    non-hardware-validated development artefacts and are never published (no release, no tag, no upload).
74. **Findings of the owner's personal demo test (2026-11-18)** are in scope: Docker/Compose minimum versions with a pre-flight check, entry
    scripts that build missing project images themselves, visible progress output, demo loudness (without changing the product audio
    engine without reason), clean-environment tests of the entry points, isolation regression tests against production Lyrion servers,
    reproducibly pinned Lyrion images.

## Implementation decisions of package 1 - review fixes (reversible engineering rules; 2026-11-18)
75. **No LMS call under the controller's state lock.** The controller takes a snapshot under the lock, calls LMS without it and
    revalidates under the lock afterwards (server, target, phone and session unchanged); a superseded start removes exactly the item it
    started, by URL. Status reads never wait for LMS I/O; the LMS host name is resolved in a worker with a 2 s timeout and cached 60 s.
    Health treats a loop waiting in an LMS call as busy and only a call over 60 s as hung (review H2, docs/SERVICE_SUPERVISION.md).
76. **Settings survive a downgrade:** the configuration store keeps unknown keys, values and secrets verbatim and never lowers
    `schema_version`; only undecodable content counts as corrupt, an I/O error is reported instead of resetting the device (review M8;
    needed for OTA rollback).
77. **Bare local host names are accepted** by the Host check (e.g. `http://hifi-bridge/` through a router's search domain); single-label
    names cannot be rebound through public DNS. Extends #35 (review M9).

## Implementation decisions of package 2 - demo and entry points (reversible; 2026-11-18)
78. **Docker minimums: Engine 24.0 + Compose 2.20; Engine >= 29 needs Compose >= 2.24.5** (Engine 29 refuses API 1.43 clients). Determined
    by validating the compose files with static Compose binaries 2.20.3-2.36.2 and by running the demo topology in disposable dind
    sandboxes on Engine 24.0.9-29.1.5 (docs/DOCKER_REQUIREMENTS.md, reports/evidence/docker_version_matrix*.log). `gw_priority` (Compose
    >= 2.33, the cause of the owner's failure) is no longer used: Docker puts the gateway's default route on the only non-internal network
    on every tested engine; isolation unchanged (#71). A pre-flight check (`make doctor`, run by every entry point) names the detected and
    required versions and how to upgrade, before anything is started.
79. **Entry scripts share `scripts/lib/common.sh`:** an image registry (Dockerfile, context, build args, dependencies) builds missing project
    images in dependency order and pulls third-party images itself; step counters, elapsed time, filtered build output and a heartbeat after
    15 s of silence; `-q`/`HB_QUIET=1` for CI; full logs under `/tmp/hifi-bridge-logs-<uid>/`.
80. **Reproducible images:** every third-party image is pinned by exact tag **and** manifest-list digest in `docker/image-pins.txt`
    (Lyrion 9.1.2 instead of `stable`); a static test rejects unpinned or moving references; pins are updated deliberately with
    `scripts/image_pins.sh` (docs/IMAGE_PINS.md). Resolves OPEN_QUESTIONS #48.
81. **Demo loudness (demo only, product audio engine unchanged):** rooms start at volume 75 (Lyrion's default 50 = -25 dB), generated audio
    at -13.6 dBFS RMS / -1 dBFS peak with a soft limiter, monitor default gain 4 behind a soft-knee limiter. Measured at the Wohnzimmer DAC:
    -42.1 → -25.7 dBFS RMS. Resolves OPEN_QUESTIONS #49.
82. **Isolation against production Lyrion servers is regression-tested** statically (every network internal + `inhibit_ipv4` except the
    gateway's `edge`; no host networking, `extra_hosts`, Docker socket; Lyrion only as `lms`) and at runtime (`scripts/check_isolation.sh`:
    no host address, TCP 9000/3483, UDP discovery and broadcast to host addresses fail), with a positive and a negative control.
