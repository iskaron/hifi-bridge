# Phase 2b – preparation: architecture decisions, implementation plan, acceptance criteria

**Status:** research and plan only. The decisions in §2 are **proposals P1–P9**, to be confirmed by the owner. **No image, OTA installer or flash tool has been built.** No hardware compatibility is
confirmed: every board statement below is "supported by Buildroot", **not** "works for this product". The plan starts only after the hardware gate
(`docs/HARDWARE_TEST_PROCEDURE.md`) and with the owner's explicit approval.

## 1. Evidence base (what was actually checked)
Source: Buildroot **2026.02.x LTS** branch, commit `679b9ea` (2026-06-16), read with a sparse clone in a disposable container on 2026-11-16.
Versions and licences come from the package `.mk` files, option names from `Config.in`.

| Need | Buildroot package (version) | Licence | Notes |
|---|---|---|---|
| Python runtime | python3 (3.14.5) | Python-2.0 | **The lab runs 3.11** - compatibility tested in Docker with 3.14 (this package, `tests` on python:3.14) |
| D-Bus client for BlueZ/Avahi | python-jeepney (0.9.0) | MIT | lab uses 0.8 |
| Bluetooth | bluez5_utils (5.79) | GPL-2.0+, LGPL-2.1+ | plan A1 still decides on `--experimental` |
| Audio graph | pipewire (1.2.8), wireplumber (0.5.10) | MIT (+LGPL-2.1+ spa-alsa) | `pw-cat` only with libsndfile; **no `parec`** unless the full pulseaudio (17.0, LGPL-2.1+) package is added -> capture defaults to `pw-cat --raw` there (implemented: automatic tool choice) |
| mDNS | avahi (0.8) with AVAHI_DAEMON + LIBAVAHI_CLIENT; dbus (1.14.10) | LGPL-2.1+ / AFL-2.1 or GPL-2.0+ | preferred production mDNS (DECISIONS #49 - review decision); controlled over D-Bus (implemented: avahi backend) |
| TLS/certificates | libopenssl (3.6.2) + `BR2_PACKAGE_LIBOPENSSL_BIN` (openssl CLI) | Apache-2.0 | Apache-2.0 is compatible with (A)GPL-3.0 |
| Updates | rauc (1.15.2, options DBUS/NETWORK/STREAMING/JSON) | LGPL-2.1 | alternatives: swupdate (2025.05, GPL-2.0+ and others), mender-artifact (4.2.0, Apache-2.0) |
| Init | systemd (258.7) or busybox (1.37.0) | LGPL-2.1+ / GPL-2.0 | unit file is ready and verified (`packaging/systemd`) |
| Wi-Fi client / hotspot | wpa_supplicant (2.11), hostapd (2.11), dnsmasq (2.92), iw (6.17) | BSD-3-Clause / GPL | hotspot needs AP mode of the on-board Wi-Fi (hardware test) |
| Boot | rpi-firmware (VARIANT_PI for 3 B+/Zero 2 W, VARIANT_PI4), optional U-Boot | BSD-3-Clause (redistributable binaries) | `RPI_FIRMWARE_EXTRA_FILES` can place `autoboot.txt` |

Board defconfigs that exist: `raspberrypizero2w_64_defconfig` (bcm2710-rpi-zero-2-w), `raspberrypi3_64_defconfig` (bcm2710-rpi-3-b-plus) and
`raspberrypi4_64_defconfig` (bcm2711-rpi-4-b). All three use aarch64, the Raspberry Pi kernel fork, and a 120 MB ext rootfs in the example
configuration.

## 2. Architecture proposals for phase 2b (P1–P9, to confirm)
| # | Decision | Reason | Verify on hardware |
|---|---|---|---|
| P1 | **Buildroot 2026.02.x LTS**, aarch64, one `br2-external` tree with a defconfig per board (zero2w_64, 3_64, 4_64) and a common package set | LTS security updates until 2027-02; one tree, three boards | boot on each board |
| P2 | **Avahi daemon** publishes `hifi-bridge.local` and the services (decision of the review). The bridge controls it over D-Bus (`EntryGroup`); the built-in responder stays as the fallback for Docker or other runtimes | mature conflict handling, IPv6, per interface | resolve from iOS/Android/Windows/macOS |
| P3 | **RAUC** for signed A/B updates (X.509-signed bundles, CMS); **U-Boot** with boot-count/rollback on all three boards | RAUC is in Buildroot, has a D-Bus API, verity bundles and streaming; U-Boot gives a uniform A/B path. The firmware's `tryboot`/`autoboot.txt` is an option where verified | power-loss tests per board (plan) |
| P4 | **Partition layout:** `boot` (FAT, firmware + U-Boot + env), `rootfs-A`, `rootfs-B` (read-only, squashfs or ext4 verity), `data` (ext4, grows on first boot) | read-only root, settings in `data` (`/var/lib/hifi-bridge`) | measured sizes, first-boot growth |
| P5 | **Keys:** offline release signing key (hardware token or air-gapped machine), separate development key; device keyring holds the release CA; key rotation through a signed bundle carrying the new keyring; development images never accept release bundles and vice versa | no online key on build machines | – |
| P6 | **First setup:** the flash tool writes `hifi-provision.json` to the boot partition: setup code, hotspot SSID and **individual** WPA2/3 password, country (regulatory domain), optional Wi-Fi credentials. The device consumes it on first boot, moves secrets into `data`, and **deletes the file**. No shared factory password | DECISIONS #9, PRODUCT_SPEC networking | boot without provisioning file -> hotspot with per-device password printed by the tool |
| P7 | **CA fingerprint out of band:** the device creates its CA on first boot and writes **only the fingerprint** to `hifi-status.txt` on the boot partition; the flash tool and the console show it. The CA private key never leaves the device. Alternative (owner decision): the flash tool generates the CA, so the fingerprint is known at flash time, but the key passes through the host | trust step of LOCAL_ACCESS_TLS.md | – |
| P8 | **Recovery without network:** a file `hifi-recovery` on the boot partition triggers either "reset owner password" (new setup code to `hifi-status.txt`) or "factory reset" (wipe `data`). Physical access to the SD card is the authority; the logic already exists (`python -m btbridge.recovery`) | spec: recovery, no universal credentials | – |
| P9 | **Flash/provisioning tool:** a small cross-platform desktop app. Recommendation: **Python + Qt (PySide6, LGPL)**, or Tauri/Rust. It writes the image with verification (SHA-256), writes the provisioning file, shows the setup code and CA fingerprint, and offers DE/EN. Alternative: Raspberry Pi Imager with a custom OS list plus a separate provisioning step (rejected: no per-device secrets, two tools) | per-device secrets need our own tool | Windows/macOS/Linux installers |

## 3. Size and resource estimates (to be measured; not commitments)
* **Rootfs:**
  * kernel + modules ~30 MB;
  * Python 3.14 (stripped, without tests) ~35–45 MB;
  * BlueZ, PipeWire and WirePlumber ~10 MB;
  * OpenSSL, Avahi, D-Bus and RAUC ~10 MB;
  * bridge code < 2 MB.

  Expected total **~100–150 MB per slot**. The A/B layout needs about 2 × 256 MB slots, a 256 MB boot partition and at least 256 MB data, so
  **≥ 1 GB minimum**. 4 GB stays an optimisation target (PRODUCT_SPEC); the measured image decides.
* **RAM** budget on a Zero 2 W (512 MB): the bridge process with an RSS of ~25–40 MB (to be measured) plus PipeWire/WirePlumber, BlueZ, Avahi and
  RAUC. The systemd unit currently caps the bridge at 256 MB.
* **CPU:** SBC decoding (BlueZ/PipeWire), possible 48→44.1 kHz resampling, PBKDF2 at login (OPEN_QUESTIONS #21), FLAC encoding (optional).
  Plan E4 measures these.

## 4. Licence obligations (AGPL-3.0-only project)
* All runtime components listed above are compatible with distribution next to AGPL code: Apache-2.0, MIT, LGPL-2.1+, GPL-2.0+, BSD and the
  Python licence.
* **The image is a collection.** We must ship the source (or a written offer) for GPL/LGPL components; Buildroot's `make legal-info` produces
  the manifest and sources. Add that to the release process.
* The Raspberry Pi firmware binaries are redistributable under their own licence (BSD-3-Clause per Buildroot); include the licence text.
* The flash tool: PySide6 is LGPL-3.0, so dynamic linking with notices is fine. Tauri is MIT/Apache.

## 5. Implementation plan (after approval; order = dependencies)
| Step | Content | Depends on | Acceptance criteria (verifiable) |
|---|---|---|---|
| 1 | br2-external skeleton, three defconfigs, package selection (§1), bridge as a Buildroot package (python-hifi-bridge), systemd unit, users/permissions | hardware gate passed | `make` builds all three images reproducibly in Docker; `legal-info` complete; images boot on each board (REAL-HARDWARE) |
| 2 | Partition layout + U-Boot + RAUC system.conf; development key pair | 1 | `rauc status` shows A/B; an update of slot B, then reboot, ends in "good"; boot-count rollback after a forced failed boot; power cut during install leaves a bootable system (10×) |
| 3 | Provisioning (`hifi-provision.json`), hotspot with per-device password, regulatory domain, first-boot data growth | 1 | a fresh card with provisioning boots into the configured Wi-Fi or a hotspot within N s (measured); the file is deleted; no shared secret in the image (scan) |
| 4 | Avahi integration in the image (avahi backend enabled), HTTPS on 443 with the local CA, fingerprint in `hifi-status.txt` | 1 | name resolves on iOS/Android/Windows/macOS; HTTPS lock after CA install; fingerprint matches the trust page |
| 5 | Recovery (`hifi-recovery`), factory reset, fault log | 3 | reset with physical card access only; unauthenticated network paths cannot trigger it |
| 6 | Signed update upload in the WebUI + optional update check (never automatic install) | 2 | an unsigned or wrongly signed bundle is rejected; a signed one is installed only after owner confirmation; rollback is selectable |
| 7 | Flash/provisioning tool (Windows/macOS/Linux) | 3, 4 | writes and verifies the image; generates per-device secrets; shows the setup code and fingerprint; signed installers or clearly marked gaps |
| 8 | Resource and image-size measurement, storage recommendation | 1–4 | measured numbers replace §3 estimates |

## 6. Risks
* `tryboot`/`autoboot.txt` support on the 3 B+ and Zero 2 W is not established; U-Boot boot-count is the fallback for all boards (mitigation in P3).
* The Zero 2 W has 512 MB RAM and shared 2.4 GHz Wi-Fi/Bluetooth. It may not be a supported target if plan E5 fails.
* The jump from Python 3.11 (lab) to 3.14 (image) must stay covered by the CI matrix.
* PipeWire 1.2.8 differs from the tested 0.3.65/1.4.2. The capture command must be re-verified (the `pw-cat --raw` path).
* Key handling and release signing are organisational: process plus hardware token.
