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
35. **brcmfmac firmware licence:** Buildroot labels `brcmfmac_sdio-firmware-rpi` PROPRIETARY. Its LICENSE must be read before any public
    image (G4 L-1). Without it there is no on-board Wi-Fi/Bluetooth.
36. **Firmware/DTB updates:** the boot partition (firmware, DTB, overlays, U-Boot) is shared by both slots and not A/B. Freeze it per release
    line, or design a two-phase update? (PHASE2B_PLAN §6)
37. **vfat in-place overwrite:** scrubbing assumes vfat keeps the clusters on a same-length in-place write. Verify on a Pi (G2 P6-3).
38. **Release CA:** who creates it, on which hardware token, and the rehearsal (G4 K-1). Until it exists, "official" trust cannot be built.
39. **Factory reset and Bluetooth pairings:** should a factory reset also remove `/var/lib/bluetooth`? Today it removes only the bridge's own
    files (G2 P8-2).
40. **Revocation:** no CRL; a compromised key is handled by shipping a new keyring. Is that enough for v1? (UPDATE_TRUST §5)
41. **Project LICENSE file** (AGPL-3.0-only intent) is still missing; needed for `legal-info` and any publication.
