# HANDOFF_FOR_CHATGPT - Phase 2b: software completion and hardware readiness (2026-11-18 .. 2026-11-20)

Labels used throughout: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (**none** in this
package). Chronological log with every red analysed: `reports/PROGRESS_PHASE2B.md`. Resume/status table: `reports/PHASE2B_WORKSTATE.md`.

## 1. Status and decisions requested

**Status: COMPLETE in software / hardware-pending.** All seven work packages of the owner's phase-2b assignment are implemented, reviewed,
tested in Docker, merged into `develop` and pushed; CI is green (§4). Nothing has run on a Raspberry Pi, a real Wi-Fi radio, a real phone or a
real SD card. The hardware gates G1-G4 stay open; no simulated result is presented as hardware validation (DECISIONS #73).

Owner decisions needed (details in `docs/OPEN_QUESTIONS.md`; recommendation in brackets):
1. **Partition sizes before the first release image** (#76; cannot change after the first flash): [slot 384 MiB, boot 64 MiB, data ≥ 448 MiB,
   **2 GB minimum card**]. The development images still use 256 MiB slots (rootfs uses ~185 MiB).
2. **Firmware licences** (#73-#75): export-control notice on a public download page [yes, short notice]; the revocable Synaptics Bluetooth
   licence for the Zero 2 W [accept, document; optional per-board pruning]; release source bundle must add the overlay sources, the Buildroot
   source and the Bootlin toolchain licences [yes, before G4].
3. **Code-signing certificates** for the Windows/macOS flash tool (#69) and the **release CA / hardware token** (#38) - nobody has created
   any release key; only throw-away development keys exist.
4. **Hotspot onboarding UX** (#78): network settings are now reachable before the wizard via a link; a dedicated wizard step instead?
5. **U-Boot environment hardening** (#80/#81): switch the image to libubootenv with an image default environment [recommended], or raw MMC
   sectors instead of FAT files.
6. Smaller items: #61-#67 (network/OTA policies), #68 (reverse-DNS namespace), #70-#72 (flash tool), #79, #82-#85.
7. **Next step:** start hardware gate G1 with the kit (`docs/HARDWARE_GATE_CHECKLIST.md`, `docs/HARDWARE_TEST_PROCEDURE.md`). Do not start
   a new phase before the owner/ChatGPT review of this handoff.

## 2. Guest experience outcome

Unchanged from phase 2a and still **SIMULATED/INTEGRATION only**: a simulated phone pairs, connects and plays through the real bridge into a
real Lyrion 9.1.2 + Squeezelite (bit-exact WAV/FLAC, takeover, sync groups, outages - 55/55 integration tests). New in 2b for the guest/owner
path: the device can now be onboarded through its own setup hotspot (network pages reachable before the wizard, DECISIONS #105), joins Wi-Fi
or Ethernet with a confirmed trial and automatic rollback, announces itself via mDNS also on a LAN without a gateway, and updates itself via
signed RAUC bundles with rollback. Real Bluetooth, real Wi-Fi and real audio output remain **NOT TESTED - hardware unavailable**.

## 3. Implementation and architecture (by package)

| # | Package | Result | Key decisions |
|---|---|---|---|
| 1 | Review fixes of the existing code | 28 findings fixed test-first (6 High: negative Content-Length memory exhaustion, Lyrion lock vs. watchdog restart loop, stale stream address, start crashes on bad MQTT CA / long names, power cut during CA creation) | #75-#77 |
| 2 | Demo and entry points | `gw_priority` removed (the owner's failure on Engine 25.0.2/Compose 2.24.5); measured minimums **Engine 24.0 + Compose 2.20** from a dind matrix; `make doctor` pre-flight; every entry script builds/pulls what it needs with progress; demo loudness -42 → -26 dBFS RMS (product audio unchanged); clean-clone tests; Lyrion isolation regression tests; all third-party images pinned by digest | #78-#82 |
| 3 | Network management | root agent `hifi-netd` + pure state machine: Ethernet/Wi-Fi, IPv4/IPv6, DHCP/static, Wi-Fi-XOR-hotspot fallback after 60 s with individual credentials, trials with 3-min confirmation and rollback, nftables firewall, Avahi/mDNS, hostname, timesyncd, `network-reset` recovery; WebUI pages DE/EN | #83-#89, #105-#106, #109, #113-#114 |
| 4 | OTA (RAUC) | version info, manual check, signed download/upload with progress, reboot confirmation, A/B slot view, health check + automatic and manual rollback, interrupted-update handling, configuration backup/restore (optional encryption: scrypt + AES-256-CTR via the openssl CLI + encrypt-then-MAC, no own crypto) | #90-#95, #107-#108, #110-#111 |
| 5 | Flash tool | PySide6 wizard (DE/EN, light/dark/system, accessible), board/image catalogue, resumable downloads, signature + hash checks, custom images and publisher keys, safe disk selection, per-device provisioning, write/verify/cancel, owner sheet, TLS trust bootstrap; privilege separation: unprivileged GUI + one-shot elevated helper (pkexec/polkit, osascript, UAC) with strict JSON-lines protocol and connect-back token; Linux package built, Windows/macOS prepared; LGPL texts shipped | #96-#99 |
| 6 | Buildroot images | internal development images for Pi 3 B+, Zero 2 W, Pi 4 (§5); read-only root without journal, `/var` overlay, persistent `/data`, A/B slots with kernel inside, firewall, RAUC with the dev CA, reproducibility fixes, SBOM, licence notices on the card | #100-#104, #111-#115 |
| 7 | Final QA | independent review of packages 3-6 (14 findings, all fixed), coverage 94.9 → 98.1 % lines, flake root causes, local mDNS, demo shows network/update pages | #105-#119 |

Module boundaries: `bridge/btbridge/net/` (agent, state machine, renderers, client), `bridge/btbridge/ota/` (RAUC client, source, transfer,
manager, health, backup), `bridge/btbridge/webui/views_network.py|views_update.py`, `flashtool/hififlash/{gui,helper,platforms}`,
`os/br2-external/**`, `os/tools/{image_check,secret_scan,tree_manifest,size_plan}.py`. Product code stays Python stdlib (+ optional jeepney;
openssl CLI); the flash tool adds PySide6 (LGPL-3.0, one-folder packaging, replaceable Qt libraries).

Git: `develop` only, Conventional Commits, merged with `--no-ff` per package; no `main` push, no tag, no release, no force-push, no
visibility or settings change. Uncommitted state at handoff: clean.

## 4. Tests, coverage and CI

Final full run `scripts/run_all.sh` on `b995068` (2026-11-20, log `reports/evidence/full_run_final.log`), all in Docker:

| Suite | Result | Label |
|---|---|---|
| static (shellcheck, compile, systemd-analyze verify + exposure, actionlint) | PASS | STATIC |
| entry scripts + compose isolation + image pins | 90 passed | UNIT/STATIC |
| unit (Python 3.11) | **1063 passed** | UNIT |
| integration (real Lyrion 9.1.2, 4 Squeezelite, Mosquitto, fake-BlueZ D-Bus, WebUI) | **54/55** - see below | INTEGRATION + SIMULATED phone |
| outage scenarios (Lyrion, player) | both exit 0 | INTEGRATION |
| PipeWire capture (0.3.65 bookworm / 1.4.2 trixie) | 11 + 1 skip / 12 passed | INTEGRATION (real PipeWire), SIMULATED node |
| hwkit self-tests (real SBC/AAC codecs) | 32 passed | UNIT |
| mDNS (built-in responder + Avahi backend vs. real Avahi, gateway-less device) | 18/18 PASS (red locally before package 7) | INTEGRATION |
| network (real nft/dnsmasq/hostapd/wpa_supplicant/networkd/resolved/avahi; live firewall + DHCP) | 17 passed + live PASS | INTEGRATION, SIMULATED radio |
| OTA (real RAUC 1.13 D-Bus service, real signatures, two-copy U-Boot env) | 13 passed | INTEGRATION, SIMULATED slots |
| remote syslog over verified TLS (real rsyslog) | 2 passed | INTEGRATION |
| Python 3.14 | 1 failed in the run (new FLAC fault test needs the `flac` binary the 3.14 image lacks) → fixed `test(unit)` skip; rerun **1061 passed, 2 skipped** | UNIT |
| security (Bandit gate) / secrets (Gitleaks, 255 commits) | rc=0 / rc=0 | STATIC |
| OS (defconfigs 3 boards, licences, card layout, boot robustness, image tools) | 162 passed | STATIC/UNIT |
| flash tool (offscreen Qt GUI + helper, simulated disks) + Linux package + helper smoke | 158 passed, 100 % line+branch; package built | UNIT/SIMULATED |
| Home Assistant 2026.11.1 + Mosquitto | 7 passed | INTEGRATION |
| demo unit / demo stack (isolation, setup, chain, console UI, P7, MQTT) | 55 passed / rc=0 (2 + 11 + 8 passed) | SIMULATED devices |
| browser (Chromium/WebKit, keyboard-only, axe-core, light/dark, phone/desktop, trusted HTTPS) | 11 passed | INTEGRATION |

Integration red: `test_end_to_end_bit_exact[wav]` - the first test after the lab had been recreated at the start of the run - captured audio
that could not be aligned; **3/3 green when rerun** (2 tests each), green in CI and in the three previous full runs. The player gain was
correct this time, so the earlier volume cause is excluded; root cause open (OPEN_QUESTIONS #86, harness/lab state).

Coverage (`scripts/run_coverage.sh --integration`, scope `bridge/btbridge`; `docs/TEST_COVERAGE.md`): **98.1 % lines, 95.9 % branches**
(before package 7: 94.9 / 91.8). Flash tool: **100 % line + branch** in its own suite. Remaining gaps are listed with reasons (CLI shells,
Protocol bodies, hardware-dependent D-Bus errors, lock-interleaving guards, fsync/close error handlers); no `pragma: no cover`, no
line-only tests.

GitHub Actions (17 jobs, every suite above except the image builds): package runs 44350223892, 44372722429, 44420917417, 44483643387,
44504983152 green; package 6 run 44597478524 16/17 (Home Assistant test race, fixed); 44612468369 17/17; 44692532202 16/17 (Bandit B104 on
the new discovery bind, justified); 44710523221 on `b995068` 16/17 (python314: the FLAC fault test above, fixed).
**Final CI: run 44721926361 on `48925e8` (all fixes + this handoff) - 17/17 green.** Only this docs line was committed after it.

Real components in the tests: Lyrion 9.1.2, Squeezelite, Mosquitto 2.0.20, Home Assistant 2026.11.1, PipeWire 0.3.65/1.4.2, Avahi 0.8, RAUC
1.13 (D-Bus service, real signatures, libubootenv), nftables, dnsmasq, hostapd, wpa_supplicant, systemd-networkd/resolved, Qt 6 offscreen,
Chromium/WebKit with axe-core, Python 3.11/3.13/3.14. Simulated: phones, BlueZ, Wi-Fi radio, RAUC slot installation (no loop device in
Docker: RAUC verifies the signature and stops at the bundle mount), disks/cards.

## 5. Build results per Pi model (BUILD - never booted)

Buildroot 2026.02 LTS, built in Docker with `--cpus=3 --memory=8g`, `BR2_JLEVEL=3` (`docs/IMAGE_BUILD.md`, evidence
`reports/evidence/os_image_<board>.md`, `os_sbom_<board>.cdx.json`, `os_size_plan.md`).

| Board | Clean build | Card image | rootfs used | Image check | Secret scan | Dev OTA bundle |
|---|---|---|---|---|---|---|
| rpi3_64 (Pi 3 B+) | 3631 s (warm ccache) / 6980 s (no ccache); rebuilt incrementally after the 7b-2 fixes (283 s) | 644 MiB | 185 MiB of 256 | **0 FAIL / 103 PASS** (after 7b-2) | 0 findings | accepted by the image keyring, foreign bundle refused |
| rpizero2w_64 (Zero 2 W) | 3645 s | 644 MiB | 185 MiB of 256 | 0 FAIL / 89 PASS (before the 7b-2 fixes) | 0 findings | accepted / refused |
| rpi4_64 (Pi 4) | 7485 s | 644 MiB | 185 MiB of 256 | 0 FAIL / 89 PASS (before the 7b-2 fixes) | 0 findings | accepted / refused |

- Layout: MBR, boot FAT 64 MiB, rootfs-A/B 256 MiB, data 64 MiB growing on first boot, 4 MiB alignment; two U-Boot environment copies.
- Reproducibility (rpi3_64, ccache vs. no ccache): MBR, boot FAT, data and slot B identical; 7394/7398 rootfs files identical, the other 4
  differ only in ccache-rewritten `__FILE__` paths → release builds without ccache. Not shown: two ccache-free builds against each other.
- Size: largest parts are kernel + modules (46 MiB), systemd 21 MiB, Python 25 MiB. Recommendation see §1 decision 1.
- The Zero 2 W and Pi 4 images predate the 7b-2 OS fixes (two-copy U-Boot env, root-owned boot FAT, discovery port); a rebuild picks them up.

## 6. Security and licence findings

- **Security reviews:** existing code 30 findings (package 1), new code 14 findings (package 7: 1 High - hotspot-only onboarding dead end;
  5 Medium - network trial races, restore ordering, OTA stuck states, non-redundant U-Boot env), all fixed with tests
  (`reports/REVIEW_PHASE2B_EXISTING_CODE.md`, `reports/REVIEW_PHASE2B_NEW_CODE.md`). Coverage work found one more real bug: unbounded login
  back-off exponent (overflow after ~1,030 failures → back-off bypass), fixed (#119).
- Gitleaks over the full history: clean (two reviewed false positives pinned by fingerprint in `.gitleaksignore`). Bandit: 0 Medium/High.
  Image secret scan (rootfs, boot FAT, raw card): 0 findings on every board; no setuid files, all accounts locked, no SSH/telnet.
- No release key exists anywhere; builds and tests use throw-away development keys only; nothing was put into GitHub secrets.
- Privacy: diagnostics bundle no longer contains neighbours' Wi-Fi names (#109); credentials are never logged.
- **Firmware licences** (`docs/IMAGE_LICENSING.md`, read from the hash-verified archives): Raspberry Pi boot firmware and Broadcom/Cypress/
  Synaptics radio firmware may be redistributed publicly **unmodified, with their licence texts, only for Raspberry Pi hardware**; every
  image carries `licenses/` on the boot FAT and the build fails if a text is missing; the Synaptics Bluetooth licence was missing upstream and
  is now shipped. Open: export control, revocability, release source bundle (§1 decision 2).
- Flash tool: LGPL-3.0 obligations met for PySide6/Qt (texts shipped, replaceable libraries, source offer, About Qt).
- SBOM: CycloneDX 1.6, 120 components per board; licence policy 52 packages, 0 to review.

## 7. Screenshots (current, regenerated by the final run)

`reports/screenshots/`: `net-01..07` (network pages incl. hotspot and trial banner, DE/EN, light/dark), `upd-01..06` (update and backup
pages), `flashtool-*` (22 wizard screens: board/image selection, disk selection, provisioning, progress, errors, DE/EN, light/dark),
`demo-*` (demo console incl. the SIMULATED network/update card), `axe-results.json` (axe-core accessibility results); older WebUI
screenshots `01..11`. Only a subset of the files is tracked in Git (the rest is git-ignored and regenerated by `scripts/run_browser.sh`).

## 8. Risks, unverified items and open hardware tests

- **Everything at runtime on a Pi is HARDWARE-PENDING:** boot and boot time on all three boards, U-Boot environment and boot-count
  behaviour, RAUC install/rollback and power cuts during updates (P3-1, P3-5, P3-6), data growth, read-only root in operation, radio firmware,
  Bluetooth on PL011, RAM/CPU on the Zero 2 W.
- **Network:** real Wi-Fi association, hotspot with a real phone (does the phone stay on a hotspot without internet? gate N-5), real `iw`/
  `systemctl` timing, IPv6 on a real router; gate items N-1..N-11.
- **Flash tool:** never wrote a real card; Windows/macOS builds, signing and notarisation not done; real disk enumeration only from recorded
  outputs.
- **Residual U-Boot risk:** both environment copies bad at once → A is tried first (may be the older version), visible only on the kernel
  command line; Linux-side writes with both copies bad would produce a generic default (#80).
- PipeWire: `pactl unload-module` crashes pipewire-pulse 1.4.2 in the harness; whether a real BlueZ node removal can upset PipeWire 1.2.8 on
  the device is open (#83, hardware A3/A4).
- Docker drops multicast on `internal` networks (Engine 29.1.3); the mDNS suite uses its own non-internal network without a host address
  (#116, #85).

## 9. Next steps

1. Owner/ChatGPT review of this handoff and the decisions in §1.
2. Hardware gate G1 with the kit; then G2 (network, OTA, power cuts) on real Pis - order and items in `docs/HARDWARE_GATE_CHECKLIST.md`.
3. Before a release image: partition sizes, release CA on a hardware token, ccache-free release build, rebuild of all three boards,
   source bundle completion, code-signing certificates.
4. Local clean-up the owner may do (not done by me, not needed): the merged agent worktrees under `.claude/worktrees/` and the 9 GB
   reproducibility build `.lab/br-out/rpi3_64-repro`.

## 10. Operator summary (copy/paste)

> Phase 2b of Hi-Fi Bridge (Bluetooth-to-Lyrion bridge) is complete in software and hardware-pending. On `develop` (private GitHub repo
> iskaron/hifi-bridge, CI green) Claude Code implemented and tested in Docker only: fixes for 30 review findings in the existing code; the
> owner's demo failure (`gw_priority`) and measured Docker minimums (Engine 24.0 / Compose 2.20) with a pre-flight check and self-building
> entry scripts; network management (Ethernet/Wi-Fi, IPv4/IPv6, setup hotspot with individual credentials, trials with rollback, firewall,
> mDNS, time sync, recovery); OTA on RAUC (signed bundles, A/B, health check, rollback, backup/restore); a PySide6 flash tool with a separate
> privileged helper (simulated disks only); and internal Buildroot development images for Pi 3 B+, Zero 2 W and Pi 4 (built, image checks
> 0 FAIL, secret scan clean, **never booted**). A final independent review found 14 issues in the new code (1 High: hotspot-only onboarding
> dead end), all fixed with tests; coverage is 98.1 % lines / 95.9 % branches, flash tool 100 %. Firmware licences allow public
> redistribution unmodified, with licence texts, for Raspberry Pi only. Nothing was published; no release keys exist. Please review the
> decisions in §1 (partition sizes / 2 GB card, firmware-licence items, signing certificates and release CA, hotspot UX, U-Boot env
> hardening) and the hardware-pending list in §8; the next step is hardware gate G1. Report: `reports/HANDOFF_FOR_CHATGPT.md`, log:
> `reports/PROGRESS_PHASE2B.md`, decisions #73-#119, open questions #50-#86.
