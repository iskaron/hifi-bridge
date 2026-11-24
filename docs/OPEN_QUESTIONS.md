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

## New questions from phase 2a (2026-11-15)
19. **Product name and trademarks:** may the product/UI use the word "Bluetooth" (Bluetooth SIG trademark rules)? The UI shows "Bluetooth Audio Bridge"
    and the tagline "Bluetooth to Lyrion"; the logo deliberately avoids the Bluetooth mark. **Owner/legal decision.**
20. **HTTPS for the WebUI:** TLS works with a supplied certificate; how the appliance gets one (self-signed + fingerprint shown by the flash tool,
    local CA, or plain HTTP on trusted LANs) is a phase-2b decision.
21. **PBKDF2 cost on a Pi Zero 2 W:** 200k iterations take ~0.1 s on x86; must be measured on the board (login latency vs. brute-force cost).
22. **MQTT command trust:** commands (pairing, bridge audio, test tone) are accepted from anyone allowed by the broker's ACL. Acceptable, or should
    MQTT control be read-only by default? Current choice: commands on (they cannot touch foreign music), documented.
23. **Real Home Assistant import** of the discovery payloads is not tested (only the documented schema keys and a real broker). A Docker HA test is
    possible later if wanted.
24. **WirePlumber without systemd-logind** (likely in a Buildroot image): 0.4 needs `with-logind = false` or it quits; 0.5 needs
    `monitor.bluez.seat-monitoring = disabled`. Which init system/session setup does phase 2b use?
25. **pipewire-pulse in the image:** `parec` (default, verified on 0.3.65 and 1.4.2) needs pipewire-pulse; without it, `pw-cat --raw` (PipeWire >= 1.x) is the alternative.
26. A rare (1 in ~10 runs) disappearance of pipewire-pulse 1.4.2 during module unload was seen once in the lab; not reproduced in 4 further runs.
    The capture supervisor retries, but a process supervisor must restart pipewire-pulse on the device.

## Status after the phase-2a review and follow-up package (2026-11-15)
* #19 trademark: **decided provisionally** - product name "Hi-Fi Bridge", "Bluetooth" only descriptive, no Bluetooth logo (DECISIONS #34);
  trademark/qualification questions remain open **before any public release**.
* #20 HTTPS: **implemented** (docs/LOCAL_ACCESS_TLS.md); fingerprint display by the flash tool remains phase 2b.
* #22 MQTT commands: **decided** - read-only default, separately enabled controls (DECISIONS #36).
* #23 real Home Assistant: **done** - 7/7 tests against HA 2026.11.1.

## New questions (follow-up package)
27. **Development host: Docker's data filesystem is full** (`/dockerfs/daemon-data`, 100 %). Non-root containers (LMS, and very likely the owner's
    other services such as Postgres) fail to write; fresh `apt` inside Debian 12 images fails (truncated downloads look like "invalid signature").
    The lab LMS was moved to the host filesystem. **Owner action needed:** free space; my request to delete my own dangling images was denied.
28. **HSTS** for the WebUI: off for now (a broken local certificate + HSTS could lock the owner out). Enable after field experience?
29. **openssl CLI on the appliance** is needed for certificate management (the stdlib cannot create certificates). Confirm in the Buildroot
    package set, or switch to a small dependency later.
30. **mDNS: own responder vs. avahi** in the image. The own responder is tested against Avahi and has no dependencies; avahi has per-interface
    answers, IPv6 and broader field experience. Decide with the 2b init/network design (`--no-mdns` exists).
31. **Orphaned Home Assistant entities** of earlier instances (other instance id, e.g. after a factory reset) stay retained at the broker; the
    bridge can only clean its own topics. Offer a "remove from Home Assistant" action before reset?
