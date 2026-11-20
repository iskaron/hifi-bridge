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

## Implementation decisions of package 3 - network management (reversible; 2026-11-18; details docs/NETWORK.md)
83. **Privilege boundary:** a small root agent `hifi-netd` accepts six JSON requests (`status`, `trial`, `confirm`, `revert`, `scan`,
    `hostname`) on `/run/hifi-net/agent.sock` (0660 root:hifi-bridge, SO_PEERCRED: root or the bridge's uid, ≤ 4 KiB). No request carries
    configuration: the bridge owns `network.json` (0600); the agent reads it strictly (no symlinks, owner, mode, size), checks its SHA-256
    against the request id, validates it again and never writes the bridge's data. An invalid file means Ethernet + DHCP only.
84. **Hotspot policy:** only with owner-known hotspot credentials + a regulatory country + no working Ethernet uplink (P6, #56). Opens 60 s
    after the configured Wi-Fi fails, or 15 s after start without configured Wi-Fi; retries the Wi-Fi after 2 min, 5 min, then every
    10 min, never while a phone is connected to the hotspot and only if a scan sees the network; closes after Ethernet returns and 30 s idle.
    Client Wi-Fi and hotspot are mutually exclusive, also enforced by systemd `Conflicts=` between the units.
85. **Owner network changes are trials:** a change that affects the connection the owner may be using must be confirmed on the page within
    3 min, otherwise it is rolled back automatically; a failed Wi-Fi attempt rolls back at once; after a power cut the last confirmed
    configuration applies. SD-card/console recovery `network-reset` forgets client Wi-Fi and fixed addresses (keeps country and hotspot).
86. **Hotspot network:** 10.42.0.1/24, WPA2-PSK/CCMP, client isolation, ≤ 8 stations, dnsmasq DHCP only (no router, no DNS - no captive-portal
    emulation); WPA3-SAE for the hotspot only after hardware test N-4.
87. **No SSID or password as text in daemon configuration files** (WPA2 PMK hex, SAE password hex, hostapd `wpa_psk` hex, SSIDs hex) -
    found by the real-tool test: wpa_supplicant strips `#` outside quotes, so a passphrase containing `"` or `#` was silently truncated.
88. **V1 scope:** one saved client Wi-Fi network; open, WEP, WPA1 and enterprise networks are not supported.
89. **OS stack:** systemd-networkd + timesyncd + hostnamed + nftables (inet, default deny inbound/forward; LAN: WebUI 80/443, stream 8090,
    mDNS, DHCP/ND replies, Lyrion discovery replies, rate-limited ICMP; hotspot clients: DHCP + WebUI only); base firewall loaded before
    `network-pre.target`; Avahi host name follows `local_hostname` through the agent; wait-online `--any --timeout=15`; the time-sync clock
    file persists on the data partition (no RTC).

## Implementation decisions of package 4 - OTA update management (reversible; 2026-11-19; details docs/OTA.md)
90. **RAUC access:** the bridge talks to RAUC over D-Bus; our policy allows only root and `hifi-bridge` (upstream allows every local user).
    "Restart now" creates an empty file in `/run/hifi-bridge-ota`; a root `hifi-bridge-reboot.path`/`.service` reacts to it and reads no
    input. No other root helper.
91. **Mark-good only from the health check** (`hifi-bridge-health.service`: `/data` mounted, state writable, loopback `/healthz` ok or
    degraded, RAUC reachable); upstream's unconditional `rauc-mark-good.service` is masked. A failed first boot of a new slot: `mark-bad` +
    reboot to the previous slot; a failure on an established slot does not reboot (U-Boot's boot-count falls back after three boots).
    Manual rollback (`mark-active other`) only when the other slot is bootable and its version is known.
92. **Staging requires RAUC's signature check (`InspectBundle`, keyring, codeSigning) and the manager's own compatible check** - real
    RAUC 1.13 does not compare the compatible during inspection; payload integrity is enforced by dm-verity at install time.
93. **Pre-update snapshot** of settings, secrets, network configuration and certificates before every install (no install without it);
    restored **only on the owner's request** (ConfigStore keeps newer settings across a rollback, #76); factory reset removes snapshots.
94. **Configuration backup:** versioned JSON; secrets excluded by default; optionally the owner password hash, stream token and MQTT
    password are encrypted with an owner passphrase - scrypt (salted, per file) → AES-256-CTR via the openssl CLI (key material only through
    an inherited pipe, never argv/environment/files) + encrypt-then-MAC HMAC-SHA256 checked before decryption (#50: no own cryptography; a
    first self-built keystream was rejected in review). The device CA key and sessions are never exported.
95. **Official update source:** release assets are accepted only from `<official repository>/releases/download/`; the update check is
    manual and never installs (#10); it works only once releases are public (OPEN_QUESTIONS #45). `make ota` (`scripts/os_bundle.sh`) builds
    bundles with throw-away development keys and refuses certificates without codeSigning or keys tracked by git.

## Implementation decisions of package 5 - flash tool (reversible; 2026-11-19; details docs/FLASH_TOOL.md)
96. **Privilege separation:** the PySide6 GUI runs as the normal user (refuses root) and has no write logic; one elevated helper run per
    write job (no Qt) validates the request strictly (whole-disk paths only, absolute non-device image path, manifest, slot-sized
    provisioning), enumerates disks again itself, requires the same identity (path + size + serial), applies the full policy, unmounts/locks,
    opens exclusively, checks the opened size, writes, reads back with the OS cache bypassed and invalidates the card on any failure.
    Elevation: Linux `pkexec` with a polkit action for exactly the helper path (administrator password every time); macOS `osascript ... with
    administrator privileges`; Windows UAC (`requireAdministrator` only on the helper). macOS/Windows helpers connect back to 127.0.0.1 and must
    present a 256-bit one-time token from a 0600 file - never on a command line. End of input from the GUI cancels and invalidates.
97. **Disk safety (review M11/M12):** mounts are collected from the whole device tree (LUKS/LVM, swap counts as a system mount); disks held by an
    open volume manager are refused; macOS resolves the boot disk via `diskutil info -plist /` and APFS physical stores; mounted volumes are
    unmounted by the helper and re-checked. Read-back bypasses the cache (Linux `BLKFLSBUF` + `POSIX_FADV_DONTNEED`, macOS `/dev/rdiskN` +
    `F_NOCACHE`, Windows unbuffered write-through I/O); the existing read-back evidence is labelled SIMULATED.
98. **Trust in images:** an official image needs the built-in official key (none exists yet → official images are shown untrusted, OPEN_QUESTIONS
    #38); a custom publisher key is trusted only after explicit addition with fingerprint confirmation and is shown as CUSTOM everywhere; an
    untrusted manifest is refused before the image is downloaded. Provisioning reuses the device's validators (no duplicate rules); the
    owner sheet never contains the trust key or the home Wi-Fi password.
99. **Packaging / LGPL:** PyInstaller one-folder build (Qt libraries as separate, replaceable files; no one-file bundle); the package ships the
    LGPL-3.0/GPL texts itself because the PySide6/shiboken6 6.12 wheels contain none, plus third-party notices with a relinking note, source
    links and a 3-year source offer; the About dialog shows the LGPL notice and "About Qt". Linux package built and smoke-tested in Docker;
    Windows (Inno Setup, UAC manifest) and macOS (.app, notarisation) prepared only. No signing keys in the repository.

## Implementation decisions of package 6 - Buildroot development images (reversible; 2026-11-19; details docs/IMAGE_BUILD.md, docs/IMAGE_LICENSING.md)
100. **Development images only:** internal images for `rpi3_64`, `rpizero2w_64` and `rpi4_64` are built in Docker (`scripts/os_build.sh`) and
    marked as development builds everywhere (os-release `VARIANT_ID=development`, `build-info.json` `hardware_validated: false`, file name
    `*-development.img`). They are never published; they carry only the public development CA. Every runtime statement about them stays
    HARDWARE-PENDING until G1/G2.
101. **Read-only root without a journal:** rootfs ext4 with 4 KiB blocks and no journal, mounted `ro` (fstab and bootargs); `/var` is an
    overlay over tmpfs; persistent state is bind-mounted from `/data`; the kernel lives inside each A/B slot. Stock `wpa_supplicant.service`
    and `avahi-dnsconfd.service` are masked (`hifi-netd` owns the network, DECISIONS #83ff.).
102. **Reproducibility:** fixed ext4 UUIDs and hash seeds, `SOURCE_DATE_EPOCH` stamps on boot inputs, `mkfs.vfat --invariant`, data
    `fs-timestamp`. A clean build with and one without ccache differ only in 4 files (ccache rewrites `__FILE__` include paths), so
    **release builds are made without ccache** (`HIFI_OS_CCACHE=0`). The RAUC verity salt and signature time differ by design.
103. **Firmware licence texts on the card:** every image carries `licenses/` on the boot FAT (Raspberry Pi boot firmware, Broadcom, Cypress,
    Synaptics Wi-Fi and the Synaptics End-User Software Tool License for the Zero 2 W Bluetooth patch, which the upstream package does not
    ship); the build fails if one is missing. Firmware is shipped unmodified and only for Raspberry Pi hardware.
104. **Image gate:** `scripts/os_check_image.sh` (89 checks: layout, alignment, services, RO root, RAUC keyring, firewall, dev marking) and
    `os/tools/secret_scan.py` (rootfs, boot FAT and raw card) must report 0 FAIL / 0 findings before an image leaves the build host;
    `scripts/os_check_ota.sh` proves the image keyring accepts the development bundle and refuses a foreign-signed one.

## Implementation decisions of package 7 - final QA fixes (reversible; 2026-11-19; review reports/REVIEW_PHASE2B_NEW_CODE.md)
105. **Network settings before the wizard (N-1):** `/settings/network*` and `/api/network` are reachable while setup is incomplete (login,
    CSRF and origin checks unchanged; every other page still redirects to the wizard), so hotspot-only onboarding and fixing a mistyped Wi-Fi
    password from the card work; the wizard's Lyrion step links to them and wizard pages show the Keep/Undo banner of a pending change.
106. **Bridge/agent consistency during network trials (N-2/N-3):** a generation counter discards status answers computed before a save
    finished; a request that reached the agent without an answer keeps the pending file and the next status decides by trial id; the agent
    answers status within 0.25 s with its last consistent state (marked `busy`) instead of waiting behind slow system actions.
107. **Snapshot restore in the boot step (N-4):** an owner-requested restore runs in `hifi-bridge-boot.service` before `hifi-netd` reads
    `network.json` and the host name; it is all-or-nothing, never raises, records `restore_failed` on error and never causes a restart loop.
    The boot unit drops `ProcSubset=pid` to read `rauc.slot=` (exposure unchanged at 0.6).
108. **The update manager never stays busy (N-5/N-10):** any monitor failure for 120 s ends as `install_interrupted`; verify/upload errors
    end as `verify_failed`/`upload_failed`; a restart that did not happen within 300 s (provisional) returns to the previous phase with
    guidance and offers the restart again.
109. **Diagnostics privacy (N-12):** the diagnostics bundle contains only the number of scanned Wi-Fi networks, never neighbours' names.
110. **Backup KDF bounds (N-9):** scrypt parameters are accepted only within OpenSSL's memory formula (128·r·(N+p+2) ≤ 64 MiB, N < 2^(16r));
    anything else is "invalid file".
111. **Two U-Boot environment copies (N-6):** `CONFIG_ENV_REDUNDANT` with `uboot.env` + `uboot-redund.env` on the boot FAT and a two-line
    `fw_env.config`; the card ships both copies (default environment + fresh-card slot state `BOOT_ORDER=A B`, `hifi_env=1`); `boot.cmd`
    fails safe when `hifi_env` is missing (A then B, 3 attempts each, `hifi.bootenv=reset` on the kernel command line); `panic=10`.
    Power-cut behaviour is HARDWARE-PENDING (gate P3-5).
112. **Root-owned boot FAT (N-14):** `/boot` is mounted `uid=0,gid=991,fmask=0117,dmask=0007`; the member-less group `hifi-boot` is joined only
    by `hifi-bridge-boot.service` (drop-in). Replaces the sandbox-only protection of #64's design.
113. **Lyrion discovery port (N-8):** discovery binds the fixed local port 3484, accepts answers only from source port 3483, and the firewall
    accepts only `udp sport 3483 udp dport 3484`.
114. **systemd-resolved (extends #89):** stays the DNS client; `LLMNR=no` and `MulticastDNS=no` (Avahi owns mDNS).
115. **growdata (N-13):** no guard file; it runs on every boot and is idempotent.
116. **mDNS test network:** Docker's rules for `internal` networks drop container-to-container multicast (Engine 29.1.3), so the mDNS suite's
    own network is not internal but has `inhibit_ipv4` and no masquerading (no host address, no route out; checked at runtime and by
    `tests/entry/test_compose_isolation.py`, which allows exactly this network and `NET_ADMIN` only for `mdns-nogw`).
117. **PipeWire test harness:** `start_pipewire.sh` pre-creates `$XDG_RUNTIME_DIR/pulse` (start-up race with the readiness poll); on PipeWire
    1.x the tests remove a source node with `pw-cli destroy` instead of `pactl unload-module`, which crashes pipewire-pulse 1.4.2 (SIGSEGV)
    while streams reconnect. Harness only; the product never unloads modules or runs pipewire-pulse.
118. **Demo network/update pages:** the demo runs the real network agent on a simulated Wi-Fi radio and a simulated RAUC; the product pages
    are unchanged, the SIMULATED labelling is on the demo console and in docs/DEMO*.md.
119. **Login back-off bound:** the rate limiter's exponential back-off exponent is capped (an unbounded `2 ** n` overflowed after ~1,030
    failures and let an attacker bypass the back-off).

## Owner decisions of 2026-11-20 - phase 2b final polish (after the ChatGPT review of the phase-2b handoff)
120. **Partition layout (resolves OPEN_QUESTIONS #76):** boot FAT 64 MiB, rootfs A 384 MiB, rootfs B 384 MiB, data partition as small as its
     measured needs allow and grown to the rest of the card on first boot. Technical target: 2 GB cards; practical recommendation: a 16 GB
     microSD; no artificial 16 GB minimum. The 448 MiB data figure of the size plan is not binding.
121. **Persistence rule (binding design rule): persist only what must survive a reboot; everything else belongs in RAM.** Persistent:
     administrator credentials, device certificates and keys, Wi-Fi/network/LMS/MQTT configuration, Bluetooth bonds, the OTA/rollback state
     that is required, boot and recovery metadata. Logs and diagnostics only in bounded RAM buffers (gone after a reboot); an explicitly
     configured external syslog server stays possible. Update installation may keep temporary files on the card while it runs and must clean
     them up reliably; power-loss safety and rollback take precedence over avoiding necessary writes. No audio recording, no playback
     history, no unbounded log files. Checked automatically.
122. **Wizard network step (resolves OPEN_QUESTIONS #78):** the setup wizard gets its own network step so a device can be set up entirely
     over its individual hotspot (choose Wi-Fi, enter credentials, test, explain the network change, handle the lost connection and
     rediscovery, roll back failures with the existing recovery logic). Ethernet users are not led through Wi-Fi steps. The existing network
     state machine is reused.
123. **U-Boot environment (OPEN_QUESTIONS #80/#81):** evaluate libubootenv with a complete image default environment so that two damaged
     copies never produce a generic, unbootable default. Keep the redundant environment; no switch to raw MMC sectors without real-hardware
     validation; never claim U-Boot works on real hardware.
124. **MQTT state (resolves OPEN_QUESTIONS #50):** in the private home network the connected phone's name, the title and the artist are
     visible and published to Home Assistant by default - explicitly wanted, no extra privacy switch for them. On disconnect / end of
     playback the current state is reset correctly; the bridge keeps no playback history (Home Assistant's own history is not the bridge's
     concern).
125. **Firmware licences (OPEN_QUESTIONS #73-#75):** ship only the firmware files each board needs, where that carries no compatibility risk;
     include the licence texts completely; prepare source provision for a later public release; document the export-control and Synaptics
     risks. No publication and no claim of a binding legal clearance.
126. **Code signing (OPEN_QUESTIONS #69, macOS part):** macOS builds, Developer ID signing and notarisation will run on GitHub Actions with the
     owner's existing Apple Developer membership (not Xcode Cloud). Prepare the pipeline and a setup guide; no real certificates, secrets or
     releases yet. The offline RAUC release key is unaffected. Windows code signing stays open.
