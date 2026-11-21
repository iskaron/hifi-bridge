# HANDOFF_FOR_CHATGPT - Phase 2b final polish: pre-hardware readiness (2026-11-20 .. 2026-11-21)

Labels used throughout: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (**none** in this
package). Chronological log with every red analysed: `reports/PROGRESS_PHASE2B_POLISH.md`. Previous handoff (phase 2b main package):
git history of this file at `48925e8`. Owner decisions of this package: DECISIONS #120-#126; implementation decisions #127-#141; new
open questions #87-#99.

**Status: COMPLETE in software / hardware-pending.** Every item of the owner's "Final Polish & Pre-Hardware Readiness" assignment is
implemented, tested in Docker, merged into `develop` and pushed; CI is green (section 7). Nothing ran on a Raspberry Pi, a real radio, a
real phone or a real SD card. **The next gate is G1: real Bluetooth audio playback on the Raspberry Pi.** I stopped here.

Work was split into packages, one agent at a time, each merged `--no-ff` after its own tests: P1 persistence + layout (`8475549`), P2
U-Boot environment + firmware + rebuild (`23a4fa6`), P3 wizard network step (`3c7cd70`), P4 integration flake #86 (`c770f25`); P5 (MQTT
#50, macOS signing) was done by the coordinator during P1 (`8ace337`, `87d0b23`); P6 final QA (rebuild of all boards, full suites, this
handoff).

## 1. Product decisions implemented