32. **Lab images on Debian 12**: currently cannot be rebuilt (see #27); consider moving lab images to Debian 13 once rebuilding is possible.
33. **Accessibility:** automated (axe-core, keyboard-only journey, accessibility tree) is green; a manual screen-reader pass (VoiceOver/TalkBack)
    on real phones is still needed.

## Stabilisation package (2026-11-16)
34. **Stuck digital silence on capture:** the no-data watchdog restarts a capture child that delivers *nothing*, but not one that delivers
    only silence. Should the bridge restart capture after N s of digital silence while AVRCP reports "playing"? Decide from real traces
    (hardware A3/A4, C1/C2) - a quiet passage must not trigger it (track bab_quiet_passage).

## Status after the stabilisation package (2026-11-16)
* #28 HSTS: **decided** - off by default (DECISIONS #48); regression-tested.
* #29 openssl on the appliance: **checked** - Buildroot 2026.02 libopenssl 3.6.2 (Apache-2.0) + `BR2_PACKAGE_LIBOPENSSL_BIN`; allowed (DECISIONS #50).
* #30 mDNS: **decided** - Avahi preferred (DECISIONS #49); Avahi backend implemented and interop-tested; built-in responder as fallback.
* #24/#25 PipeWire in the image: **researched** - Buildroot 2026.02 ships PipeWire 1.2.8 / WirePlumber 0.5.10 and **no `parec`** without the
  pulseaudio package -> automatic capture tool choice picks `pw-cat --raw`. Init system/logind remain a 2b decision (docs/PHASE2B_PLAN.md).
* #32 lab images: **resolved** - all images rebuilt from scratch on 2026-11-16 (scripts/build_images.sh).
* #27 Docker disk: **resolved by the owner** (102 GB free on 2026-11-16).

## Added by the phase-2b preparation package (2026-11-16)
35. ~~brcmfmac firmware licence~~ **largely resolved by package 6** (docs/IMAGE_LICENSING.md, read from the hash-verified archives): the
    Raspberry Pi boot firmware and the Broadcom/Cypress/Synaptics radio firmware may be redistributed publicly **unmodified, with their
    licence texts, only for Raspberry Pi hardware** (DECISIONS #103). Remaining owner items: #73-#75.
36. **Firmware/DTB updates:** the boot partition (firmware, DTB, overlays, U-Boot) is shared by both slots and not A/B. Freeze it per release
    line, or design a two-phase update? (PHASE2B_PLAN §6)
37. **vfat in-place overwrite:** scrubbing assumes vfat keeps the clusters on a same-length in-place write. Verify on a Pi (G2 P6-3).
38. **Release CA:** who creates it, on which hardware token, and the rehearsal (G4 K-1). Until it exists, "official" trust cannot be built.
39. **Factory reset and Bluetooth pairings:** should a factory reset also remove `/var/lib/bluetooth`? Today it removes only the bridge's own
    files (G2 P8-2).
40. **Revocation:** no CRL; a compromised key is handled by shipping a new keyring. Is that enough for v1? (UPDATE_TRUST §5)
41. ~~Project LICENSE file~~ **resolved by DECISIONS #72** (official AGPL-3.0 text; `HIFI_BRIDGE_LICENSE_FILES` set).

## Added with the central Git server and the GitHub migration (2026-11-17)
42. ~~CI on the GitLab server~~ **resolved by DECISIONS #70:** GitHub Actions is the CI; the existing workflow runs there.
43. **Source availability for AGPL/GPL:** the WebUI now links the repository in every footer (DECISIONS #72); this is sufficient for the
    bridge's own code once the repository is public. Still open before a public image: source tarballs per GitHub Release (so the offer
    matches the exact running version) and the GPL/LGPL written offer for the image's components (G4 L-1).
44. **Actions minutes of a private repository** (moot once the repository is public: standard GitHub-hosted runners are free): one full CI run uses about **1 runner-hour** (measured on the first GitHub run:
    13 jobs, ~54 job-minutes, ~60 billed minutes, ~22 min wall time; integration and coverage take ~18-20 min each, see
    docs/REPOSITORY.md §3). Documentation-only pushes are skipped. If the plan's monthly minutes are not enough: run the heavy
    suites (Home Assistant, browser, coverage) only on `main`, pull requests and manual dispatch? Owner decision.
45. **Update check while the repository is private:** devices fetch official bundles from GitHub Releases without credentials. A private
    repository's releases are not reachable anonymously, and a device must never carry a GitHub token. The update check therefore works
    only once the releases are public (G4). Alternative: a public release-only repository. Owner decision before G4.

## Added by the demo and public-readiness package (2026-11-17)
46. **Private information in the immutable Git history** (found by the publication check; agents must not rewrite history, #69): the owner's
    name and e-mail (commit identity and docs, by design), the private GitLab domain `git.cativa.net` (8 files and commits since #69), the
    local user name in one old report path, and 46 commit messages with Claude session links. None is a credential (Gitleaks clean). Accept
    as is, or the owner rewrites history before publishing (force-push to a fresh repository). Owner decision before the visibility change.
47. **GitHub settings to switch on when going public** (owner, repository settings): private vulnerability reporting (SECURITY.md relies
    on it), "require approval for workflows from outside collaborators", branch protection for `main` (and `develop`), Dependabot alerts,
    secret scanning with push protection.
48. ~~**Unpinned Lyrion image:**~~ **resolved by DECISIONS #80** (9.1.2 + digest). lab and demo use `lmscommunity/lyrionmusicserver:stable`; pin a version (e.g. the tested 9.1.x tag) for
    reproducible CI once the tag naming is confirmed.
49. ~~**Demo volume:**~~ **resolved by DECISIONS #81** (rooms at 75). Lyrion starts new players at volume 50, which is quiet in the audio monitor; the monitor has its own listening level.
    Should the demo preset the rooms higher? (cosmetic)

## Added by package 1 - review fixes (2026-11-18)
50. ~~**Privacy of MQTT state:**~~ **resolved by DECISIONS #124** (phone name, title and artist stay published by default, no extra switch;
    the bridge clears them on disconnect and on a clean stop, keeps no playback history). Original question: the bridge publishes the connected phone's name and the track title as **retained** MQTT state, readable by
    every broker subscriber and stored in Home Assistant's history. Keep, make it an opt-in setting, or publish without retain? (Logs,
    syslog and the diagnostics bundle now use salted pseudonyms `dev-<8 hex>` for MAC addresses and leave phone names out.)
51. LMS player IDs look like MAC addresses and are now pseudonymised in logs as well. Should player IDs stay in clear for debugging?
52. The new global login-failure budget (50 failures, then up to 60 s delay) can slow the owner during a distributed guessing attack.
    Accept or tune?
53. A player that reads nothing for 60 s is dropped by the stream server; a pause at LMS longer than 60 s therefore starts a fresh bridge
    session on resume. Validate on hardware (E2).
54. Capture restart back-off (4 fast retries, then doubling up to 5 s) - measure against the real PipeWire BlueZ node on the Pi (A3/A4).
55. A device CA regenerated after a torn write (power cut during first boot) has a new fingerprint, so the owner must trust it again.
    Accept, or keep a backup copy of the CA?
56. An unreadable configuration now makes the service fail at start (systemd restarts it; the log names the reason) instead of silently
    resetting the device. Should the reason also go into `hifi-status.txt` on the boot partition?

## Added by package 2 - demo and entry points (2026-11-18)
57. **Image pin updates:** enable Dependabot for the Docker ecosystem (proposes digest bumps as pull requests), or keep manual updates via
    `scripts/image_pins.sh`? Owner decision together with #47.
58. Engine 29 with Compose 2.21-2.23 was not tried; the stated Engine-29 minimum (2.24.5) is the lowest release tested there.

## Added by package 3 - network management (2026-11-18)
59. **Fallback NTP server:** keep `pool.ntp.org` after the DHCP-provided servers, use none, or make it an owner setting?
60. **Devices without internet never sync time:** certificate validity is judged by the device clock, so browsers may see an expired
    certificate after ~13 months. Options: a time hint from the owner's browser or from Lyrion, or manual renewal.
61. Do phones need a "stay connected without internet" hint on the hotspot page? Depends on hardware test N-5.
62. More than one saved client Wi-Fi network in V1 (today one, DECISIONS #88)?
63. Should `hifi-bridge.service` declare `Wants=hifi-netd.service` (today independent; the WebUI shows the agent as unavailable)?

## Added by package 4 - OTA update management (2026-11-19)
64. A health failure on an **established** slot currently leads to a rollback via U-Boot's boot-count after three boots. Keep, or never
    roll back an established slot automatically?
65. Should a factory reset also remove RAUC's slot status (`slots.json`)?
66. Payload integrity of an uploaded bundle is checked only at install time by dm-verity (RAUC's inspection accepts a payload byte changed
    after signing). Acceptable, or additionally hash the payload against a signed checksum before staging?
67. One large upload occupies one of the 64 WebUI connection slots for its duration. Acceptable for V1?

## Added by package 5 - flash tool (2026-11-19)
68. **Reverse-DNS namespace** for the polkit action and app IDs: currently `io.github.iskaron.hifibridge` (action
    `io.github.iskaron.hifibridge.flash.write`). Keep, or a project domain?
69. **Code-signing certificates** - **macOS resolved by DECISIONS #126** (GitHub Actions with the owner's Developer ID; pipeline and guide
    prepared, docs/MACOS_SIGNING.md; certificates and secrets are the owner's step). **Windows (Authenticode) still open.** Original question: for Windows (Authenticode) and macOS (Developer ID + notarisation): who obtains them and at what cost? Without
    them Windows SmartScreen and macOS Gatekeeper warn or block.
70. Should the elevated helper also verify the manifest **signature** itself? Today it checks the image hash against the manifest the GUI
    verified (the image is the user's choice either way).
71. Installing the device CA on the owner's computer: the tool saves the verified certificate and shows the fingerprint; there is no
    automatic installation into OS or browser trust stores. Enough for V1?
72. Is an AppImage wanted for Linux? It conflicts with polkit's exact-executable-path rule (today: tarball + `install.sh`).

## Added by package 6 - Buildroot development images (2026-11-19)
73. **Export control:** the Broadcom licence (§7.1) names embargoed destinations. Does a public download page need a notice or restriction?
    *P2 (2026-11-20):* clause and the unchanged exposure after per-board pruning documented (docs/IMAGE_LICENSING.md 7); still the owner's /
    legal review before any public download (gate L-3). Open.
74. **Synaptics Bluetooth licence (Zero 2 W `SYN43430*.hcd`)** can be terminated by Synaptics by written notice. Accept, or prune the firmware
    per board so the Pi 3 B+/Pi 4 images do not carry it (and document the Zero 2 W risk)?
    *Partly resolved by DECISIONS #125/#134 (P2):* pruned per board - the Pi 3 B+ and Pi 4 images carry no Synaptics Bluetooth file (also not
    the byte-identical `BCM43430B0.hcd`) and not the tool licence; the Zero 2 W risk is documented (docs/IMAGE_LICENSING.md 7). Still open:
    whether the Zero 2 W stays a supported board under a revocable licence (owner).
75. **Release source obligations:** the prebuilt DT overlays come from raspberrypi/linux `957fc9f9`, not our kernel commit `21b41014`;
    `make legal-info` does not save the Buildroot source itself and lacks the Bootlin toolchain licence files. All three must be added to a
    release's source bundle (G4 L-1).
    *Prepared by DECISIONS #135 (P2):* `scripts/os_source_bundle.sh` assembles all three plus legal-info and the repository with a hashed
    manifest (run once for rpi3_64, docs/IMAGE_BUILD.md 10). Open: hosting / written offer (#96) and mirroring Bootlin's sources.
76. ~~**Partition sizes (cannot change after the first flash)**~~ **resolved by DECISIONS #120** (owner, 2026-11-20): boot 64 MiB, slots
    384 MiB, data 256 MiB before growth (measured minimum, docs/PERSISTENCE.md 2.1), 2 GB target card, 16 GB recommended. Implemented in
    the final polish package P1 (`os/storage.json`, genimage, defconfigs, image check `size.*`, flash tool `MIN_BYTES`).
77. ~~machine-id on a read-only `/etc`~~ **fixed** (networkd drop-in `DUIDType=link-layer`, image check `network.dhcp-duid-link-layer`; in
    images built after 2026-11-19, the package-6 evidence images predate it). Original finding: `/etc/machine-id` is empty, so each boot gets a transient id; networkd derives its DHCP DUID from
    it, so DHCPv6 leases (and DHCPv4 with a DUID-based client id) may change on every boot. Open only: whether other users of the
    machine id (journal directory names, D-Bus) need a persistent id under `/data`.

## Added by package 7 - final QA (2026-11-19)
78. ~~**Wizard network step:** network settings are reachable before setup via a link (DECISIONS #105). Is a dedicated network step in the
    setup wizard wanted instead?~~ **resolved by DECISIONS #122 (owner), implemented in P3 (#137-#140, docs/NETWORK.md 12).**
79. Show a boot with a reset U-Boot environment (`hifi.bootenv=reset`) on the update page?
80. ~~Switch the image to libubootenv with `/etc/u-boot-initial-env` as its default~~ **resolved by DECISIONS #123/#133 (P2):** libubootenv
    0.3.7 + one patch, `/etc/u-boot-initial-env` = U-Boot's default + fail-safe slot state. Evidence (SIMULATED, image check of all three
    boards and tests/os): 13 corruption cases on the image's own copies; both copies bad -> `bootcmd=bootflow scan`, fail-safe boot; the
    previous images' uboot-tools 2025.10 stored `bootcmd=bootp; ... root=/dev/nfs ...` in that case and did not return on a short copy.
    Real U-Boot/FAT behaviour stays HARDWARE-PENDING (gate P3-1, P3-12, P3-13). Original question: switch the image to libubootenv with
    `/etc/u-boot-initial-env` as its default, so a Linux-side write with both env copies bad cannot produce uboot-tools' generic default
    (`bootcmd=bootp`, an unbootable card)?
81. Store the U-Boot environment in raw MMC sectors in the 4 MiB gap instead of FAT files (no FAT writes on every boot)?
    *Kept open (DECISIONS #123, P2):* raw sectors would avoid the FAT driver (directory/FAT sector updates on every boot and the short-file
    case of docs/OTA.md 4.1) and the boot FAT writes, but they need `CONFIG_ENV_IS_IN_MMC` + offsets agreed between U-Boot, `fw_env.config`,
    genimage (the gap must never be overwritten by the flash tool or a repartition) and the SD card's own behaviour on small in-place
    writes - only real cards and real power cuts can show whether that is safer. Decide after G2 P3-5/P3-6/P3-13 on the FAT variant.
82. The saved environment pins `serial#`, `usbethaddr`, `fdtfile` of the first Pi the card booted in. Reset them when a card moves boards?
83. Can removing a real BlueZ node under capture upset PipeWire 1.2.8 the way `unload-module` crashes pipewire-pulse 1.4.2? (hardware A3/A4)
84. If pipewire-pulse is ever shipped on the appliance, should it be supervised/restarted? (today not shipped; OPEN_QUESTIONS #26 moot)
85. Root cause of Docker dropping multicast on internal networks (host firewall not inspected); report upstream?
86. ~~**Lab flake `test_end_to_end_bit_exact[wav]` as the first test after a lab recreate**~~ **resolved (P4, 2026-11-21, harness;
    DECISIONS #141, evidence `reports/evidence/flake86_investigation.md`).** Cause, reproduced and confirmed by experiment: Lyrion
    resumed a stale queue item (an old bridge URL in `.lab/lms-config/prefs/clientplaylist_<mac>.m3u` with `playingAtPowerOff: 1`, left by
    the player-outage scenario) with a 1 s fade-in when the player connected to the recreated Lyrion; the URL could not be streamed, and
    Lyrion keeps a requested fade-in until a stream really starts, so the first test's stream faded in (`strm s ... transition type: 2`)
    and could not be aligned. Cold labs before the fix: stale state 6/6 failed, fresh 5/5 and stale-without-`playingAtPowerOff` 3/3 passed;
    warm lab with an injected unreachable play request: with fade-in 3/3 failed, without 3/3 passed. The same fade-in is in the logs of the
    two package-2/3 full-run failures of 2026-11-18 (gain 65536 there too); the `c76c541` volume cause was real but a second one. Fix:
    `Lab.settle_players()` starts and stops one stream per player before the first test and verifies that the next start has no
    transition (after the fix, cold labs: stale 6/6 and fresh 6/6 passed; three full suites from cold labs 56/56 each). Product impact: see #99. Original observation (final run 2026-11-20): 0 aligned
    blocks, `first_stored_frame` 250, gain 65536, a stale bridge URL resumed on reconnect; 3/3 green warm, green in CI (fresh state).

## Added by the final polish package (2026-11-20)
87. **Staged update bundle across a reboot** (P1, docs/PERSISTENCE.md 5): a verified bundle waiting for the owner's "install" (phase
    `staged`, up to ~192 MiB) and the bundle of an installation cut by a power cut are kept across a reboot, so the owner can install or
    retry without uploading again; every other staging file is removed at boot. Should a staged-but-not-installed bundle rather be dropped
    at boot (stricter reading of DECISIONS #121, the owner uploads again)?
88. **External syslog for the whole system:** the bridge forwards its own log over TLS syslog (bounded in-memory queue, never spooled).
    The journal of BlueZ, PipeWire, RAUC and the kernel is not forwarded. A journald forwarder (`systemd-journal-upload`, or an
    rsyslog/syslog-ng client reading the journal) would do it without writing to the card; not built (no new V1 feature). Wanted?
89. ~~**PRODUCT_SPEC wording:**~~ **resolved:** the owner's rule #121 (logs and diagnostics only in RAM) supersedes the old line; PRODUCT_SPEC
    updated with a reference to #121. Original: PRODUCT_SPEC ("mostly RAM logs with persistent critical fault history") predates DECISIONS #121; the
    fault history is now RAM-only (kept across a service restart in `/run/hifi-bridge`, lost on reboot; DECISIONS #127). Update the
    PRODUCT_SPEC line accordingly?
90. **Write volume per update and per flash:** RAUC writes the whole 384 MiB slot image (it is not known whether RAUC 1.13 skips the
    empty blocks of an ext4 image on a block device - check on hardware, gate P3); the flash tool writes and reads back all 1092 MiB
    of the card image, including the empty slot B (correct and simple; skipping zero areas would need a discard plus a zero read-back).
91. **BlueZ name cache:** `/var/lib/bluetooth/<adapter>/cache/` (persistent, BlueZ-managed) gets an entry for every phone that connected,
    paired or not; small (a few hundred bytes each) but not bounded by the bridge. Prune entries of unpaired devices at boot?
92. **macOS helper signature check** (DECISIONS #126, docs/MACOS_SIGNING.md 5): should the GUI verify the bundled helper's code signature
    (same Team ID, `codesign --verify -R`) before elevating it, and refuse to run from a user-writable location outside App Translocation?
    Today the helper is protected by the bundle seal, Gatekeeper and the admin prompt only.
93. **libubootenv patch upstream:** our patch (a short/unreadable redundant copy is treated as invalid, DECISIONS #133) fixes a real data-loss
    path in stock libubootenv 0.3.7 (upstream master unchanged). Submitting it upstream is a publication - may the patch be sent to the
    libubootenv maintainers (author identity as in the patch)?
94. **Boards outside the target list in the images:** the upstream defconfigs also build DTBs for the Pi 3 B and CM3 (rpi3_64) and the
    Pi 400, CM4 and CM4S (rpi4_64), so their radio firmware stays (DECISIONS #134; for the Pi 4 image this is the only Synaptics-licensed
    file: `brcmfmac43456`). Drop those DTBs and their firmware (only Pi 3 B+, Pi 4 B, Zero 2 W boot), or keep them as unsupported but working?
95. **Prebuilt DT overlays:** keep the 358 `overlays/*.dtbo` of rpi-firmware (their source is raspberrypi/linux `957fc9f9`, ~250 MB extra per
    source bundle), build them from our kernel tree, or drop them (our `config.txt` uses none; a HAT EEPROM could request one)?
96. **Hosting of the source bundles:** a legal-info source bundle is ~1 GB per board and release. Publish it next to each release (GitHub
    Release assets, separate storage) or give a written offer (OPEN_QUESTIONS #43)? Who keeps the Bootlin toolchain sources mirrored?
97. **Finding the device after the hotspot (P3, DECISIONS #137):** the network step tells the owner to open `http://<name>.local/` or the
    address the router lists. Some phone browsers (notably older Android versions) do not resolve `.local` names, and the device's new DHCP
    address is not known before the switch. Is the router's device list acceptable as the fallback, or should the hotspot page later
    show the address the device had on the home network (it learns it only after joining - today shown after an unconfirmed trial)? Real
    phones: hardware items N-2/N-5. A QR code with the address was not added (no existing page has one; no new V1 feature).
98. **The 3-minute confirmation window from the hotspot (P3):** it starts when the agent applies the change and includes the association
    (seconds), the owner switching the phone's Wi-Fi, opening the address and logging in again (cookies are per host). Enough for an owner
    on a phone, or should a trial started from the setup hotspot get a longer window (a reversible constant, `netmanager.TRIAL_S`)?
    Not changed without a real-phone measurement.

## Added by the final polish package P4 - lab flake #86 (2026-11-21)
99. **Lyrion keeps the released bridge item in its persisted queue (real installations too):** the bridge releases with
    `playlist deleteitem <url>` (URL-addressed, DECISIONS/FINDINGS 3); Lyrion 9.1.2 removes the item from the live queue but does not
    rewrite `prefs/clientplaylist_<mac>.m3u` for that command (it is not in Lyrion's list of queue-saving commands; observed in the lab
    after a fresh start and one bridge session). After a Lyrion restart the player's queue therefore shows the old "Bluetooth" item again,
    and if the player had been disconnected or switched off while the bridge played (`playingAtPowerOff`), Lyrion tries to resume it on
    reconnect with a 1 s fade-in. With a running bridge the stream server rejects the old URL (INTEGRATION, warm lab, 3/3: old session
    -> 410, `stale_rejected` +1, no audio served; an old token would get 403), the request fails before streaming, Lyrion keeps the
    fade-in pending, and the bridge's next stream on that player started with `transition type: 2` (3/3): the guest's first second
    fades in (FINDINGS 13; no latency change measured: first output 1.51-1.62 s either way). Harmless for audio (no data lost), cosmetic in Lyrion's UI. Options: (a) accept and document (current state);
    (b) after a release that left the queue empty, also send a queue-saving command - but `playlist clear` is not URL-addressed and could
    remove an item someone added in the same instant (ownership rule), so not done; (c) report upstream (adding `deleteitem` to the
    queue-saving commands is a one-line Lyrion change) - a publication, needs the owner. Real players/Lyrion installations: hardware gate.

## Added by the G1 PCM-ingress repair (2026-11-24)
100. **Transport acquisition in the input role (hardware):** with `bluez5.media-source-role = input` nothing consumes the phone's node
     until the bridge's capture starts on `pending`; PipeWire then acquires the transport on node start (`media-source.c` `do_start`).
     Whether every phone (Android/iPhone) tolerates that delay, and whether BlueZ ever times a `pending` transport out before it is
     acquired, can only be seen on hardware (G1-R). If it fails: keep a capture consumer attached for the whole connection instead.
101. **The image's PipeWire instance for the bridge user:** the image runs PipeWire/WirePlumber system-wide (`pipewire.service`), and
     the bridge (user `hifi-bridge`) must reach its socket for `pw-dump`/`pw-cat`; the role rule and the 44.1 kHz clock are now in the
     image. Access rights and the system-wide vs. per-user choice remain OPEN_QUESTIONS #24/#25 until the image boots (G2).
102. **Authenticated pairing with numeric comparison** (owner idea, G1 day): the WebUI would show the 6-digit code to the owner. Not
     approved and not built; the agent capability parameter now reaches BlueZ, so a later `DisplayYesNo` agent is a contained change.
103. ~~**Lab: integration tests lose audio after days of lab uptime**~~ **fixed (harness, 2026-11-24):** four radio/guest-flow tests failed
     ("audible_fraction 0.78-0.94"), also on the unchanged code of `b648936`. Cause, shown by experiment: `btkit.analyze.load()` parsed
     the player's whole capture history (281 MB PCM + 43 MB index after three days: 3.07 s holding the GIL), so the synthetic radio served
     by a thread of the same test process fell behind real time and the player went silent (threads stalled up to 0.44 s, radio silent
     for 4 s); the tests evaluate `lab.capture()` before `time.time()`, so the window contained that silence. Fix: `load()` reads only the
     requested window (binary search in the time-ordered index, a ranged read of `pcm.raw`); equal to the full parse
     (`tests/unit/test_capture_load.py`), milliseconds for a window of a 3-hour history; the four tests then passed 3 x 3 against the
     same, still growing capture (304 MB). The capture volume itself still grows until `scripts/lab_down.sh`; a size cap was not added.
