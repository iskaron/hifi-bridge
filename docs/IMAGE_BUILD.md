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

## 1. Commands
```bash
export HIFI_LAB=$PWD/.lab                       # a git worktree points this at the main checkout's .lab
scripts/os_build.sh rpi3_64                     # configure + make + legal-info + SBOM (one board at a time; --cpus 3, 8 GB, BR2_JLEVEL 3)
scripts/os_check_image.sh rpi3_64               # image checks + secret scan -> reports/evidence/os_image_rpi3_64.md (+ SBOM, licences, legal manifest)
scripts/os_check_ota.sh rpi3_64                 # `make ota` bundle of the built rootfs, checked with the image's own keyring
python3 os/tools/size_plan.py --board rpi3_64=.lab/br-out/rpi3_64/hifi/check/image-check.json:<bundle> ... --md reports/evidence/os_size_plan.md
HIFI_OS_OUT=$HIFI_LAB/br-out/rpi3_64-repro HIFI_OS_CCACHE=0 scripts/os_build.sh rpi3_64 clean build    # reproducibility rebuild
python3 os/tools/tree_manifest.py compare <A>/hifi/check/rootfs-manifest.tsv <B>/hifi/check/rootfs-manifest.tsv
```
Incremental: re-running `os_build.sh <board>` re-syncs the bridge sources and re-runs post-build, rootfs and post-image (minutes).
After changing an already built package's configuration: `HIFI_OS_REBUILD="pkg ..."`; after removing a package: `clean`.

## 2. Build results (host: 4 CPUs shared with other work; container capped at 3 CPUs / 8 GB; shared download cache + ccache)
| Board | Result | `make` time | Notes |
|---|---|---|---|
| rpi3_64 | **BUILD-VERIFIED** | 8460 s (first, cold: downloads + empty ccache; stopped in post-image, see §7) · 3631 s clean with warm ccache | |
| rpizero2w_64 | **BUILD-VERIFIED** | 3645 s (clean, warm ccache from the Pi 3 B+: same Cortex-A53 flags, same kernel) | |
| rpi4_64 | **BUILD-VERIFIED** | 7485 s (clean; Cortex-A72 flags, so few ccache hits) | |
| incremental (bridge/post-build/image only) | | 147–180 s | |
| rpi3_64 incremental after 7b-2 (`HIFI_OS_REBUILD="uboot host-uboot-tools"`: U-Boot reconfigured with the fragment, boot.scr regenerated) | **BUILD-VERIFIED** | 283 s (configure 24 s + make 259 s) | |

`make legal-info` 61–76 s (965 MB per board, not committed), SBOM 12–25 s. Download cache 791 MB, ccache 1.1 GB, one output directory ~9 GB.

## 3. What the image check verifies (all three boards: 0 FAIL, 89 PASS, 20 INFO; reports/evidence/os_image_<board>.md)
* *Added in package 7b-2 (rpi3_64 rebuilt: 0 FAIL, 103 PASS, 20 INFO):* both U-Boot environment copies (present, CRC, serials 1/0, fresh-card
  slot state, complete default environment), U-Boot `CONFIG_ENV_REDUNDANT` = fw_env.config, torn-copy fallback with the build's own
  `fw_printenv`/`fw_setenv`, boot.scr fail-safe; root-owned boot FAT, group `hifi-boot` without members, only the boot step writes it;
  growdata without a guard below `/data`; discovery firewall rule; resolved without LLMNR/mDNS (section 9).
* *Added after the evidence builds:* `network.dhcp-duid-link-layer` (networkd DUID from the MAC, OPEN_QUESTIONS #77) - a rebuild reports 90 checks.
* **Identity:** development variant in os-release and build-info.json; RAUC compatible `hifi-bridge-rpi3|rpizero2w|rpi4`.
* **Layout:** MBR, boot FAT 64 MiB | rootfs-A 256 MiB | rootfs-B 256 MiB (all zero on a fresh card) | data 64 MiB (grows on first boot);
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

**Recommendation (os/tools/size_plan.py; slots cannot be resized by an update, so the slot size is chosen once):**
| | slot (A = B) | boot FAT | data (min. after growth) | card |
|---|---|---|---|---|
| minimum | 224 MiB (largest used + 15 %) | 16 MiB | 144 MiB (one uploaded bundle + 64 MiB state) | 612 MiB → **1 GB card** |
| **recommended** | **384 MiB** (2 × used, room for later releases) | **64 MiB** (firmware may need old + new during an update) | **448 MiB** | 1284 MiB → **2 GB card** |

The builds still use 256 MiB slots (70 MiB free each). Switching to 384 MiB is a one-line change (`BR2_TARGET_ROOTFS_EXT2_SIZE`) but fixes
the layout for every device ever flashed: owner decision (proposed below). Possible savings if a smaller card is wanted: a trimmed kernel
configuration (modules are 23 MiB), fewer inodes, dropping the 358 overlays (unused by our config.txt), per-board firmware pruning.

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
  the higher serial, 255→0 wrap handled). uboot-tools 2025.10 `fw_env` (image) and libubootenv (Debian, OTA tests) treat two regular files
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