| Owner decision | Implementation | Evidence |
|---|---|---|
| **Partition layout** (#120) | boot 64 MiB, rootfs A/B 384 MiB each, data 256 MiB grown to the card on first boot; 2 GB card technical target, 16 GB recommended, no 16 GB minimum anywhere; one source of the numbers `os/storage.json` (#132) read by genimage checks, size plan, flash tool test, image check | section 2 |
| **Persistence rule** (#121) | journald RAM-only and bounded, no core dumps, fault history in RAM (#127), boot cleanup of everything a power cut leaves (#128), no needless boot-FAT writes (#130), no Python bytecode writes, allow-list of every persistent path checked in UNIT, STATIC and BUILD | section 3 |
| **Wizard network step** (#122) | own step Owner → **Network** → Bluetooth → Lyrion → Player → Done, full setup via the individual hotspot, reuse of the existing network state machine (#137-#140) | section 4 |
| **U-Boot environment** (#123) | libubootenv 0.3.7 + our patch + complete image default environment `/etc/u-boot-initial-env` (#133); redundant FAT env kept; raw MMC sectors **not** used | section 5 |
| **MQTT state** (#124, resolves OQ #50) | phone name, title, artist published by default; a Bluetooth disconnect now also clears the title (it survived before - real bug); a clean MQTT stop publishes a cleared retained state before `offline`; no history on the bridge, no extra privacy switch. UNIT tests in `tests/unit/test_controller.py`, `tests/unit/test_mqtt.py` | `8ace337` |
| **Firmware licences** (#125) | per-board radio firmware lists `firmware/radio-<compatible>.txt` with sha256 pruning check (#134): the Pi 3 B+ image carries no Synaptics file, the Pi 4 image no Synaptics Bluetooth file (one Synaptics Wi-Fi file for the Pi 400/CM4, OQ #94), only the Zero 2 W image carries `SYN43430*.hcd` and the Synaptics tool licence; full licence texts + per-board `NOTICE.txt` on the boot FAT (build fails if one is missing); source provision prepared (`scripts/os_source_bundle.sh`, #135, **never published**); export-control and Synaptics risks documented (`docs/IMAGE_LICENSING.md` §5-§7). **No legal clearance is claimed** (hardware/legal gate item L-3) | `docs/IMAGE_LICENSING.md` |
| **Code signing** (#126, macOS part of OQ #69) | GitHub Actions workflow `flashtool-macos.yml` (manual only; build job unsigned for arm64 + x86_64; sign job opt-in, environment `macos-signing` with required reviewer, inside-out `codesign` with hardened runtime and timestamp, `notarytool` with an App Store Connect API key, staple, `.dmg`); empty entitlements with reasons; setup guide `docs/MACOS_SIGNING.md` (Developer ID Application yes, Developer ID Installer not needed for the `.dmg` design, API key, environment secrets, first-run checklist, privileged helper); 10 STATIC workflow tests. **Never run on macOS; no certificate, secret or release exists.** RAUC release key stays offline; Windows signing stays open | `docs/MACOS_SIGNING.md` |

Not changed: product behaviour outside these decisions; no new V1 feature.

## 2. Final partition layout and measured space (BUILD, all three boards, P6 rebuild on `a7ae13b`)

| Partition | Size | Measured use |
|---|---|---|
| MBR + alignment | 4 MiB | every partition on a 4 MiB boundary |
| p1 boot FAT | 64 MiB | 4.0-4.6 MiB used (firmware, DTBs, overlays, U-Boot + two env copies, `boot.scr`, licences, provisioning slot) |
| p2 rootfs A | 384 MiB | **191.5-191.9 MiB used** (155.1-155.6 MiB of files; kernel + modules 46.6-46.8 MiB) → ~192 MiB (50 %) headroom; image check fails above 80 % |
| p3 rootfs B | 384 MiB | empty; RAUC installs here |
| p4 data | 256 MiB before growth | **225.8 MiB available** to the bridge (`mke2fs -b 4096 -i 65536`); need 216 = largest bundle 192 (full slot at 2:1; measured ratio 0.379 → 146 MiB) + free-space margin 16 + state 8 (measured: well below 1 MiB on a set-up lab device) |

Card image **1092 MiB = 1.145 GB** apparent, ~192 MiB on disk (sparse). Fits a nominal 2 GB card (usable size assumed 1.8 × 10^9 bytes
= 1717 MiB; the flash tool refuses only below that, "smaller than 2 GB"); data grows to ~881 MiB there, ~14.1 GiB on the recommended 16 GB
card. The old 448 MiB data figure was dropped: the update manager never holds two copies of one bundle (`docs/PERSISTENCE.md` §2.1).
Development OTA bundles: 76 064 201 / 76 322 250 / 75 941 326 bytes (rpi3_64 / rpi4_64 / rpizero2w_64) - the bigger slot costs nothing
in the bundle. Evidence: `reports/evidence/os_size_plan.{md,json}`, `os_image_<board>.md`, `os_ota_<board>.txt`.

Consequences: the flash tool writes and reads back 1092 MiB instead of 644 MiB (~1.7×); an update writes a 384 MiB slot image (whether
RAUC skips empty blocks is hardware-pending, OQ #90). The slot size cannot change after the first flash.

## 3. Persistent write paths (complete list; `os/storage.json`, `docs/PERSISTENCE.md` §3)

| Path | Writer | Content | Frequency |
|---|---|---|---|
| `/data/hifi-bridge` (= `/var/lib/hifi-bridge`) | bridge, boot step, health check | settings, secrets (password hash, session key, stream token, MQTT password), network config (+ pending trial), device CA/HTTPS keys and certificates, setup code until a password is set, `session.json` (the owned LMS item, no title), provisioning/recovery journals, OTA state, slot map, health result, two pre-update snapshots, the update bundle **only while staged/installing** | owner changes, session start/end, updates |
| `/data/bluetooth` (= `/var/lib/bluetooth`) | BlueZ | bonds of paired phones, adapter settings, BlueZ name cache (OQ #91) | pairing, connection |
| `/data/timesync` | systemd-timesyncd | empty clock file (mtime = last known time; Pi has no RTC) | every 900 s (was 60 s) |
| `/data/rauc` | RAUC | per-slot install status | per update |
| `/boot/uboot.env`, `/boot/uboot-redund.env` | U-Boot, RAUC via `fw_setenv` | A/B selection, boot counter | every boot, per update |
| `/boot/hifi-status.txt` | boot step | offline status for the owner | **only when its content changes** (was every boot) |
| `/boot/hifi-provision.json`, `/boot/hifi-recovery` | boot step | scrubbed / removed after processing | once |

Everything else is in RAM: journal (`Storage=volatile`, 16M/2M), bridge log (600 lines), fault history (200, mirrored to `/run`), syslog
queue (1000 lines, never spooled), now-playing/MQTT state, audio ring buffer (PCM never touches a file), network runtime files, `/var`
overlay, `/tmp`. No core dumps (`DefaultLimitCORE=0`, `core_pattern=|/bin/false`), no bytecode writes, empty machine-id, no random seed.
Temporary OTA files (`.upload`, `.part`, unreferenced bundles, atomic-write leftovers, half snapshots) are removed on failure, on cancel,
at a bridge restart and at every boot before anything reads the data; a staged bundle is kept across a reboot on purpose (power-loss
safety and retry; OQ #87). External TLS syslog of the bridge's own log stays possible; whole-journal forwarding is not built (OQ #88).
No audio recording, no playback history, no unbounded log.

Automatic checks: `persist.*` and `size.*` in the image check (each fails on a planted violation, 10 cases, `tests/os/test_persistence_layout.py`),
`tests/unit/test_persistence*.py` (the bridge writes only allow-listed files; boot cleanup; RAM fault history).

## 4. Setup wizard changes (P3, DECISIONS #137-#140, `docs/NETWORK.md` §12)

- New step **Network** after the owner account (only the owner may change the network) and before every step that needs the home network.
- Hotspot / no network: Wi-Fi scan list (one automatic scan when the radio has none, never while the hotspot runs), hidden SSID, password,
  country; above *Connect* an explanation readable without JavaScript: the hotspot goes away, join Wi-Fi X, open `http://<name>.local/`
  or the router's address, log in and press *Keep* within 3 minutes, otherwise the old settings and the hotspot come back by themselves.
- Pending change: *Keep* / *Undo now* with countdown and the new address; *Keep* continues to the next step. Rolled-back change: the reason
  (wrong password, not found, not confirmed) and "the hotspot is active again".
- **Connection loss and rediscovery:** the wizard position is server-side and RAM-only (`WebUI.wizard_at`); after a login at the new
  address (new cookie) every redirect lands on the network step while a change waits, else on the step shown last. A reboot restarts at
  the network step.
- **Ethernet users** see "Connected via Ethernet as …" + *Continue*; the Wi-Fi form is only behind *Set up Wi-Fi anyway*. Client Wi-Fi
  already connected: *Continue* / *Use another Wi-Fi*. No Wi-Fi hardware, unmanaged installation, agent unreachable: short note + skip.
- **No second state machine:** the step reuses the network page's form, trial card and handlers (`views_setup_network.py`); netmanager,
  trials and the pending file are unchanged (no bug found). DE/EN, light/dark, phone layout; axe-core: no violations on the new pages.
- Tests: UNIT `tests/unit/test_webui_setup_network.py` (21, real WebUI + real network agent over a UNIX socket, SIMULATED radio);
  BROWSER `tests/browser/test_ui_wizard_network.py` (Chromium against a second lab appliance provisioned with a setup hotspot: wrong
  password rolled back after the real 60 s, retry, new context at the device name, login, Keep, next step; DE desktop; keyboard);
  demo stack setup test. Screenshots `reports/screenshots/wiz-01..04-*.png`, `01c-wizard-network-ethernet-mobile-light.png`.
- Not tested: a real phone leaving a real hotspot (gate N-2/N-5), `.local` resolution on real phones (OQ #97), whether 3 minutes are enough (OQ #98).

## 5. U-Boot / recovery results (P2, DECISIONS #133, `docs/OTA.md` §4.1)

- Read from the source: libubootenv 0.3.7 uses the same copy selection as U-Boot 2026.01 `env_check_redund()`, writes the copy it did not
  read in place with `fsync`, and falls back to `/etc/u-boot-initial-env` when both copies are bad.
- **Finding (real data-loss path in stock libubootenv, upstream master unchanged):** a copy shorter than the environment (what a power
  cut during U-Boot's FAT rewrite can leave) made the whole read fail, so the default was stored as the newest copy over the valid one. Our
  patch treats such a copy as invalid, like U-Boot. Upstream submission = publication, owner decision OQ #93.
- The previous images' uboot-tools stored `bootcmd=bootp; ... root=/dev/nfs` when both copies were bad and spun at 100 % CPU on a short
  copy - replaced. The image default environment is U-Boot's complete compiled-in default plus the slot lines; an environment rebuilt from
  it is bootable and `boot.cmd` runs its fail-safe branch (A then B, 3 attempts each, `hifi.bootenv=reset`).
- **SIMULATED corruption matrix (13 cases, every image check + CI):** bit flip in either copy, half-written copy, truncated to 8 KiB / 0
  bytes, one file missing, equal serials, serial wrap, both bad. Patched libubootenv keeps the slot state in every single-copy case; both
  bad → fail-safe bootable default, previous slot choice lost (documented). Stock 0.3.7 lost the slot state in every truncated case.
- Not done on purpose: raw MMC sectors (OQ #81, needs real-hardware validation). **U-Boot itself was not executed** (its selection rule
  is ported and tested); nothing here claims U-Boot works on a real Pi.
- Hardware checklist extended: P1-4 (per-board firmware on each board/revision), P3-12 (both copies damaged while Linux runs), P3-13
  (truncated copy on a real FAT / power cut during `saveenv`), P3-14 (card moved to another board, OQ #82), L-3 (legal review).

## 6. Integration flake #86 (P4, DECISIONS #141, `reports/evidence/flake86_investigation.md`)

**Cause (confirmed by switching it on and off with everything else equal; Lyrion 9.1.2 source read in the container):** the bridge
releases its Lyrion item with `playlist deleteitem`, which does not rewrite Lyrion's persisted queue. After the player-outage scenario the
lab's player1 kept `playingAtPowerOff: 1` with an old bridge URL in its queue. When the lab was recreated, Lyrion resumed that item on
player reconnect with a 1 s fade-in; the old URL failed before any stream started, and Lyrion clears a pending fade-in only when a stream
really starts (`stop` / `playlist clear` do not). The first test's stream therefore faded in, and the bit-exact analysis could not align
("samples altered?"). The earlier volume cause (`c76c541`) was real but a second, different cause.

| Cold-lab variant (the two bit-exact tests) | Runs | Failures |
|---|---|---|
| stale lab state as left by earlier runs | 6 | **6** |
| fresh state | 5 | 0 |
| stale state, only player1's `playingAtPowerOff` cleared | 3 | 0 |
| warm lab, injected unreachable play request with fade-in / without | 3 / 3 | **3** / 0 |

**Fix (harness only, no sleep, no retry, no filtering):** `Lab.settle_players()` once per test process plays the lab's own synthetic
stream on each lab player until Lyrion reports play, then clears it, and checks in the Squeezelite log that a second start carries no
transition (else the session stops with an error). Regression test `tests/integration/test_00_lab_settle.py` (red before the fix).
After the fix: cold labs stale 6/6 and fresh 6/6 passed; **three full `run_integration.sh` runs from cold labs 56/56 each** (natural
chain of the original failure, stale, fresh), every bridge stream `transition type: 0`.

**Product-relevant side finding (OQ #99, decision needed):** on real installations Lyrion also keeps the released bridge URL in its
persisted queue. After a Lyrion restart a player that was playing at power-off tries to resume it; the bridge correctly rejects the old
URL (410, verified 3/3), and Lyrion then fades the guest's next stream in over 1 s (cosmetic; start latency unchanged 1.51-1.62 s).
The owner's productive Lyrion was never touched.

## 7. Test results and CI status

Final full run `scripts/run_all.sh` on `a7ae13b` (2026-11-21 02:02 UTC, log `reports/evidence/full_run.log`), all in Docker, after the
P6 image rebuild; **every one of the 21 exit codes 0**:

| Suite | Result | Label |
|---|---|---|
| static (shellcheck, licence copy, compile, systemd-analyze, actionlint) | exit 0 | STATIC |
| entry scripts, compose isolation, pins, workflow checks | 100 passed | UNIT/STATIC |
| unit (Python 3.11) | **1106 passed** (1 warning: pre-existing harness thread in `test_stream_faults.py`) | UNIT |
| integration (real Lyrion 9.1.2, 4 Squeezelite, Mosquitto, fake-BlueZ D-Bus, WebUI) incl. the new lab-settle test | **56 passed** (first test after the lab had been left by the previous outage run: green) | INTEGRATION + SIMULATED phone |
| outage scenarios Lyrion / player | exit 0 / exit 0 | INTEGRATION |
| PipeWire capture 0.3.65 / 1.4.2 | 11 passed + 1 skipped / 12 passed | INTEGRATION (real PipeWire), SIMULATED node |
| hwkit (real SBC/AAC codecs) | 32 passed | UNIT |
| mDNS vs. real Avahi, gateway-less device, network isolation | 18 PASS, 0 FAIL | INTEGRATION |
| network (real nft/dnsmasq/hostapd/wpa_supplicant/networkd/resolved) | 17 passed, live checks PASS | INTEGRATION, SIMULATED radio |
| OTA (real RAUC 1.13, real signatures, image libubootenv) | 13 passed | INTEGRATION, SIMULATED slots |
| remote syslog over verified TLS | 2 passed | INTEGRATION |
| Python 3.14 | 1104 passed, 2 skipped | UNIT |
| security (Bandit medium/high gate) / secrets (Gitleaks, 292 commits) | rc=0 / rc=0 | STATIC |
| OS (defconfigs ×3, libubootenv pin, licences, tests/os + tests/flashtool incl. U-Boot corruption matrix and persistence layout) | 215 passed | STATIC/UNIT |
| flash tool (offscreen Qt GUI + helper, simulated disks) + Linux package + smoke tests | 100 % line + branch (2959 statements, 606 branches); package and helper smoke OK | UNIT/SIMULATED |
| Home Assistant 2026.11.1 + Mosquitto | 7 passed | INTEGRATION |
| demo unit / demo stack (isolation, setup incl. wizard network step, chain, console UI, MQTT, recovery) | 55 passed / rc=0 (2 + 11 + 8 passed) | SIMULATED devices |
| browser (Chromium/WebKit, keyboard-only, axe-core, light/dark, phone/desktop, wizard network step from a hotspot) | 13 passed | INTEGRATION |

Coverage (`scripts/run_coverage.sh --integration`, scope `bridge/btbridge`, `reports/evidence/coverage.txt`, `docs/TEST_COVERAGE.md`):
**98.2 % lines, 96.0 % branches** (9788 statements, 3748 branches; before this package 98.1 / 95.9 with 9540 statements). No
`pragma: no cover`, no line-only tests; the gaps are the listed hardware-dependent and error-handler paths.

GitHub Actions (`ci`, 17 jobs, every suite except the image builds and the browser/demo stacks that need local compose profiles): 44783605532
(`8475549`, P1), 44817088946 (`23a4fa6`, P2), 44838313778 (`3c7cd70`, P3), 44850288448 (`a7ae13b`, P4) - **all 17/17 green**.
**Final CI on the P6 commits (rebuild evidence, full-run evidence, this handoff): pending at the time of writing -
recorded in the next line by a docs-only commit.**

Image builds (BUILD, P6, incremental on `a7ae13b`, all three boards): image checks **0 FAIL / 128 PASS / 21 INFO** each, secret scans 0
findings, OTA checks pass (bundle accepted by the image's keyring, foreign-signed bundle refused). New images replace the P2 images (card
sha256 `1f041187…` / `ccd53dd8…` / `3a999590…` vs. P2 `6a90614b…` / `64337ea5…` / `c0e28e4f…`); the rootfs now holds the P3 wizard code
(74 instead of 73 bridge modules). The P2 clean reproducibility rebuild (rpi3_64, no ccache) remains the latest from-scratch evidence.
Secrets: Gitleaks over the full history rc=0 after every merge; no token, key or password in commits, images or reports.

## 8. Remaining hardware risks (all HARDWARE-PENDING; `docs/HARDWARE_GATE_CHECKLIST.md`)

- **G1 (next):** real A2DP from Android/iPhone into PipeWire on the Pi, AVRCP play/pause, audible output on a Lyrion player, latency to
  the ear - only simulated BlueZ so far.
- Boot of all three boards with the new 64/384/384/256 layout; first-boot growth of the data partition on real 2 GB and 16 GB cards.
- U-Boot reading/writing the FAT env copies on each Pi, power cut during `saveenv`, real-FAT truncation (P3-12/13), card moved between
  boards (P3-14, OQ #82); RAUC install/rollback and power cuts during updates; whether RAUC writes the empty part of a slot (OQ #90).
- Per-board firmware: does each board and Zero 2 W revision (radio rev A1/B0) find its Wi-Fi/BT firmware after the pruning (P1-4).
- Wizard from a real hotspot with a real phone: does the phone leave/rejoin, does `.local` resolve, are 3 minutes enough (N-2, N-5, OQ #97/#98).
- RAM/CPU of the Zero 2 W with the 16 MiB RAM journal; SD write volume in operation (only the paths of section 3, not measured on a card).
- Flash tool: never wrote a real card; macOS pipeline never run (runner labels, PyInstaller/Qt minimum macOS, notarisation outcome); Windows build and signing open.
- PipeWire 1.2.8 behaviour when a real BlueZ node disappears (OQ #83).

## 9. Owner decisions needed (details and options in `docs/OPEN_QUESTIONS.md`; my recommendation in brackets)

1. **#99 Lyrion resumes a released bridge item after a restart** (1 s fade-in on the next guest stream): [accept and document for V1;
   reporting upstream to Lyrion is a publication - your call]. A non-URL-addressed queue clear was rejected (could touch the owner's music).
2. **#87 staged update bundle across a reboot** [keep: power-loss safety, the owner need not upload again].
3. **#88 forward the whole system journal to external syslog** [not for V1; the bridge's own log is forwarded].
4. **#90 write volume per update/flash** [accept; measure at gate P3].
5. **#91 BlueZ name cache of unpaired phones** [prune at boot later if hardware shows growth].
6. **#92 macOS: GUI checks the helper's code signature before elevating** [yes, small; before the first signed release].
7. **#93 send the libubootenv patch upstream** (publication) [yes, after G2 confirms it on hardware].
8. **#94 drop the non-target boards (Pi 3 B, CM3, Pi 400, CM4, CM4S)** - removes the last Synaptics file from the Pi 4 image [decide with the supported-board list].
9. **#95 prebuilt DT overlays** [keep for V1; add their source to the source bundle].
10. **#96 source-bundle hosting vs. written offer** and mirroring of the toolchain sources [decide before the first public release].
11. **#97 finding the device after leaving the hotspot / #98 3-minute window** [decide after the real-phone test N-2/N-5].
12. **#73 export-control notice** on a public download page and **#74 Zero 2 W Synaptics Bluetooth licence** - legal review (L-3), no clearance claimed.
13. **#81 raw MMC environment** (stays open until real-hardware validation) and **#82 card moved between boards**.
14. **Windows code signing** (#69 remainder) and the **release CA / hardware token** (#38) - no release key exists.
15. Apple setup for #126 when you want the first signed build (`docs/MACOS_SIGNING.md` §3: certificate, API key, environment `macos-signing`).
16. Local clean-up you may do (not done, not needed): `.lab/p4-flake86/` (94 MB, P4 evidence; the lab's `.lab/lms-config` was moved back by P4),
    `.lab/br-out/rpi3_64-repro` (P2 reproducibility build), `.lab/source-bundle/` (1.2 GB), merged agent worktrees under `.claude/worktrees/`.

## 10. Operator summary (copy/paste)

> Hi-Fi Bridge, phase 2b "Final Polish & Pre-Hardware Readiness" is complete in software and hardware-pending (`develop` on the private
> GitHub repo, CI green, nothing published, no release keys). Implemented in Docker only: the decided card layout 64/384/384 MiB + data
> 256 MiB grown on first boot (image 1092 MiB, fits a 2 GB card, 16 GB recommended; rootfs uses ~192 MiB of 384); the persistence rule
> (only credentials, keys, network/LMS/MQTT config, BT bonds, OTA/rollback and boot state on the card; journal, faults and diagnostics
> in bounded RAM; power-cut leftovers cleaned at boot; checked automatically in unit, static and image checks); a setup-wizard network
> step for hotspot-only setup reusing the existing trial/rollback machine; libubootenv with a full default environment plus a patch for a
> real data-loss bug (13-case simulated corruption matrix; U-Boot itself not executed, raw MMC not used); MQTT now clears phone/title on
> disconnect; per-board firmware with full licence texts and a prepared (unpublished) source bundle; a manual macOS sign/notarise
> workflow with setup guide (never run). The integration flake #86 was traced, with on/off experiments, to Lyrion resuming a stale queue
> item with a pending fade-in; fixed in the test harness, 3 full cold-lab runs 56/56. All three images were rebuilt (0 FAIL image checks,
> clean secret scans, never booted). Please review the owner decisions in §9 (notably #99) and the hardware risks in §8. The next gate is
> G1: real Bluetooth audio playback on the Raspberry Pi. Report: `reports/HANDOFF_FOR_CHATGPT.md`, log: `reports/PROGRESS_PHASE2B_POLISH.md`.
