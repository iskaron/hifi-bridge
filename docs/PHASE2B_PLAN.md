# Phase 2b – architecture, implementation plan, acceptance criteria

**Status (2026-11-19, package 6):** internal **development images are BUILD-VERIFIED for all three boards** (owner approval for
builds before gate G1): `scripts/os_build.sh`, all image checks, secret scan, SBOM, legal-info, `make ota` round trip and the measured
size plan are in **docs/IMAGE_BUILD.md**; firmware licences in **docs/IMAGE_LICENSING.md**. **Nothing has been booted, no A/B update has
run, and no hardware compatibility is confirmed**: every runtime statement is HARDWARE-PENDING (docs/HARDWARE_GATE_CHECKLIST.md, G2).

*Earlier status (2026-11-16):* the review decided P1–P9 (DECISIONS #51–#60); the preparation package built the br2-external skeleton,
the device-side provisioning/trust/recovery, the flash tool core and the Docker checks.

## 1. Evidence base
Buildroot **2026.02.x LTS**, commit `679b9ea` ("Update for 2026.02.3"), fetched by `scripts/os_fetch_buildroot.sh`. Versions and licences
below come from `make show-info` for our defconfigs (`reports/evidence/os_licenses_<board>.md`; 52 target packages per board after the
network and OTA packages, licence policy passed; SBOM `reports/evidence/os_sbom_<board>.cdx.json`).

| Need | Buildroot package (version) | Licence | Notes |
|---|---|---|---|
| Python runtime | python3 (3.14.5) | Python-2.0 + others | unit suite also runs on 3.14 (`scripts/run_py314.sh`) |
| D-Bus client for BlueZ/Avahi | python-jeepney (0.9.0) | MIT | |
| Bluetooth | bluez5_utils (5.79) + audio plugins, sbc (2.1) | GPL-2.0+, LGPL-2.1+ | PipeWire's BlueZ plugin needs bluez5_utils + sbc + glib |
| Audio graph | pipewire (1.2.8), wireplumber (0.5.10), lua (5.4.8), libsndfile (1.2.2) | MIT, LGPL-2.1+ | **WirePlumber needs Lua ≥ 5.3**: without it Kconfig silently dropped WirePlumber (found by our check). `pw-cat` needs libsndfile. No `parec`, so capture uses `pw-cat --raw` |
| mDNS | avahi (0.8) daemon, dbus (1.14.10) | LGPL-2.1+ / AFL-2.1 or GPL-2.0+ | DECISIONS #49/#52 |
| TLS/certificates | libopenssl (3.6.2) + `LIBOPENSSL_BIN` | Apache-2.0 | DECISIONS #50 |
| Updates | rauc (1.15.2, D-Bus, JSON), uboot (2026.01, `rpi_arm64`), uboot-tools (2025.10, fw_printenv) | LGPL-2.1 / GPL-2.0+ | host tools for tests: Debian RAUC 1.13, mkimage 2025.01 |
| Init | systemd (258.7), merged /usr | LGPL-2.1+ | units verified with `systemd-analyze` |
| Network | wpa_supplicant (2.11), hostapd (2.11), dnsmasq (2.92), iw (6.17), wireless-regdb, brcmfmac firmware | BSD-3 / GPL / ISC / **PROPRIETARY** | firmware licence reviewed: docs/IMAGE_LICENSING.md (G4 L-1 owner items remain) |
| Card tools | util-linux 2.41.4 (sfdisk, partx), e2fsprogs 1.47.3 (resize2fs) | GPL-2.0+ and others | first-boot growth of the data partition |
| Boot firmware | rpi-firmware (VARIANT_PI for 3 B+/Zero 2 W, VARIANT_PI4) | BSD-3-Clause per Buildroot | |

## 2. Decisions after review (P1–P9)
| # | Decision (status) | Implemented in this package | Open / gate |
|---|---|---|---|
| P1 | Buildroot 2026.02 LTS, one br2-external, three boards (**approved**) | `os/br2-external`; generated defconfigs; check for dropped options; licence policy | build + boot per board (G2 P1-1/P1-2) |
| P2 | Avahi (**approved**) | in the package set; the bridge's Avahi backend exists (stabilisation package) | resolution from real clients (G1 H1) |
| P3 | RAUC **preferred**; U-Boot boot-count **provisional**; alternatives open | `boot.cmd` (RAUC U-Boot pattern; slot B never tried while empty), `system.conf`, fw_env config | per-board feasibility, rollback, power cuts (G2 P3-1…P3-8); `tryboot` as the alternative |
| P4 | boot + rootfs-A + rootfs-B + data (**approved structure**); file systems and sizes after measurement | MBR layout via genimage; ext4 slots (placeholder 256 MB); data grows on first boot | measure, then decide ext4 vs. squashfs/verity and sizes (G2 P4-1…P4-4) |
| P5 | separate dev/release keys; official updates on configured self-built images; explicit trust; no release key in CI (**revised**) | explicit trust choice with no default; keyring from public CAs only; offline re-signing; `check-purpose=codesign` → docs/UPDATE_TRUST.md | create the release CA offline (G4 K-1); install tests on the device (G2 P5-*) |
| P6 | no hotspot without an owner-known individual password; flash tool or manual bootstrap file; FAT secrets evaluated (**revised**) | format + strict validation, journaled apply, in-place scrub, status without secrets → docs/PROVISIONING.md (incl. the evaluation) | the network service consuming `network.json`; P6-1…P6-5 |
| P7 | CA stays on the device; timing contradiction removed; usable OOB flow (**revised**) | trust key on the card + HMAC proof over a nonce + HTTPS check against only that CA → docs/TRUST_BOOTSTRAP.md | real phones (G1 H2, G2 P7-1) |
| P8 | physical SD recovery (**approved**); exactly once, power-cut safe | journaled requests; the journal survives a factory reset; tests inject power cuts at every step | P8-1…P8-3 |
| P9 | PySide6 provisionally; LGPL, packaging, disk detection, safe write/verify (**approved provisionally**) | core library `flashtool/hififlash` with simulated cards → docs/FLASH_TOOL.md | GUI, real block-device helper, installers (G3) |

## 3. Sizes (MEASURED 2026-11-19, docs/IMAGE_BUILD.md §4) and resources (to be measured)
* Rootfs per slot: **185 MiB used** of 256 MiB (files 157 MiB: kernel + modules 47, systemd 21, Python 10 + stdlib 15, OpenSSL 8,
  PipeWire 7 …; bridge 1.9). The earlier estimate of 100–150 MB was low. Boot FAT: 4–5 MiB of files. Development bundle: 74 MiB.
* Recommendation (owner decision): slots **384 MiB**, boot **64 MiB**, data ≥ **448 MiB** after growth → **2 GB card**; the minimum
  layout (224 / 16 / 144 MiB) fits a **1 GB** card. 4 GB is not needed; 32 GB certainly not.
* RAM on a Zero 2 W (512 MB): the bridge ~25–40 MB RSS (to be measured) plus PipeWire/WirePlumber, BlueZ, Avahi and RAUC. The unit caps the
  bridge at 256 MB. HARDWARE-PENDING (G2 P4-4).

## 4. Licence obligations (AGPL-3.0-only project)
* The image is a collection. Ship sources or a written offer for GPL/LGPL components (`make legal-info`; release step). The bridge's own
  source lives in `https://github.com/iskaron/hifi-bridge` (private today; how recipients get it is OPEN_QUESTIONS #43).
* Reviewed exceptions in `os/tools/license_report.py`:
  * the Raspberry Pi firmware;
  * the **PROPRIETARY brcmfmac firmware** (reviewed 2026-11-18, docs/IMAGE_LICENSING.md);
  * the Bootlin toolchain runtime (glibc LGPL-2.1+, GCC runtime exception);
  * e2fsprogs libss/libet (advertising clause);
  * Python's bundled parts;
  * Buildroot's skeleton files.
* The project `LICENSE` file is still missing (owner task; `HIFI_BRIDGE_LICENSE_FILES` is empty).
* Flash tool: PySide6/Qt LGPL-3.0 (dynamic linking, notices, replaceable); python-cryptography Apache-2.0/BSD.

## 5. Implementation plan (after G1 and approval; order = dependencies)
| Step | Content | Done in preparation | Acceptance criteria (verifiable) |
|---|---|---|---|
| 1 | Real builds of the three defconfigs in Docker (`btbridge-os` is the build image), `legal-info`, image scan | **done (BUILD-VERIFIED, package 6):** builds, image checks, secret scan, SBOM, legal-info, reproducibility (docs/IMAGE_BUILD.md) | reproducible build; legal-info complete; no secret in the image; boots on each board (G2 P1) - **boot HARDWARE-PENDING** |
| 2 | Boot mechanism per board, RAUC install/rollback, power-cut tests | boot.cmd, system.conf, fw_env, trust | G2 P3-1…P3-8, P5-1…P5-4 |
| 3 | Network service: Wi-Fi XOR hotspot from `network.json`, regulatory domain, 60 s fallback; Wi-Fi settings in the WebUI | provisioning and `network.json` | G2 P6-1, P6-2, P6-4 |
| 4 | Avahi + HTTPS on 443 in the image; boot unit | bootfiles, trust proof | G1 H1/H2, G2 P7-1 |
| 5 | Recovery on the device | journaled requests | G2 P8-* |
| 6 | Signed update upload in the WebUI, showing `update-trust.json`; optional update check against the fixed official source, GitHub Releases of `iskaron/hifi-bridge` (never automatic) | trust json incl. `official_source` | G4 U-1, U-2 |
| 7 | Flash tool GUI (PySide6), block-device helper with exclusive open and privilege separation, installers | core library | G3 F-1…F-6 |
| 8 | Measure sizes and resources; storage recommendation | **sizes measured, recommendation made** (docs/IMAGE_BUILD.md §4); resources need a board | G2 P4-3/P4-4 |

## 6. Risks
* **Boot chain per board is unproven.** U-Boot `rpi_arm64` on the Zero 2 W/3 B+/4 with RAUC boot-count is a pattern, not a result. `tryboot`
  may only be reliable on the Pi 4. Mitigation: P3-8 keeps both open.
* **Firmware, DTB and overlays live on the shared boot partition** and are not A/B. An update that needs new firmware or DTB is not atomic.
  Options: freeze the firmware per release line, or a careful two-phase firmware update (decide in step 2).
* **The Bluetooth UART:** Buildroot's example moves Bluetooth to the mini-UART (`miniuart-bt`); we do not. Whether A2DP is stable on PL011
  on each board must be measured (P1-3).
* **The vfat in-place overwrite assumption** for scrubbing secrets (P6-3); FTL residue is untestable without lab equipment.
* **The Zero 2 W's 512 MB RAM** and shared 2.4 GHz radio (plan E5).
* **PipeWire 1.2.8** differs from the tested 0.3.65/1.4.2 (the `pw-cat --raw` path must be re-verified, A3/A4).
* **Revocation** of update keys only by shipping a new keyring (no CRL), see docs/UPDATE_TRUST.md §5.
* **Licence of the brcmfmac firmware:** reviewed (docs/IMAGE_LICENSING.md): redistributable unmodified, only for the chips, with the
  licence texts; open owner items: export control, the revocable Synaptics Bluetooth licence (Zero 2 W), overlay sources.
* Key handling and release signing are organisational: process, hardware token, rehearsal (G4 K-1/K-2).
