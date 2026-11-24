# Development images: build, checks, sizes, reproducibility (phase 2b, package 6)

**Status (2026-11-19): internal DEVELOPMENT images built for Pi 3 B+ (`rpi3_64`), Pi Zero 2 W (`rpizero2w_64`) and Pi 4 (`rpi4_64`).
Everything below is BUILD-VERIFIED (built and taken apart in Docker on a host without any hardware). Nothing has been booted;
every runtime statement is HARDWARE-PENDING (docs/HARDWARE_GATE_CHECKLIST.md, G2).** The images are internal, never published, and marked
as development builds (os-release `VARIANT_ID=development`, `/etc/hifi-bridge/build-info.json` with `"hardware_validated": false`, file
name `…-development.img`). Their update trust is `own` with a temporary development CA (docs/UPDATE_TRUST.md, `scripts/os_dev_ca.sh`).

**Package 7b-2 (2026-11-20):** only **rpi3_64 was rebuilt** (incrementally) after the boot-robustness fixes of review N-6/N-8/N-13/N-14
(section 9); `reports/evidence/os_image_rpi3_64.md` and `os_ota_rpi3_64.txt` are from that rebuild (0 FAIL, 103 PASS, 20 INFO; secret
scan 0 findings). The reports of **rpi4_64 and rpizero2w_64 predate these fixes** (not rebuilt; same br2-external tree, so the same
changes apply when they are built next).

**Final polish P1 (2026-11-20):** card layout of DECISIONS #120 (boot 64 | A 384 | B 384 | data 256 MiB, 1092 MiB image) and the
persistence rule of DECISIONS #121 (docs/PERSISTENCE.md). Only **rpi3_64 was rebuilt** (incrementally, ~10 min): 0 FAIL, 119 PASS,
20 INFO (new checks `persist.*` and `size.*`), secret scan 0 findings, OTA check passes. rpizero2w_64 and rpi4_64 keep the old layout until
their rebuild (package P2).

