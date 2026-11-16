# HANDOFF_FOR_CHATGPT — Phase-2b review implementation and preparation (2026-11-16)

Previous handoff (software stabilisation) is in git history at commit `576b823`. Decisions: `docs/DECISIONS.md` #51–#68 are new.
Gate checklist: `docs/HARDWARE_GATE_CHECKLIST.md`. Plan: `docs/PHASE2B_PLAN.md`.

## 1. Status and decisions requested
**Status: COMPLETE for every task of this package that can be done without hardware. Stopped before the hardware gate.** Nothing in this
package has been run on real hardware.
- **Not built:** no Raspberry Pi image.
- **Not booted:** nothing.
- **Not performed:** no A/B update; no Bluetooth connection on real hardware.
- **Not written:** no real disk. The flash tool only writes regular files that simulate cards.

All evidence is UNIT, INTEGRATION (real Docker services and host tools), SIMULATED or STATIC. **There is no REAL-HARDWARE evidence.**

Decisions requested from the owner:
1. **Run gate G1** (stock Pi OS, phones, player) per `docs/HARDWARE_TEST_PROCEDURE.md` + `docs/HARDWARE_GATE_CHECKLIST.md`.
2. Open product questions (`docs/OPEN_QUESTIONS.md` #35–#41):
   - #35: review the licence of the PROPRIETARY brcmfmac firmware;
   - #36: how firmware/DTB updates work (not A/B);
   - #38: who creates the release CA, offline and on which hardware token;
   - #39: whether a factory reset also removes Bluetooth pairings;
   - #40: whether revocation by shipping a new keyring (no CRL) is enough for v1;
   - #41: add the project LICENSE file (AGPL-3.0-only).
3. Optional: `scripts/cleanup_own_images.sh --apply` removes **18 superseded images of this project (about 7.1 GB)**. They are left over from
   rebuilds of my own images. My earlier deletions were refused by the permission system, and I did not work around that.

## 2. Implementation status by review decision
| Decision | Status | Where |
|---|---|---|
| P1 Buildroot 2026.02 LTS, one br2-external (approved) | skeleton for Zero 2 W / 3 B+ / 4; **defconfigs generated from the pinned upstream board configs + fragments**; a check fails if Kconfig silently drops a required option; licence policy from `make show-info` | `os/`, `scripts/os_defconfigs.sh`, `scripts/os_licenses.sh` |
| P2 Avahi (approved) | in the package set; bridge-side Avahi backend from the last package | `fragments/common.fragment` |
| P3 RAUC preferred, U-Boot boot-count provisional | RAUC `system.conf`, U-Boot `boot.cmd` following the RAUC pattern (compiles with mkimage; empty slot B is never tried), fw_env config. **Per-board feasibility is open** (G2 P3-1…P3-8, `tryboot` kept as the alternative) | `os/br2-external/board/hifi-bridge/{rauc,uboot}` |
| P4 boot + A + B + data | MBR layout via genimage, verified with dummy content: 4 partitions, A = B size, FAT contents, data label, **first-boot growth of the data partition** on an image file. File systems/sizes are placeholders until measured | `genimage.cfg.in`, `post-image.sh`, `growdata.sh`, `tests/os` |
| P5 revised (dev/release separate, official updates on configured self-built images, explicit trust, no release key in CI) | build-time choice official / own / official+own **with no default** (the build fails); keyring from public CA certificates only (refuses keys, non-CA input, several certificates); recorded in `/etc/hifi-bridge/update-trust.json`; release = **offline `rauc resign`** of a CI-signed bundle; `check-purpose=codesign`. **Verified with real RAUC 1.13** (matrix: official⇒release only, own⇒own only, both⇒both) | `docs/UPDATE_TRUST.md`, `post-build.sh`, `tests/os/test_update_trust.py` |
| P6 revised (no hotspot without owner-known individual password; flash tool or manual file; FAT secrets evaluated) | `hifi-bridge-provision/1`: strict validation (problems by field, never values), journaled idempotent apply, **in-place scrub**, power-cut safety tested, status file without secrets; **no network entries ⇒ no hotspot ever**. FAT evaluation (residual data in FS and FTL, hand-edited files, takeover, failures, deletion) in the doc | `bridge/btbridge/provisioning.py`, `bootfiles.py`, `docs/PROVISIONING.md` |
| P7 CA stays on the device; contradiction removed; usable OOB flow | The flash tool cannot know the fingerprint at flash time. Instead it puts a **one-time trust key** on the card. After first boot it fetches the CA from `/trust/proof` with a fresh nonce and checks `HMAC-SHA256(key, nonce, SHA-256(CA))`, then verifies HTTPS against **only** that CA. The key ends with the setup. A **CA-swapping LAN attacker is detected** (test) | `trustproof.py`, `/trust/proof`, `hififlash/trust.py`, `docs/TRUST_BOOTSTRAP.md` |
| P8 SD recovery (approved; once + power-cut safe) | journal before/after the action; the request file is removed only after "done"; the journal survives a factory reset; tests inject a power cut after each step (repeat vs. no repeat) | `recovery.py`, `tests/unit/test_recovery.py` |
| P9 PySide6 provisionally | **core library only, no GUI:** disk policy (removable, not system, size limits, typed confirmation, identity re-check), signed manifest (openssl-signed, verified with python-cryptography), write → hash → read-back → slot → verify, cancel, **invalidation on failure**, per-device secrets, owner sheet DE/EN, CLI without a write command. LGPL/packaging/privileges documented | `flashtool/hififlash`, `docs/FLASH_TOOL.md` |
| Coverage goal (90 % is not a ceiling) | **91 %** line+branch for bridge + flash tool; new modules 91–100 % | `docs/TEST_COVERAGE.md` |

Supporting changes:
- **Boot-partition unit:** `hifi-bridge-boot.service` is the only writer of `/boot`. It has no network and a systemd exposure of 0.6.
- **Public-route pin:** the set of public routes is pinned by a test.
- **Firmware config:** the product firmware configuration has no `miniuart-bt`.
- **OS tooling image:** one image, `btbridge-os` (522 MB), for all OS and flash-tool checks.

## 3. Defects found and fixed in this package
1. **WirePlumber silently dropped:** without Lua ≥ 5.3, Buildroot's Kconfig drops WirePlumber, and also two wrongly named options. Found by the new defconfig check.
2. **Deadlock on double stop signal:** in the appliance, mDNS responder and Avahi publisher.
   * The SIGTERM handler called `threading.Event.set()`. A second SIGTERM during that call deadlocked the process: no mDNS goodbye, and the appliance would only have ended by SIGKILL.
   * The mDNS interop test exposed it intermittently (`pkill` hit tini and Python). A `faulthandler` stack dump confirmed the cause.
   * The fix is an async-signal-safe `StopSignal`. Before the fix: about 1 hang in 4–11 runs; after: 0 in 40, plus 5 × 13/13 runs of the full script. Regression test: 30 real processes, each receiving 2–5 SIGTERMs back to back.
3. **mDNS test script fragility:** `test_mdns.sh` relied on fixed sleeps, and could count an old goodbye line as new.
   * It now polls with timeouts and counts only new goodbye lines.
   * It measures the time until the name disappears after a goodbye: ≤ 4.5 s, including the 3 s resolve timeout.
   * It prints diagnostics if a stop hangs.
4. **Test-day crash:** with a root-owned `/boot/firmware` on stock Pi OS, `appliance --boot-dir` would have crashed. It now reports and continues.
5. **Flash tool:** if even invalidating the card failed, the tool kept silent. It now reports that the card must not be used.
6. **genimage template:** a comment contained the placeholder token, and `sed` broke the generated config.
7. **During development, never committed broken:** inserting the `/trust/proof` view moved `@public` off `/healthz`. It was caught by the tests, and a guard test was added.

## 4. Tests — clean-slate full run on commit `0ae4d2a`
Evidence: `reports/evidence/full_run_phase2b_prep.log`, 2026-11-16T16:19Z, after `lab_down`.
```
exit codes: static=0 unit=0 integration=0 outage_lms=0 outage_player=0 pipewire=0 hwkit=0 mdns=0 syslog=0 py314=0 security=0 os=0 homeassistant=0 browser=0
static: shellcheck (incl. OS scripts), python compile, systemd-analyze verify (hifi-bridge 1.9 OK, hifi-bridge-boot 0.6 SAFE), actionlint
unit 294 | integration 55 (real LMS 9.1.2 / Squeezelite / Mosquitto) | outages lms + player | PipeWire 0.3.65: 11 + 1 skip, 1.4.2: 12
hwkit 32 | mDNS vs real Avahi 13/13 (goodbye: name gone after 3.8 s) | syslog vs real rsyslog 2/2 | Python 3.14: 294 | bandit: 0 high/medium
OS + flash tool: 3 defconfigs PASS (72 requirements kept each), licences 48 target packages/board, 0 to review; tests/os + tests/flashtool 46
Home Assistant 2026.11.1: 7/7 | browser 5/5 (Chromium + WebKit, axe-core)
```
**Coverage** on the same commit, unit + integration + os + flash tool (`reports/evidence/coverage.txt`): **91 %** (5606 statements, 2116 branches).

**Timings** (real LMS + Squeezelite with a virtual DAC + SIMULATED phone; **not acoustic**):

| Measurement | Result |
|---|---|
| Play → sound | WAV 1.58 s / FLAC 1.99 s |
| Pause → silence | 0.057 s |
| Resume | 1.58 s |
| Takeover → LMS command | 2.04 s |

**Not executable here (gate items):**
- real BlueZ, phones, AVRCP, volume and players;
- acoustic latency;
- the Buildroot build and boot on any board;
- U-Boot/RAUC install, rollback and power cuts;
- the hotspot on the on-board Wi-Fi;
- vfat in-place overwrite on real media, and FTL residue;
- the flash tool on real computers and cards;
- installers;
- CI on GitHub (the repository is unpublished).

## 5. Open hardware risks
| Risk | Gate item |
|---|---|
| Capturable phone node / PipeWire 1.2.8 `pw-cat --raw` path | G1 A3/A4 |
| Music vs. notification separation on real phones (main software risk) | G1 C1/C2 |
| U-Boot `rpi_arm64` + boot-count on each board; `tryboot` maybe only on the Pi 4 | G2 P3-1…P3-8 |
| Firmware/DTB/overlays on the shared boot partition (not A/B) | G2 P3-7, OQ #36 |
| Bluetooth on PL011 without `miniuart-bt` | G2 P1-3 |
| vfat in-place overwrite assumption; FTL residue cannot be tested without lab equipment | G2 P6-3 |
| Zero 2 W: 512 MB RAM, shared 2.4 GHz radio | G1 E5, G2 P4-4 |
| Power cut during install / boot-count write | G2 P3-5/P3-6 |
| Licence of the brcmfmac firmware | G4 L-1 |

## 6. Docker resources (read-only, `reports/evidence/docker_usage_phase2b_prep.txt`)
| | Start of package | End of package |
|---|---|---|
| `/dockerfs` (196 G) | 71 G used, 115 G free | 73 G used, **114 G free** |
| Images | 47.4 GB (103) | 48.7 GB (107) |

The one new image is `btbridge-os:dev` (522 MB).
- **Superseded own images:** 18 images, about 7.1 GB, removable with the owner-run cleanup script.
- **Lab:** stopped with `lab_down` (own compose project only).
- **Buildroot checkout:** in `.lab/buildroot` (~80 MB, host filesystem, not versioned).
- **Foreign resources:** none touched; no prune command used.

## 7. Changes and commits
7 commits on `develop` (`576b823..0ae4d2a`) plus this handoff/evidence commit. All use the identity `Oliver Meyer <olm@cativa.net>`. 131 files
changed. There was no push, tag, release or history rewrite.

| Commit | What |
|---|---|
| a3af4b2 | device: provisioning, `/trust/proof`, journaled recovery, bootfiles step + its systemd unit |
| e96416f | `os/` skeleton, OS tooling image, defconfig/licence checks, `tests/os`, flash tool core + `tests/flashtool` |
| dfb7736 | docs: P1–P9 review, UPDATE_TRUST, PROVISIONING, TRUST_BOOTSTRAP, FLASH_TOOL, gate checklist; CI/run_all/coverage wiring |
| 65127b7 | flash tool CLI (no disk writing); bundle ships the flash tool + gate docs; coverage tests |
| adb3c1e | fix: appliance keeps starting with a non-writable boot directory |
| 7b1aded | fix: async-signal-safe stop handling; robust mDNS test script |
| 0ae4d2a | docs: stop handling, measured goodbye |

## 8. Recommendation for the next step
1. **Run gate G1 now.** Everything for it is prepared:
   * `scripts/hwkit_bundle.sh`;
   * `docs/HARDWARE_TEST_PROCEDURE.md`, about 4 h;
   * the new test-day items: provisioning and `hififlash verify-device` from a laptop.

   G1 decides whether the audio path works with real phones, which is the precondition for any image work.
2. In parallel, owner decisions: OQ #35, #38, #41 (licence review, release-CA process, LICENSE file). They block a public image, not G1.
3. After G1 passes and is approved, start **phase 2b step 1/2** (PHASE2B_PLAN §5):
   * real Buildroot builds in `btbridge-os`;
   * then the boot-mechanism and RAUC/rollback feasibility **per board** (G2).

   Only after that: the network service (step 3) and the flash-tool GUI (step 7).

**Do not** start a productive OTA rollout before G4 (keys, licences, power-cut results per board).

## 9. Operator summary (copy/paste)
> The phase-2b review is implemented without hardware. Everything ran in Docker; no Pi image was built or booted, no real Bluetooth was
> used, and no real disk was written.
>
> - **P5:** update trust is now an explicit build choice (official / own / both, no default), verified with real RAUC 1.13. Release bundles
>   are re-signed offline, so the release key never touches CI.
> - **P6:** a strictly validated, journaled provisioning file on the boot partition, scrubbed in place after use. There is no hotspot
>   without an owner-known password, and the FAT residual-data risks are documented.
> - **P7:** the contradiction is removed. The flash tool cannot know the fingerprint at flash time, so it puts a one-time trust key on the
>   card and later authenticates the device CA with an HMAC proof plus an HTTPS check. A CA-swapping attacker is detected in tests.
> - **P8:** recovery runs exactly once and is power-cut safe.
> - **P9:** the flash-tool core (disk safety policy, signed manifest, write/verify/invalidate, provisioning) is tested with simulated cards
>   only.
> - **Buildroot:** a br2-external skeleton for Zero 2 W / 3 B+ / 4 with checked defconfigs (this caught WirePlumber being silently
>   dropped) and a licence report (48 packages; brcmfmac firmware licence still to review).
> - **Bugs fixed:** a real deadlock with back-to-back SIGTERMs (appliance/mDNS/Avahi) and a test-day crash with a read-only
>   `/boot/firmware`.
>
> Full clean run on one commit: all 14 suites green, 294 unit, 55 integration, coverage 91 %. Docker: 114 GB free; 18 superseded own
> images (~7 GB) can be removed with the owner script.
>
> Next: hardware gate G1, then phase 2b builds and the per-board boot/rollback feasibility (G2).
