# Hardware gate checklist

**Status: prepared, NOTHING executed.** No item below has been run. The development machine has no Pi, Bluetooth, audio or card-writer
hardware, and none of these tests may run on it (CLAUDE.md). Every result must be recorded as **REAL-HARDWARE** with the device, OS and
firmware versions. Simulated or Docker results never tick a box here.

The gates are in order. A gate starts only when the previous one is passed **and** the owner has approved it.

| Gate | Question | Needs | Decides |
|---|---|---|---|
| **G1 Audio on stock Pi OS** | Does the bridge work as a Bluetooth speaker with real phones and real players? | test Pi, phones, player, `hwkit` | whether phase 2b is worth building; fusion, capture and volume tuning |
| **G2 Image feasibility per board** | Does our Buildroot image boot, update A/B, roll back and survive power cuts on each board? | images built in phase 2b step 1/2, three boards | the boot mechanism (P3), file systems and sizes (P4), the supported board list |
| **G3 Flash tool on real computers** | Can owners write and provision cards safely on Windows, macOS and Linux? | GUI + helper (phase 2b step 7), test computers, test cards | release of the flash tool |
| **G4 Before any productive OTA rollout** | Are keys, licences and the update path ready for real users? | G2 + G3 passed | first public release |

---

## G1 – Audio on stock Raspberry Pi OS (procedure: docs/HARDWARE_TEST_PROCEDURE.md, kit: `hwkit/`)
**Prerequisites**
- [ ] Pi 3 B+ (reference), power supply, a 16 GB card (recommended) and a nominal 2 GB card (technical minimum, DECISIONS #120), Ethernet. Optional: Pi Zero 2 W (E5), Pi 4.
- [ ] Android phone **and** iPhone with a music app, a messenger, navigation and YouTube.
- [ ] One target player (Squeezelite on a PC/Pi, or a real Squeezebox: note the model and firmware).
- [ ] A **test** LMS. The production LMS may be observed read-only only (plan §0, F4).
- [ ] USB microphone (E2/F3).
- [ ] A laptop for H1 (Windows or macOS).
- [ ] `scripts/hwkit_bundle.sh` run on the dev machine; the bundle and test tracks copied over.
- [ ] `python3 -m hwkit preflight` without FAIL. It also shows the capture tool (`pw-cat --raw` on PipeWire ≥ 1.x).

**Tests** (criteria in docs/HARDWARE_VALIDATION_PLAN.md)
- [ ] A1–A5 bring-up: adapter, A2DP sink, **capturable phone node**, `node.dont-fallback`, no microphone fallback.
  **Stop if A3/A4 fail.**
- [ ] B1, B2, B3, B4, B6, B7 pairing window, single source, reconnect. **Stop if B4 evicts the first phone.**
- [ ] C1 (Android) and C2 (iPhone) media traces: music vs. notification vs. navigation. This is the main open software risk.
- [ ] D1, D2: absolute volume per phone.
- [ ] E1 (30 min capture), E2 (10 × play/pause with the microphone): the first real timing numbers.
- [ ] F1, F2: each real Squeezebox (compatibility list, ICY decision).
- [ ] G1: guest flow end to end (an ordinary visitor pairs, presses Play, hears music).
- [ ] H1–H3, H4 if time: `hifi-bridge.local` from four OSes, trusted HTTPS, home-screen shortcut, screen reader.
- [ ] **New, this package:** `--boot-dir /boot/firmware` with a hand-made provisioning file. On Pi OS `/boot/firmware` belongs to root:
  run the bridge with write access to it for this item only, or point `--boot-dir` at a copy. Use a **fresh data directory**.
  On the laptop (not the dev machine), from the unpacked bundle:
  1. `PYTHONPATH=bridge:flashtool python3 -m hififlash provision --country DE --out hifi-provision.json --key-out trust.key`, then copy
     the file into the boot directory before starting the bridge;
  2. check that the device applies and overwrites it (`hifi-status.txt` shows "Provisioning applied");
  3. `PYTHONPATH=bridge:flashtool python3 -m hififlash verify-device --url http://<pi>:8080 --key-file trust.key --https https://<pi>:8443`
     must print VERIFIED, the same fingerprint as `hifi-status.txt`, and `HTTP 200`. The HTTPS check needs the Pi's IP or name in its
     certificate.

**Pass:** A1–A5, B4, C1/C2, G1 pass. Everything else is recorded, with known deviations accepted by the owner.

---

## G2 – Image feasibility per board (phase 2b; for each of Pi Zero 2 W, Pi 3 B+, Pi 4)
Each item: **result per board**. A board that fails a mandatory item is **not supported** until it is fixed. Nothing is claimed for a board
that was not tested.

**Boot and layout (P1, P4)**
- [ ] P1-1 The image from `hifi_bridge_<board>_defconfig` builds reproducibly in Docker. Two builds give the same rootfs hash, or the
  differences are explained.
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* all three boards build in Docker; two clean rpi3_64 builds (with and without ccache) compared file by file, see docs/IMAGE_BUILD.md §5.
  *Repeated in final polish P2 (BUILD-VERIFIED):* clean ccache-free rpi3_64 build vs. the incremental P2 build: the same 4 ccache files differ, boot FAT and data partition bit-identical (docs/IMAGE_BUILD.md §5).
- [ ] P1-2 Firmware → U-Boot → kernel from slot A → `multi-user.target` (serial or HDMI log). Measure the time to the WebUI.
- [ ] P1-3 `config.txt` without `miniuart-bt`: Bluetooth on PL011 is stable during 30 min of A2DP (repeat E1). Compare with
  `miniuart-bt` if problems occur.
- [ ] P1-4 **Per-board firmware (DECISIONS #134):** on each board (and each Zero 2 W revision at hand: radio chip rev A1 and B0) the
  kernel log shows Wi-Fi and Bluetooth firmware loaded from the board's list (`dmesg | grep -E "brcmfmac|Bluetooth: hci0|BCM"`: file name,
  no "Direct firmware load ... failed" for a file that the list does not ship and the driver did not fall back from). Record the chip
  revision and the loaded names; compare with `os/br2-external/board/hifi-bridge/firmware/radio-<compatible>.txt` and with stock Raspberry
  Pi OS on the same board (G1). Pi 3 B, Pi 400 and CM4 are not target boards; their files stay in the images until OPEN_QUESTIONS #94 is decided.
- [ ] P4-1 Partitions as designed (boot FAT, A, B, data). `hifi-bridge-growdata` grows data to the card size once; `x-systemd.growfs`
  grows ext4; the second boot does nothing. (`hifi-bridge-growdata` runs on every boot without a guard since 7b-2 and only reads the
  partition table once the card is filled.)
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* the built cards have this layout (MBR, 4 MiB aligned, slot B empty); growth itself is HARDWARE-PENDING.
  Since P1 of the final polish (DECISIONS #120): boot 64 | A 384 | B 384 | data 256 MiB (1092 MiB image). Run it on a **nominal 2 GB
  card** (data grows to ~880 MiB) and on a 16 GB card; on both, an update upload of a full-size bundle must be accepted.
- [ ] P4-2 Rootfs is read-only; `/var/lib/hifi-bridge` and `/var/lib/bluetooth` persist across reboots (pairings survive).
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* fstab `ro`, bootargs `ro`, /var overlay on tmpfs, bind mounts from /data verified in the images; runtime behaviour HARDWARE-PENDING.
- [ ] P4-3 Measured sizes (rootfs used/free, boot, data after setup) replace the estimates in PHASE2B_PLAN §3. Minimum card size and
  recommendation decided.
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* measured per board (rootfs 185 MiB used, bundle 74 MiB); layout
  **decided by the owner (DECISIONS #120)**: 64/384/384 MiB + data 256 MiB before growth (derivation docs/PERSISTENCE.md 2.1), 2 GB target,
  16 GB recommended; data after setup needs a device (expected < 1 MiB plus bonds).
- [ ] P4-5 Persistence rule (DECISIONS #121, docs/PERSISTENCE.md): after a day of use, `journalctl --header` shows only a runtime journal
  (`/run/log/journal`), `/var/log/journal` does not exist, `find /data -newer <boot marker>` lists only allow-listed paths
  (`os/storage.json`), `/proc/sys/kernel/core_pattern` is `|/bin/false`; after a power cut during an upload the next boot leaves no
  `ota/*.upload`/`*.part`. Optional: the card's write counter / `/sys/block/mmcblk0/stat` writes per hour at idle and while playing.
  *Software evidence (STATIC/UNIT/BUILD, P1):* image checks `persist.*`, `tests/os/test_persistence_layout.py`, `tests/unit/test_persistence*.py`.
- [ ] P4-4 RAM/CPU at idle and while playing (Zero 2 W: 512 MB) with BlueZ, PipeWire, WirePlumber, Avahi and RAUC running.

**A/B updates and rollback (P3 – provisional U-Boot boot-count)**
- [ ] P3-1 `fw_printenv`/`fw_setenv` read and write **both** environment copies `uboot.env` + `uboot-redund.env` on the boot FAT, and U-Boot
  agrees (console `Loading Environment from FAT... OK`, no "bad CRC"); U-Boot's `saveenv` alternates between the copies (serial byte rises)
  (check `CONFIG_ENV_SIZE`/offset of `rpi_arm64`; adjust `/etc/fw_env.config`).
  *Software evidence (BUILD-VERIFIED, package 7b-2, not a hardware result):* rpi3_64 U-Boot built with `CONFIG_ENV_REDUNDANT=y`, both copies on the FAT (CRC ok, serials 1/0, complete default environment); the build's fw_printenv/fw_setenv and real RAUC 1.13 + libubootenv alternate between the copies and fall back to the older one when the newest is torn.
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* U-Boot `rpi_arm64` build: `CONFIG_ENV_SIZE=0x4000`, `uboot.env` on FAT `mmc 0:1` = `/etc/fw_env.config`; reading/writing on a board is pending.
  *Software evidence (SIMULATED + BUILD-VERIFIED, final polish P2, not a hardware result):* the image's fw_printenv/fw_setenv are
  libubootenv 0.3.7 + our patch; 13 corruption cases on the image's own copies (docs/OTA.md 4.1, image check `boot.uboot-env-corruption-matrix`).
  Also check on the device: `fw_printenv` output = U-Boot `printenv` (same variables, same values), `fw_printenv -V` prints `0.3.7`.
- [ ] P3-2 A fresh card boots slot A; slot B (empty) is **never** tried (`BOOT_B_LEFT=0`).
- [ ] P3-3 `rauc install` (development-signed bundle) writes slot B, switches, boots B and marks it good (`rauc status`).
- [ ] P3-4 A deliberately broken B (kernel missing) is tried 3 times, then A boots again automatically.
- [ ] P3-5 **Power cut during `rauc install`, 10× at random points:** every time the device boots a good slot. Include cuts during the
  activation at the end (RAUC writes the environment twice) and right after "Restart now".
- [ ] P3-6 Power cut during the boot-count write (right after power-on, 10×): the device boots **the same slot as before**, the console
  shows the other copy being used (never "using default environment"). Then delete both copies on a computer: the device boots A (then B
  after three failures) and `/proc/cmdline` contains `hifi.bootenv=reset`. A slot with a damaged root file system panics and restarts
  after 10 s (`panic=10`) until the counter falls back.
- [ ] P3-12 **Both environment copies damaged while Linux runs** (DECISIONS #133): overwrite both copies on the running device with
  garbage (as root, `dd` over the files on `/boot`), then `rauc status mark-good`: `fw_printenv` shows `bootcmd=bootflow scan` and
  `BOOT_ORDER=A B` (never `bootcmd=bootp`), one copy is rewritten, the device reboots into A with `hifi.bootenv=reset` on `/proc/cmdline`,
  and B gets its three attempts only after A fails. Repeat with the files deleted: `fw_setenv` fails cleanly, nothing is written, the
  next boot's `saveenv` recreates the missing file.
- [ ] P3-13 **Truncated copy on a real FAT:** truncate the newest copy to 8 KiB on a computer (or cut the power during U-Boot's `saveenv`
  until a short copy is observed): U-Boot prints "some problems detected reading environment; recovered successfully" and boots the
  previous slot state; Linux `fw_printenv` reads the same state (patched libubootenv) and the next `fw_setenv` rewrites the short copy to
  16 KiB. Note whether vfat ever leaves a short file at all, or only a full-length torn one (FAT driver write order).
- [ ] P3-14 **Card moved to another board (OPEN_QUESTIONS #82):** a card first booted in a Pi 3 B+ and then in a second Pi 3 B+ (and, for
  the knowledge, a Zero 2 W card in a Pi 3 B+ although it is the wrong image): record `serial#`, `ethaddr`/`usbethaddr` and `fdtfile` in
  `fw_printenv` and on the console; check whether the Ethernet MAC, the hostname/mDNS name and the DTB still match the new board. Decide
  #82 from the result.
- [ ] P3-7 Firmware/DTB on the shared boot partition: decide and test how they are updated (not A/B today, see PHASE2B_PLAN risks).
- [ ] P3-8 Alternative per board if U-Boot fails: firmware `tryboot`/`autoboot.txt` (Pi 4 first). Record which mechanism each board uses.
- [ ] P3-9 The WebUI flow end to end (docs/OTA.md): upload → install with progress → "Restart now" → `hifi-bridge-health.service` marks B
  good → "Update completed". A bridge that fails in B (e.g. `/healthz` 503) → `mark-bad` + `FailureAction=reboot` → A, page shows
  "rolled back". Time from "Restart now" until the page is back.
- [ ] P3-10 A bundle whose payload was changed after signing passes `InspectBundle` (verified in Docker) but is refused by dm-verity
  during `rauc install` on the device; the device stays on its slot.
- [ ] P3-11 On the image: the D-Bus policy (`de.pengutronix.rauc-hifi-bridge.conf`) refuses RAUC to other users; the reboot path unit
  and `/run/hifi-bridge-ota` work; `rauc-mark-good.service` is masked; a full data partition during an upload gives a clear error.

**Update trust (P5)**
- [ ] P5-1 An image built with `official` refuses a development-signed bundle **on the device** (`rauc install` fails with a signature error).
- [ ] P5-2 An image built with `official+own` installs both. An image built with `own` refuses official bundles.
- [ ] P5-3 Installing an official bundle on a self-built image: the device afterwards reports `mode: official` (`update-trust.json`). This is
  the documented consequence.
- [ ] P5-4 A bundle with the wrong `compatible` is refused.

**Provisioning and first setup (P6, P7)**
- [ ] P6-1 Card with provisioning file (hotspot): the hotspot appears with the **individual** password only, WPA2/WPA3, correct country.
- [ ] P6-2 Client Wi-Fi **or** hotspot, never both. On a Wi-Fi failure, the setup fallback appears after ~60 s (PRODUCT_SPEC).
- [ ] P6-3a **Boot FAT permissions (review N-14):** `/boot` is root-owned (`ls -ln /boot`: uid 0, gid 991 `hifi-boot`); the boot step can
  still scrub the provisioning file, remove a processed `hifi-recovery` and write `hifi-status.txt`; a process of the `hifi-bridge` user
  outside that unit (`setpriv --reuid=990 --regid=990 --init-groups touch /boot/x`) gets "Permission denied"; RAUC (root) still writes
  the environment.
- [ ] P6-3 **Residual data:** after first boot, read the raw card on a computer and search for the setup code, the hotspot password and the
  trust key. They must not be found in the FAT file system. Note what a raw image of the whole card still shows (FTL effects are not
  testable without lab equipment; record the limitation).
- [ ] P6-4 Card **without** provisioning file: no hotspot ever; Ethernet works; `hifi-status.txt` shows a one-time setup code.
- [ ] P6-5 Power cut 5× during first boot: the device still ends up provisioned, and the file is overwritten.
- [ ] N-1 … N-11 Network management on the device (client Wi-Fi variants, hotspot with phones, AP-mode scan, WPA3 AP, captive-portal behaviour, regulatory domain, interface names, nftables modules, persistent clock, host name via Avahi, 2.4 GHz coexistence): list and procedure in docs/NETWORK.md section 11.
- [ ] P7-1 Flash-tool trust proof against the real device. Then a phone shows a lock after installing the CA, and the fingerprint matches the
  tool's.

**Recovery (P8)**
- [ ] P8-1 `hifi-recovery` with `reset-owner-password`: a new code appears in `hifi-status.txt`; the old password stops working; settings stay.
- [ ] P8-2 `factory-reset`: a fresh setup works; Bluetooth pairings are removed (decide: should they be?).
- [ ] P8-3 Power cut during each request, 5×: each runs exactly once in effect (no second reset after completion).

**Security on the device**
- [ ] S-1 No root login, no SSH, no open ports except 80/443 (and the stream port). Scan from the LAN.
- [ ] S-2 `systemd-analyze security` on the device matches the static results (1.9 / 0.6), and the watchdog restarts a hung service.
- [ ] S-3 No secret in the image (scan the image for keys and passwords). Only public certificates in `/etc/rauc/keyring.pem`.

---

## G3 – Flash tool on real computers (test computers and test cards, never the dev machine)
- [ ] F-1 Disk list on Windows 11, macOS (Apple Silicon) and Ubuntu: only the SD card is offered; the system disk and USB hard disks are not.
- [ ] F-2 Typed confirmation; swapping the card after selecting is detected; nothing is written.
- [ ] F-3 Write + read-back verify + provisioning on each OS. The card boots (with G2 images).
- [ ] F-4 Cancel and pull the card during writing: the card does not boot a half image. The tool reports it clearly.
- [ ] F-5 A fake-capacity card (if available) fails verification.
- [ ] F-6 Installers signed; privilege elevation only for the write helper; LGPL notices present; DE/EN, light/dark.

---

## G4 – Before any productive OTA rollout or public release
- [ ] K-1 Release CA and signing key created **offline** (hardware token); the public `release-ca.pem` committed; the process documented and
  rehearsed.
- [ ] K-2 Re-signing a CI bundle on the offline machine works end to end; no release key in any CI log, GitHub secret or Actions artifact.
- [ ] K-3 Repository and release visibility decided (OPEN_QUESTIONS #43/#45): official bundles are downloadable without credentials, and
  the corresponding source is available to recipients.
- [ ] L-1 `make legal-info` complete. Review the `brcmfmac_sdio-firmware-rpi` (PROPRIETARY) and Raspberry Pi firmware licence texts.
  Source offer for GPL/LGPL in place.
  *Software evidence (BUILD-VERIFIED, package 6, not a hardware result):* legal-info completes for all boards (warnings: Buildroot source and toolchain licences not saved); licence texts reviewed in docs/IMAGE_LICENSING.md; owner items there.
  *Prepared in final polish P2 (not a release, not legal clearance):* per-board firmware with exactly the licence texts it needs (image
  check `firmware.*`, `boot.firmware-licences`); `scripts/os_source_bundle.sh <board>` assembles legal-info + the Buildroot source + the
  overlay source (`raspberrypi/linux@957fc9f9`) + toolchain licence texts/pointers + our repository with a hashed manifest (docs/IMAGE_LICENSING.md 6).
- [ ] L-3 Owner/legal review of the export-control clause (Broadcom §7.1) and of the revocable Synaptics Bluetooth licence for the Zero 2 W
  image (OPEN_QUESTIONS #73/#74, docs/IMAGE_LICENSING.md 7) before any public download; the source bundle mirrored next to each release.
- [ ] L-2 Project `LICENSE` (AGPL-3.0-only) added; `HIFI_BRIDGE_LICENSE_FILES` set.
- [ ] U-1 The WebUI update upload refuses unsigned and wrongly signed bundles; it installs only after owner confirmation; the previous release
  can be selected.
- [ ] U-2 The update check never installs automatically (PRODUCT_SPEC). It finds a test release on GitHub Releases of `iskaron/hifi-bridge`
  and refuses a bundle that is not signed by the keyring, even from that source.
- [ ] R-1 G2 power-cut tests passed on **every** board that is to be listed as supported.

## Recording
Use `python3 -m hwkit results new/check/report` for G1. For G2–G4, use the same results file with the item IDs above until the kit has a
catalogue for them. Bring back logs, `rauc status` output and photos of failures. Traces become REAL-HARDWARE fixtures.