**Final polish P2 (2026-11-20):** U-Boot environment hardening (libubootenv + image default environment, DECISIONS #133), firmware per
board (#134), source-bundle preparation (#135). **All three boards rebuilt** (incremental `retarget` builds, section 10): each image check
0 FAIL, 128 PASS, 21 INFO; secret scans 0 findings; OTA checks pass. Every `reports/evidence/os_*` file of the three boards and
`os_size_plan.{md,json}` was regenerated in P2 and carries an "Evidence: final polish P2" line (Markdown header, `"evidence"` key in the
secret-scan JSON, a `hifi-bridge:evidence` property in the SBOM; the `os_licenses_*.md` and `os_legal_manifest_*.csv` files are
regenerated from the same builds but have no header by format). Sections 2-8 below still describe the package-6/7b-2/P1 state where they
say so.

**Final polish P6 (2026-11-21):** all three boards rebuilt **incrementally** on `develop` `a7ae13b` (`os_build.sh <board> build`, which
re-syncs the bridge package), so the images carry the P3 wizard network step (74 bridge modules instead of 73; no OS configuration
changed since P2). Image checks 0 FAIL, 128 PASS, 21 INFO each; secret scans 0 findings; OTA checks pass (bundles 76 064 201 / 76 322 250
/ 75 941 326 bytes for rpi3_64 / rpi4_64 / rpizero2w_64); new card-image sha256 `1f041187…` / `ccd53dd8…` / `3a999590…` (the P2 images
`6a90614b…` / `64337ea5…` / `c0e28e4f…` are replaced). The `os_image_*`, `os_ota_*`, `os_secret_scan_*`, `os_sbom_*` and `os_size_plan.*`
evidence now carries an "Evidence: final polish P6 rebuild" line; legal-info and SBOM content were not regenerated (no package changed).
BUILD only - nothing booted.

**G1 PCM-ingress fix (2026-11-24, branch `wip/g1-pcm-ingress`):** the first real-hardware day showed that the image had no PipeWire
configuration at all. The overlay now ships `/etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf` (phone audio as an
Audio/Source, DECISIONS #142) and `/etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf` (44.1 kHz clock), identical to the test-Pi
files; new image checks `audio.*` (BlueZ SPA plugin, `pw-cat` + `pw-dump`, role rule, clock rates; seat monitoring INFO). All three boards
rebuilt incrementally: 0 FAIL, 132 PASS, 22 INFO each; OTA checks pass; evidence labelled "G1 PCM ingress fix". BUILD only - nothing booted.

## 1. Commands
```bash
export HIFI_LAB=$PWD/.lab                       # a git worktree points this at the main checkout's .lab
scripts/os_build.sh rpi3_64                     # configure + make + legal-info + SBOM (one board at a time; --cpus 3, 8 GB, BR2_JLEVEL 3)
scripts/os_check_image.sh rpi3_64               # image checks + secret scan -> reports/evidence/os_image_rpi3_64.md (+ SBOM, licences, legal manifest)
scripts/os_check_ota.sh rpi3_64                 # `make ota` bundle of the built rootfs, checked with the image's own keyring
python3 os/tools/size_plan.py --board rpi3_64=.lab/br-out/rpi3_64/hifi/check/image-check.json:<bundle> ... --md reports/evidence/os_size_plan.md
HIFI_OS_OUT=$HIFI_LAB/br-out/rpi3_64-repro HIFI_OS_CCACHE=0 scripts/os_build.sh rpi3_64 clean build    # reproducibility rebuild
scripts/os_build.sh rpi3_64 retarget build legal sbom   # fresh target tree without recompiling (after removing a package, P2)
HIFI_EVIDENCE_LABEL="..." scripts/os_check_image.sh rpi3_64   # label line in every evidence file of the check
scripts/os_source_bundle.sh rpi3_64             # source-bundle preparation under .lab/source-bundle/ (never published; IMAGE_LICENSING.md 6)
python3 os/tools/tree_manifest.py compare <A>/hifi/check/rootfs-manifest.tsv <B>/hifi/check/rootfs-manifest.tsv
```
Incremental: re-running `os_build.sh <board>` re-syncs the bridge sources and re-runs post-build, rootfs and post-image (minutes).
After changing an already built package's configuration: `HIFI_OS_REBUILD="pkg ..."`; after removing a package: `retarget` (P2) or
`clean`.

## 2. Build results (host: 4 CPUs shared with other work; container capped at 3 CPUs / 8 GB; shared download cache + ccache)
| Board | Result | `make` time | Notes |
|---|---|---|---|
| rpi3_64 | **BUILD-VERIFIED** | 8460 s (first, cold: downloads + empty ccache; stopped in post-image, see §7) · 3631 s clean with warm ccache | |
| rpizero2w_64 | **BUILD-VERIFIED** | 3645 s (clean, warm ccache from the Pi 3 B+: same Cortex-A53 flags, same kernel) | |
| rpi4_64 | **BUILD-VERIFIED** | 7485 s (clean; Cortex-A72 flags, so few ccache hits) | |
| incremental (bridge/post-build/image only) | | 147–180 s | |
| rpi3_64 incremental after 7b-2 (`HIFI_OS_REBUILD="uboot host-uboot-tools"`: U-Boot reconfigured with the fragment, boot.scr regenerated) | **BUILD-VERIFIED** | 283 s (configure 24 s + make 259 s) | |
| P2 `retarget build legal sbom` (fresh target, libubootenv built, uboot-tools gone, firmware pruned) | **BUILD-VERIFIED** | rpi3_64 542 s (+ 153 s incremental after fixing the file list, section 10) · rpizero2w_64 627 s · rpi4_64 607 s (both with `HIFI_OS_REBUILD="uboot host-uboot-tools"`: first rebuild since package 6) | legal-info 59-69 s, SBOM 9-23 s |

`make legal-info` 61–76 s (965 MB per board, not committed), SBOM 12–25 s. Download cache 791 MB, ccache 1.1 GB, one output directory ~9 GB.

## 3. What the image check verifies (all three boards: 0 FAIL, 89 PASS, 20 INFO; reports/evidence/os_image_<board>.md)
* *Since P2 (all three boards rebuilt: 0 FAIL, 128 PASS, 21 INFO):* `boot.uboot-env-linux-default`, `boot.uboot-builtin-env`,
  `boot.uboot-env-corruption-matrix` (13 SIMULATED cases with the image's libubootenv; table in the report) + INFO comparison with the
  previous uboot-tools, `rauc.env-tool-libubootenv`, `rauc.env-tool-single`; `firmware.boot-files`, `firmware.radio-list(-present)`,
  `firmware.no-hidden-synaptics-bt`, `boot.firmware-licences` (now exact per board), `boot.firmware-notice` (section 10). The former
  `boot.uboot-env-torn-copy-fallback` (uboot-tools host build) is replaced by the matrix.
* *Added in package 7b-2 (rpi3_64 rebuilt: 0 FAIL, 103 PASS, 20 INFO):* both U-Boot environment copies (present, CRC, serials 1/0, fresh-card
  slot state, complete default environment), U-Boot `CONFIG_ENV_REDUNDANT` = fw_env.config, torn-copy fallback with the build's own
  `fw_printenv`/`fw_setenv`, boot.scr fail-safe; root-owned boot FAT, group `hifi-boot` without members, only the boot step writes it;
  growdata without a guard below `/data`; discovery firewall rule; resolved without LLMNR/mDNS (section 9).
* *Added after the evidence builds:* `network.dhcp-duid-link-layer` (networkd DUID from the MAC, OPEN_QUESTIONS #77) - a rebuild reports 90 checks.
* **Identity:** development variant in os-release and build-info.json; RAUC compatible `hifi-bridge-rpi3|rpizero2w|rpi4`.
* **Layout:** MBR, boot FAT 64 MiB | rootfs-A 256 MiB | rootfs-B 256 MiB (all zero on a fresh card) | data 64 MiB (grows on first boot)
  - package-6 images; since P1: 64 | 384 | 384 | 256 MiB (`size.layout`, docs/PERSISTENCE.md);
  every partition on a 4 MiB boundary; slot A = `rootfs.ext4`; manifest size/sha256 and the provisioning-slot offset match the image.
* **Boot FAT:** firmware (`bootcode.bin`/`start.elf`/`fixup.dat`, Pi 4: `start4.elf`/`fixup4.dat`), DTBs, 358 overlays, `u-boot.bin`,
  `boot.scr` (bootargs contain `ro`), `config.txt` with `kernel=u-boot.bin` and no `miniuart-bt`, the 4096-byte empty provisioning slot,
  `licenses/` with the firmware licences (docs/IMAGE_LICENSING.md); since 7b-2 both U-Boot environment copies `uboot.env` and
  `uboot-redund.env` ship (section 9; before: no `uboot.env`).
* **Read-only root:** fstab `/dev/root / auto ro`, `/var` = overlayfs over tmpfs (Buildroot `VAR_OVERLAYFS`), bind mounts from `/data` for
  `/var/lib/hifi-bridge`, `/var/lib/bluetooth`, `/var/lib/systemd/timesync`; rootfs ext4 with 4 KiB blocks and **no journal**; kernel
  (`/boot/Image`) and modules inside the slot; `/etc/machine-id` empty (not shared by all devices; see §8).
* **RAUC:** `system.conf` (U-Boot backend, slots `/dev/mmcblk0p2/3`, `check-purpose=codesign`), keyring = exactly the public development
  CA, `update-trust.json` (`own`, `official_source: null`), `fw_env.config` = U-Boot's `CONFIG_ENV_SIZE=0x4000`, `uboot.env` on FAT
  `mmc 0:1`; RAUC D-Bus policy for the bridge user; `rauc-mark-good` masked (only `hifi-bridge-health.service` marks a slot good).
* **Units:** `systemd-analyze verify --root` of all 12 `hifi-*` units (templates as `@wlan0`): no message about our units (the only
  messages are newer systemd-258 keys in stock units that the Debian 257 checker does not know). Enabled at boot: hifi-bridge(-boot,
  -data, -growdata, -health, -reboot.path), hifi-firewall, hifi-netd; never at boot: `hifi-net-wpa@/hostapd@/dhcp@` (started by
  hifi-netd). Stock `wpa_supplicant.service` and `avahi-dnsconfd.service` are masked (§7). Offline exposure: hifi-bridge 1.9, -boot 0.6,
  -netd 3.0, -health 1.5.
* **Network/firewall:** base nftables ruleset (default-drop input/forward) loaded by `hifi-firewall.service` before networking,
  nf_tables in the kernel, modules-load entry, regulatory database, 81 Wi-Fi/Bluetooth firmware files.
* **Users/secrets:** all accounts locked (`*`/`!`), `/etc/shadow` 0600 root, no SSH/telnet daemon, 0 setuid/setgid files; the secret scan
  finds nothing in the rootfs (6073–6079 files), the boot FAT and the **raw card image** (675 MB incl. free space).
* **Update bundle (`scripts/os_check_ota.sh`):** `make ota` with the development signing key; `rauc info` with the keyring and
  system.conf read out of the image accepts it, its compatible equals the image's, a bundle signed by a foreign CA is refused.

Facts recorded as INFO for other streams: PipeWire is installed with **system-wide** units (`pipewire.service` as user `pipewire`,
socket-activated) and also user units; whether the bridge uses the system instance is the audio stream's open question
(docs/SERVICE_SUPERVISION.md).

## 4. Sizes (measured; reports/evidence/os_size_plan.md)
| Board | rootfs used (ext4) | files in rootfs | kernel + modules | Wi-Fi/BT firmware | Python stdlib | bridge | boot FAT files | dev bundle | card image |
|---|---|---|---|---|---|---|---|---|---|
| rpi3_64 | 185.1 MiB | 156.8 MiB | 46.6 MiB | 2.8 MiB | 15.1 MiB | 1.9 MiB | 4.6 MiB | 73.6 MiB | 644 MiB |
| rpizero2w_64 | 185.0 MiB | 156.7 MiB | 46.6 MiB | 2.8 MiB | 15.1 MiB | 1.9 MiB | 4.5 MiB | 73.6 MiB | 644 MiB |
| rpi4_64 | 185.2 MiB | 156.9 MiB | 46.8 MiB | 2.8 MiB | 15.1 MiB | 1.9 MiB | 3.9 MiB | 73.7 MiB | 644 MiB |

Largest packages (rpi3_64): linux 46.7 MiB (kernel 23.2 + modules 23.4 of the generic `bcm2711` defconfig), systemd 20.8, python3 10.4,
libopenssl 7.8, pipewire 6.7, util-linux 5.8, toolchain runtime 5.4, glib 4.5. "rootfs used" minus "files" (~28 MiB) is ext4 metadata,
mostly the 16 MiB inode table for 65536 inodes (6.9 k used). The earlier estimate of 100–150 MB per slot (PHASE2B_PLAN §3) was low.

The table above is from the package-6 builds (256 MiB slots). **Decided layout (DECISIONS #120, implemented in P1; numbers in
`os/storage.json`, derivation and consequences in docs/PERSISTENCE.md sections 2 and 8):** boot FAT 64 MiB, slots 384 MiB, data 256 MiB
before growth (one bundle of a full slot + free-space margin + state; measured 225.8 MiB available to the bridge), card image 1092 MiB →
fits a nominal 2 GB card (technical target; 16 GB recommended). rpi3_64 rebuilt with it: rootfs 193.3 MiB used of 384 (more inode tables
in the bigger slot), bundle unchanged at 73.6 MiB, card image 1092 MiB apparent / 192 MiB on disk (sparse). `os/tools/size_plan.py` now
checks the measured builds against this layout (`reports/evidence/os_size_plan.md`). The earlier proposal (minimum 224/16/144 MiB → 1 GB,
recommended 384/64/448 MiB) is superseded. Possible savings if ever needed: a trimmed kernel configuration (modules are 23 MiB), fewer
inodes in the rootfs, dropping the 358 overlays (unused by our config.txt, OPEN_QUESTIONS #95); per-board firmware pruning is done since P2
(-1.7 MiB per board, section 10).

## 5. Reproducibility (G2 P1-1, software part)
Settings: `BR2_REPRODUCIBLE` (SOURCE_DATE_EPOCH = commit date of the pinned Buildroot, TZ=UTC, LANG=C), the same container paths for
every build (`/out`, `/work`, `/buildroot`), fixed ext4 UUIDs and hash seeds (rootfs and data), genimage `fs-timestamp` for the data
partition, boot inputs stamped with SOURCE_DATE_EPOCH, `mkfs.vfat --invariant`, MBR disk signature 0 (genimage default), bridge bytecode
as `unchecked-hash` pycs (no time stamps).

**Experiment:** rpi3_64 built twice from scratch in different output directories on the host (same container path `/out`): build A
with the shared ccache (and later incremental runs), build B clean **without ccache** (`HIFI_OS_CCACHE=0`, 6980 s). Compared with
`tree_manifest.py` (path, type, mode, owner, size, SHA-256 of every file) and SHA-256 of each partition:

| Part | Result |
|---|---|
| boot FAT (377 entries; partition image) | **identical** (after the fix below) |
| data partition (ext4 image) | **identical** |
| slot B | identical (all zero) |
| rootfs files | **7394 of 7398 identical**, incl. kernel `Image`, all modules, Python, systemd, the bridge's bytecode, every configuration file |
| 4 rootfs files differ | `libwireplumber-0.5.so`, two WirePlumber modules, `pylibmount.so`: only strings of the form `__FILE__` (assertion messages from headers) differ: `../../../host/…/spa/pod/iter.h` in the ccache build vs. `buildroot/host/…` without ccache. ccache's base-dir rewriting turns absolute include paths into relative ones before Buildroot's `-ffile-prefix-map` can apply. A ccache artefact, not randomness. |
| rootfs.ext4 / card image | differ only because of those 4 files |

Also: the Zero 2 W build (independent, fresh output directory) has bit-identical rootfs files to the Pi 3 B+ build except the expected
board files (its DTB, no `eth0.network`, `system.conf`, `os-release`, `build-info.json`).

**P2 repetition (2026-11-20, code commit `6030a84`):** build A = `rpi3_64` (ccache, incremental `retarget` build of section 10), build B
= `rpi3_64-repro` rebuilt from scratch in the same directory as before (`clean`, **no ccache**, 6426 s). Result identical in kind to
package 6: **7347 rootfs entries, 0 added, 0 removed, 4 changed** - the same four ccache-affected files (`libwireplumber-0.5.so.0.510.0`,
two WirePlumber modules, `pylibmount.so`); **boot FAT partition and data partition bit-identical** (sha256 `0ac0c398…` / `0d8a1f2c…`);
rootfs.ext4 and the card image differ only through those files. So the `retarget` path (fresh target, packages reinstalled without
recompiling) gives the same files as a clean build, and the per-board firmware pruning and libubootenv are reproducible. Two ccache-free
builds against each other are still not shown.

**What is and is not bit-reproducible, and why:**
* Reproducible: every file of the rootfs and the boot FAT, the boot FAT image, the data partition, the MBR; the rootfs and card image
  **when the ccache setting is the same** (consequence: build releases **without ccache**, as CI would).
* Not shown: two complete ccache-free builds against each other (a third 2-hour build; not run). Not covered: a different host or a
  different output path (Buildroot only promises identical results for the same `O=` path, which the container mount keeps fixed).
* Not reproducible by design: the RAUC bundle's dm-verity salt and CMS signing time (docs/OTA.md); the flash manifest embeds the image hash
  and is reproducible with the image. Development images contain the temporary development CA, so each developer's image differs in
  `/etc/rauc/keyring.pem` and `update-trust.json`.

Fixes found by this experiment: boot FAT file times, volume serial and label time; data ext4 time stamps; rootfs/data UUIDs and hash
seeds (all listed in §7).

## 6. Licences and SBOM
* Licence policy (`os/tools/license_report.py` over `make show-info`): **52 target packages per board, 0 to review** (reviewed exceptions
  listed in the reports). Firmware licences: docs/IMAGE_LICENSING.md.
* **SBOM:** CycloneDX 1.6 per board from Buildroot's `utils/generate-cyclonedx` (120 components, 26 vulnerability entries = Buildroot's
  list of CVEs it patches or ignores): `reports/evidence/os_sbom_<board>.cdx.json`. The legal-info manifest (`manifest.csv`, package,
  version, licence, licence files, source, URL) is committed as `reports/evidence/os_legal_manifest_<board>.csv`.
* `make legal-info` completes for every board. Its two warnings are release (G4) items: the Buildroot source itself is not saved
  (Buildroot is GPL-2.0+; a release must add its source), and the Bootlin toolchain has no licence files defined (the glibc/GCC runtime
  licence texts must be added by hand). The custom kernel tarball's licence files have no hashes (Buildroot "No hash found" messages;
  informational).

## 7. Problems found by the real builds and fixed
| Problem | Fix |
|---|---|
| `post-image.sh` sized slot B from `rootfs.ext4`, which is a **symlink** in a real build (11 bytes): genimage refused | `stat -L` |
| post-image runs `btbridge.provisioning` under Buildroot's host Python, which had no `_ssl` (the merged `btbridge.config` imports `ssl`) | `BR2_PACKAGE_HOST_PYTHON3_SSL=y` |
| `update-trust.json` was **invalid JSON** whenever a CA subject contains a comma (RFC 2253 writes `\,`) | escape backslashes; test with comma + quote |
| stock `wpa_supplicant.service` (D-Bus mode, root) enabled by Buildroot's preset next to hifi-netd's own supplicant | masked |
| stock `avahi-dnsconfd.service` enabled: would take DNS servers announced over mDNS by any LAN host | masked |
| partitions started at sector 1 (no erase-block alignment) | genimage `align = 4M` |
| rootfs: 1 KiB blocks (mke2fs default below 512 MiB) and a 32 MiB journal on a read-only file system | `-b 4096`, `^has_journal` |
| the root file system was remounted read-write (Buildroot default), no `/var` strategy, kernel not in the slot (boot.cmd loads it from there) | `# BR2_TARGET_GENERIC_REMOUNT_ROOTFS_RW is not set`, `VAR_OVERLAYFS`, `BR2_LINUX_KERNEL_INSTALL_TARGET` |
| random ext4 UUIDs/hash seeds and genimage time stamps made every card different | fixed UUIDs/seeds, `fs-timestamp` from `SOURCE_DATE_EPOCH`, `BR2_REPRODUCIBLE` |
| two clean builds: identical boot files but different FAT images (mtimes, volume serial, label time) | boot inputs stamped with `SOURCE_DATE_EPOCH`, `mkfs.vfat --invariant` |
| secret scan: `psk = net.get("psk")` in Python source matched the Wi-Fi passphrase rule | rule limited to configuration files |

## 8. Not verified (HARDWARE-PENDING) and open points
* Boot of any board (firmware → U-Boot → kernel from slot A → multi-user), boot time, RAM/CPU (G2 P1-2, P4-4).
* U-Boot really reading/writing both environment copies on the FAT and agreeing with `fw_printenv` (P3-1); power cuts during
  `saveenv`/`fw_setenv` (P3-5/P3-6); the root-owned boot FAT at runtime (P6-3a); RAUC install, slot switch, rollback,
  power cuts (P3-*); data partition growth (P4-1); read-only root and persistence at runtime (P4-2); provisioning on real vfat (P6-3).
* Radio firmware loading and Bluetooth on PL011 without `miniuart-bt` (P1-3).
* `/etc/machine-id` is empty on a read-only `/etc`: systemd then uses a **new transient id on every boot** (journal, DHCP client
  identity via networkd's DUID). Whether the DHCP address stays stable must be checked on a device (network stream; option: a persistent
  id from `/data` or `ClientIdentifier=mac`).

## 9. Boot robustness and boot-partition permissions (package 7b-2, review N-6/N-8/N-13/N-14)
BUILD-VERIFIED on rpi3_64 only; nothing booted.
* **Two U-Boot environment copies (N-6).** U-Boot 2026.01 (`rpi_arm64`) supports a redundant FAT environment: `CONFIG_ENV_REDUNDANT` +
  `CONFIG_ENV_FAT_FILE_REDUND` (`env/fat.c`: `saveenv` writes the file that was NOT loaded; `env_check_redund` takes the valid copy with
  the higher serial, 255→0 wrap handled). uboot-tools 2025.10 `fw_env` (image until P2; since P2 libubootenv 0.3.7 + patch, section 10) and libubootenv (Debian, OTA tests) treat two regular files
  in `fw_env.config` the same way (incremental serial). Configuration: `board/hifi-bridge/uboot/uboot.fragment`
  (`BR2_TARGET_UBOOT_CONFIG_FRAGMENT_FILES`), `uboot/fw_env.config`. post-image writes both copies: U-Boot's default environment
  (`u-boot-initial-env`, `BR2_TARGET_UBOOT_INITIAL_ENV=y`, also installed as `/etc/u-boot-initial-env`) + `uboot/uboot-env.txt`, serials
  1/0 (mkenvimage `-r`). Why ship them: fw_setenv refuses to work while one file is missing (checked with both tools), and a valid copy
  replaces U-Boot's built-in defaults completely, so it must contain them (`bootcmd=bootflow scan` etc.; the image check compares all 25).
  `CONFIG_ENV_APPEND`/`ENV_WRITEABLE_LIST` were considered and rejected: APPEND alone does not pre-load the defaults in 2026.01
  (`env_load`), WRITEABLE_LIST forbids `setenv` of unlisted variables at runtime (boot.cmd/bootstd need it).
* **Fail-safe boot script.** Without `hifi_env` (U-Boot on its built-in defaults = both copies lost) A then B get three attempts each and
  the boot is marked `hifi.bootenv=reset`; `panic=10` in bootargs. Residual risks: docs/OTA.md section 4.
* **Root-owned boot FAT (N-14).** `/boot` vfat `uid=0,gid=991,fmask=0117,dmask=0007`; group `hifi-boot` (gid 991, no members) from
  `hifi-bridge.mk`; only `hifi-bridge-boot.service` joins it (drop-in `hifi-bridge-boot.service.d/boot-fat.conf`). vfat has one
  owner/mode for all files, and write access to the directory allows replacing any file, so per-file protection is impossible; the group
  confines write access to the one unit that needs it, without a capability (exposure stays 0.6).
* **growdata (N-13)** runs on every boot without a guard (idempotent); **resolved (N-8)** without LLMNR/mDNS responders; the discovery
  firewall rule is `udp sport 3483 udp dport 3484` (docs/NETWORK.md section 6).
* post-build now replaces an fstab entry of an earlier (incremental) build instead of appending a second one for the same mount point.

## 10. Final polish P2: U-Boot environment, firmware per board, source bundle, rebuild of all boards (2026-11-20)
BUILD/SIMULATED/STATIC only; nothing booted.
* **U-Boot environment** (docs/OTA.md 4.1, DECISIONS #133): target `fw_printenv`/`fw_setenv` = libubootenv 0.3.7 + our patch
  (`os/br2-external/patches/libubootenv/`, applied by Buildroot: "Applying 0001-uboot_env-use-the-other-copy..." in the build log);
  `/etc/u-boot-initial-env` = U-Boot's 25 default variables + `BOOT_ORDER=A B`, `BOOT_A_LEFT=3`, `BOOT_B_LEFT=3` (28 lines, no
  `hifi_env`); target uboot-tools removed (`rauc.env-tool-single`: no uboot-tools file, fw_printenv owned by libubootenv). U-Boot side
  unchanged: `CONFIG_ENV_REDUNDANT`, both copies shipped, U-Boot's built-in default = the same 25 variables (found as strings in
  `u-boot.bin`), `boot.cmd` fail-safe. Corruption matrix on each image's own copies: 13/13 as intended; the comparison with the previous
  images' uboot-tools 2025.10 host build stored `bootcmd=bootp; setenv bootargs root=/dev/nfs ...` with both copies bad and did not finish
  (killed after 15 s) on every truncated copy.
* **Firmware per board** (docs/IMAGE_LICENSING.md 5, DECISIONS #134): post-build "20 entries kept, 70 removed" (rpi3_64), "21 kept, 69
  removed" (rpizero2w_64), "26 kept, 64 removed" (rpi4_64); radio firmware 1.1 / 0.9 / 1.2 MiB instead of 2.8 MiB (incl. the regulatory
  database); the Synaptics tool licence only on the Zero 2 W card. Boot firmware unchanged (`firmware.boot-files`).
* **Results** (reports/evidence/os_image_<board>.md, os_ota_<board>.txt, os_secret_scan_<board>.json, os_sbom_<board>.cdx.json,
  os_size_plan.md):

| Board | image check | secret scan | OTA check, bundle | rootfs used / 384 MiB | boot FAT files | card image |
|---|---|---|---|---|---|---|
| rpi3_64 | 0 FAIL, 128 PASS, 21 INFO | 0 findings (6073 files + raw card) | pass, 76 056 010 B (72.5 MiB) | 191.6 MiB | 4.6 MiB | 1092 MiB |
| rpizero2w_64 | 0 FAIL, 128 PASS, 21 INFO | 0 findings (6069 files + raw card) | pass, 75 945 422 B (72.4 MiB) | 191.4 MiB | 4.5 MiB | 1092 MiB |
| rpi4_64 | 0 FAIL, 128 PASS, 21 INFO | 0 findings (6074 files + raw card) | pass, 76 289 483 B (72.8 MiB) | 191.9 MiB | 4.0 MiB | 1092 MiB |

* **Licence policy:** 53 target packages per board, 0 to review (libubootenv LGPL-2.1+/MIT/CC0-1.0 and libyaml MIT added, uboot-tools
  removed).
* **Found by the rebuild:** after `retarget` the first rpi3_64 image check failed `rauc.env-tool-single`: Buildroot's
  `packages-file-list.txt` is the concatenation of every `build/*/.files-list.txt`, so the removed package's old list was still in it
  (the target tree itself was clean). `retarget` now deletes those lists too; rpi3_64 was rebuilt incrementally, the other boards used the
  fixed step.
* **Source bundle** (rpi3_64, commit `6030a84`): `.lab/source-bundle/rpi3_64-6030a842d1f5/` - legal-info (512 files, hard links),
  Buildroot source 7.2 MB, repository 10.3 MB, raspberrypi/linux@957fc9f9 246.9 MB (contains the 358 `*-overlay.dts` sources), toolchain
  metadata + LGPL-2.1/GPL-3.0/GCC Runtime Library Exception texts + 21 toolchain runtime files of the image listed, MANIFEST.json and
  SHA256SUMS; 1.2 GB apparent (legal-info hard-linked). Not published.
* **Reproducibility:** clean ccache-free rebuild of rpi3_64 vs. the retarget build: only the 4 known ccache files differ, boot FAT and
  data partition bit-identical (section 5, "P2 repetition").
