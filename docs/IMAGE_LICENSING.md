# Image licensing: Raspberry Pi boot firmware and Wi-Fi/Bluetooth firmware (G4 L-1, OPEN_QUESTIONS #35)

Status (2026-11-18, legal-info facts added 2026-11-19 from the real builds; **per-board firmware, source-bundle preparation and the
export-control/Synaptics risk notes added in the final polish P2, 2026-11-20, sections 5-7**): the licence texts below were **read from the exact, hash-verified source archives that the build uses**
(Buildroot 2026.02.x @ `679b9ea`) and from the upstream copyright files those archives come from. This is a reading of the texts by the
project, **not legal advice**; the owner decides on a public release (G4). Nothing here has been published.

## 1. What is in the image
| Buildroot package (version) | Files in the image | Where | Licence file(s) |
|---|---|---|---|
| `rpi-firmware` (`063bcab6`, github.com/raspberrypi/firmware; archive sha256 `812cb646…`) | `bootcode.bin` (Pi 3 B+/Zero 2 W), `start.elf`/`fixup.dat` (Pi 3 B+/Zero 2 W) or `start4.elf`/`fixup4.dat` (Pi 4); 358 prebuilt DT overlays `overlays/*.dtbo` + `overlay_map.dtb` | boot FAT | `boot/LICENCE.broadcom` (sha256 `c7283ff5…`, = Buildroot's hash) for the firmware blobs; the overlays are compiled Linux device-tree sources (see §4) |
| `brcmfmac_sdio-firmware-rpi` (`6596d332`, github.com/LibreELEC/brcmfmac_sdio-firmware-rpi; archive sha256 `d9835b33…`) | **since P2 only the board's list** (section 5); before: all 90 entries (`brcm/*.hcd`, `synaptics/*.hcd`, `brcm/brcmfmac*`, `cypress/cyfmac*`: `.bin`, `.clm_blob`, NVRAM `.txt`) on every board | rootfs `/usr/lib/firmware` | `LICENSE` (sha256 `ea8b7b7b…`, = Buildroot's hash), a Debian-format copyright file with three licences: *binary-redist-Broadcom-wifi*, *binary-redist-Cypress*, *Synaptics* |

The repository's README names its upstreams: Wi-Fi from `RPi-Distro/firmware-nonfree` (`debian/config/brcm80211`), Bluetooth from
`RPi-Distro/bluez-firmware` (`debian/firmware`). Their `debian/copyright` files (branch `bookworm`, retrieved 2026-11-18) map the files we use:

| Board | Chip | Wi-Fi file actually loaded → licence | Bluetooth file actually loaded → licence |
|---|---|---|---|
| Pi 3 B+ | CYW43455 | `brcmfmac43455-sdio.raspberrypi,3-model-b-plus.bin` → `cypress/cyfmac43455-sdio.bin` (+ `.clm_blob`): **Cypress EULA**; NVRAM `brcmfmac43455-sdio.txt`: **GPL-2** (firmware-nonfree: "2018, Raspberry Pi (Trading) Ltd.") | `BCM4345C0.raspberrypi,3-model-b-plus.hcd` → `BCM4345C0.hcd`: bluez-firmware `debian/firmware/broadcom/*` = **Cypress EULA** |
| Pi 4 B | CYW43455 | same as Pi 3 B+ (`…4-model-b…` symlinks) | `BCM4345C0.raspberrypi,4-model-b.hcd` → `BCM4345C0.hcd`: **Cypress EULA** |
| Zero 2 W | CYW43436 (RP3A0; board revisions differ) | `brcmfmac43436-sdio.bin` or `brcmfmac43436s-sdio.bin` (via `…model-zero-2-w` symlinks): firmware-nonfree `*43436*` = **Synaptics** (binary distribution EULA, in `LICENSE`) | `BCM43430A1/B0.raspberrypi,model-zero-2-w.hcd` → `synaptics/SYN43430A1.hcd` / `SYN43430B0.hcd`: bluez-firmware `debian/firmware/synaptics/*` = **Synaptics End-User Software Tool License – NOT contained in the package's `LICENSE`** |

The *binary-redist-Broadcom-wifi* licence in `LICENSE` covers `brcm/brcmfmac*.bin` in linux-firmware. In this package most of those names are
symlinks to the Cypress files; the 43436 `.bin` files are Synaptics per firmware-nonfree. We treat all four licences as applicable to the
package as a whole.

## 2. The redistribution clauses (verbatim)
**Raspberry Pi boot firmware** – `boot/LICENCE.broadcom` (Copyright (c) 2006, Broadcom Corporation; Copyright (c) 2015, Raspberry Pi (Trading) Ltd):
> Redistribution. Redistribution and use in binary form, without modification, are permitted provided that the following conditions are met:
> * This software may only be used for the purposes of developing for, running or using a Raspberry Pi device, or authorised derivative device manufactured via the element14 Raspberry Pi Customization Service
> * Redistributions must reproduce the above copyright notice and the following disclaimer in the documentation and/or other materials provided with the distribution.
> * Neither the name of Broadcom Corporation nor the names of its suppliers may be used to endorse or promote products derived from this software without specific prior written permission.

**Broadcom Wi-Fi** – `LICENSE`, *binary-redist-Broadcom-wifi*:
> 2.1. License Grants. … Broadcom hereby grants to Licensee a non-exclusive, non-transferable, royalty-free license (i) to use and integrate the Software in conjunction with any other software; and (ii) to reproduce and distribute the Software complete, unmodified and as provided by Broadcom, and only for use with a Broadcom Product.
> 2.2. Restriction on Modification. Licensee may not make any modifications to the Software.
> 2.3. Restriction on Distribution. Licensee shall only distribute the Software under the terms of this Agreement and a copy of this Agreement accompanies such distribution.
> 2.4. Proprietary Notices. Licensee shall not remove, efface or obscure any copyright or trademark notices from the Software. Licensee shall include reproductions of the Broadcom copyright notice with each copy of the Software, except where such Software is embedded in a manner not readily accessible to the end user.
> 7.1. Export Regulations. … YOU MAY NOT EXPORT, RE-EXPORT OR TRANSFER THE SOFTWARE OR ANY DIRECT PRODUCT OF THE SOFTWARE EXCEPT AS PERMITTED UNDER THOSE LAWS. … EXPORT, RE-EXPORT OR TRANSFER OF THE SOFTWARE TO CUBA, IRAN, NORTH KOREA, SUDAN AND SYRIA IS PROHIBITED.

**Cypress** – `LICENSE`, *binary-redist-Cypress* (word-identical to bluez-firmware's *CYPRESS-WIRELESS-CONNECTIVITY-DEVICES-DRIVER-END-USER-LICENSE-AGREEMENT*, compared word by word):
> Software Provided in Binary Code Form. … Cypress Semiconductor Corporation ("Cypress") grants you a non-exclusive, non-transferable license under its copyright rights in the Software to reproduce and distribute the Software in object code form only, solely for use in connection with Cypress integrated circuit products ("Purpose").
> Proprietary Rights. … Except as otherwise expressly provided in this Agreement, you may not: (i) modify, adapt, or create derivative works based upon the Software; (ii) copy the Software; (iii) … decompile, translate, reverse engineer, disassemble …; or (iv) use the Software other than for the Purpose.
> Term and Termination. … Termination of this Agreement will not affect any licenses validly granted as of the termination date to any end users of the Software.

**Synaptics (Wi-Fi, Zero 2 W)** – `LICENSE`, *Synaptics*:
> Synaptics Incorporated ("Synaptics") grants you a non-exclusive, non-transferable license under its copyright rights in the Software to reproduce and distribute the Software in object code form only, solely for use in connection with Synaptics integrated circuit products ("Purpose").
> … you may not: (i) modify, adapt, or create derivative works based upon the Software; (ii) copy the Software; (iii) … reverse engineer …; or (iv) use the Software other than for the Purpose.

**Synaptics End-User Software Tool License (Bluetooth, Zero 2 W)** – bluez-firmware `debian/copyright`; full text now shipped as
`os/br2-external/board/hifi-bridge/licenses/SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt`:
> 2. … you will have a nonexclusive, non-transferable, limited right, during the License Term, to: … (c) Copy and redistribute the Software Libraries in object code only in accordance with this agreement for use in connection with Synaptics integrated circuits.
> 4. You may not (and may not allow anyone else to): … (d) distribute or publish any copy of the Software Libraries or related documentation on any medium for any purpose … other than as permitted under this agreement; … (g) modify, enhance or create a derivative work …
> 10. The term of this agreement (the "License Term") … will end when you stop accessing or using the Software Libraries or when Synaptics notifies you in writing of the termination of this agreement not less than thirty days following the sending of the notice … Synaptics has the right, in its sole discretion, to terminate this agreement immediately by giving written notice of termination to you.
> 11. If and when either you or Synaptics terminates this agreement: (a) you must (i) immediately cease all use of the Software Libraries and related documentation in any future releases of your products or platforms without prior written authorization from Synaptics …

## 3. Conclusion: what may be distributed publicly, under which conditions
**May be distributed in a public image** (as Raspberry Pi OS, LibreELEC and Buildroot-based distributions do), provided that:
1. **Unmodified binaries only.** No patching, re-packing or recompressing of `bootcode.bin`, `start*.elf`, `fixup*.dat`, `*.hcd`, `*.bin`,
   `*.clm_blob`. Our build copies them byte for byte (Buildroot `cp`/`install`, only file modes change). *Exception:* the Wi-Fi NVRAM `.txt`
   files are GPL-2 (firmware-nonfree) and could be changed, but we do not.
2. **The licence texts and copyright notices accompany every copy.** Implemented: every image carries `licenses/` on the boot FAT
   (readable on any computer) with `rpi-firmware.LICENCE.broadcom`, `brcmfmac_sdio-firmware-rpi.LICENSE`,
   `SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt` and `NOTICE.txt`; `post-image.sh` fails if a required file is missing (tested). A public
   release must also publish the same files next to the image download and in the legal-info archive.
3. **Only for the hardware they are made for.** Satisfied by the product: the image runs only on Raspberry Pi boards with these
   Broadcom/Cypress/Synaptics chips. The notice says so. The images must not be advertised as a source of these firmware files.
4. **No endorsement** using the name of Broadcom or its suppliers (RPi licence). Product texts must not imply that.
5. **Export control** (Broadcom 7.1, Cypress/Synaptics "Compliance With Law"): a public download is an export. GitHub Releases applies
   GitHub's trade-control restrictions, but the owner, as distributor, remains responsible. **Owner decision / legal check before G4.**

**Not allowed:** modifying or reverse-engineering the firmware; distributing it without the licence texts; distributing it for use
with other hardware; using Broadcom's name for promotion; distributing it to the embargoed destinations named in the Broadcom licence.

**Residual risks to record:**
* **Synaptics Bluetooth firmware (Zero 2 W only) is revocable:** Synaptics may terminate the tool licence by written notice; afterwards no
  *future* release may include `SYN43430*.hcd` without Synaptics' authorisation (§11). Releases for the Pi 3 B+ and Pi 4 do not need the
  Synaptics files at all (Cypress `BCM4345C0.hcd`/`cyfmac43455`). **Implemented in P2 (owner decision #125):** per-board firmware
  (section 5) - the Pi 3 B+ and Pi 4 images carry no Synaptics Bluetooth file and not the tool licence; the risk remains for the Zero 2 W
  image only (section 7).
* The package's own `LICENSE` file is **incomplete** for the Bluetooth files: it lacks the Synaptics End-User Software Tool License
  (shipped by us now) and does not name the `BCM*.hcd` files explicitly (bluez-firmware maps them to the Cypress EULA, which `LICENSE`
  contains). Worth reporting upstream (LibreELEC / Buildroot).
* "non-transferable" licences: each distributor (the owner) is a licensee; end users receive the binaries "under the terms of this
  Agreement" (Broadcom 2.3), which is why the texts must travel with the image.

## 4. Other items for the public release checklist (G4 L-1)
* **Prebuilt device-tree overlays** (`overlays/*.dtbo`, 358 files, from `rpi-firmware`) are compiled Linux device-tree sources (GPL-2.0 for
  most). They were built by Raspberry Pi from `raspberrypi/linux` commit **`957fc9f9`** (6.12.61, `extra/git_hash` in the firmware archive),
  **not** from the kernel tarball we build (`21b41014`). `make legal-info` therefore does not contain their corresponding source. Before a
  public release: add the `raspberrypi/linux@957fc9f9` source to the source offer, or build the overlays from our own kernel tree, or drop
  them (the product's `config.txt` uses no overlay). Owner decision (OPEN_QUESTIONS #95). **Prepared in P2:** the source bundle
  includes that source (section 6).
* GPL/LGPL components: ship `make legal-info` (sources + licence texts + `manifest.csv`) per release, or a written offer (OPEN_QUESTIONS #43).
  Measured: legal-info is **965 MB per board** (sources dominate; the boards share almost all of it). It completes, with two warnings that
  a release must close by hand: **the Buildroot source itself is not saved** (Buildroot is GPL-2.0+ and its skeleton files are in the
  image: add the pinned Buildroot tarball to the source offer) and **the Bootlin toolchain has no licence files defined** (add the glibc
  LGPL-2.1+ and GCC runtime-exception texts for the runtime libraries in the image).
* AGPL-3.0-only bridge: source link in every WebUI footer (DECISIONS #72) plus `NOTICE.txt` on the FAT.

## 5. Firmware per board (final polish P2, DECISIONS #125/#134)
Each image keeps only the Wi-Fi/Bluetooth firmware its board can need: `post-build.sh` deletes every other entry below
`/usr/lib/firmware/{brcm,cypress,synaptics}` and fails if a listed one is missing or differs (sha256 / link target - this also enforces the
licences' "unmodified" condition). The single source per board is `os/br2-external/board/hifi-bridge/firmware/radio-<compatible>.txt`
(path, `file <sha256>` or `link <target>`, licence tag); the image check enforces it exactly (`firmware.radio-list`: no missing, no extra,
no changed entry) and refuses any file with the bytes of a Synaptics Bluetooth patch under another tag (`firmware.no-hidden-synaptics-bt`:
the package's `brcm/BCM43430B0.hcd` is **byte-identical** to `synaptics/SYN43430B0.hcd`, so a Broadcom file name does not mean a Broadcom
licence). The names come from the kernel we build (raspberrypi/linux `21b41014`): Wi-Fi `brcmfmac/sdio.c` (`BRCMF_FW_DEF`/`ENTRY`: chip
43430 rev 1 -> `brcmfmac43430-sdio`, rev >= 2 -> `brcmfmac43430b0-sdio`; 4345 rev 9 -> `brcmfmac43456-sdio`, otherwise
`brcmfmac43455-sdio`), each file tried first as `<name>.<DT compatible>.<ext>`, then `<name>.<ext>` (the `.clm_blob` is optional);
Bluetooth `btbcm.c` (`BCM43430A1`, `BCM43430B0`, `BCM4345C0`, `BCM4345C5`: `brcm/<name>.<compatible>.hcd`, then `brcm/<name>.hcd`). The
images keep the DTBs of the upstream defconfigs, so firmware for the other boards that boot the same image stays (no compatibility risk);
whether to drop those boards is OPEN_QUESTIONS #94.

| Image | Boards it boots (DTBs) | Kept (90 entries / 25 files / 2.79 MiB before) | Licences of what is kept | Removed (examples) |
|---|---|---|---|---|
| `rpi3_64` | **Pi 3 B+** (CYW43455), Pi 3 B (CYW43438), CM3 (no radio) | 20 entries, 8 files, 1.06 MiB: `brcmfmac43455-sdio.*` (generic + `3-model-b-plus`) -> `cypress/cyfmac43455-sdio.{bin,clm_blob}`, NVRAM `brcmfmac43455-sdio.txt`, `BCM4345C0.hcd`; Pi 3 B: `brcmfmac43430-sdio.*` -> `cypress/cyfmac43430-sdio.*`, `brcmfmac43430-sdio.txt`, `BCM43430A1.hcd` | Cypress only (in `brcmfmac_sdio-firmware-rpi.LICENSE`) | every Synaptics file (`synaptics/SYN43430*.hcd`, `BCM43430B0.hcd`), 43436/43436s/43456/43439, `BCM4345C5`, `BCM4343A2`, links of other boards |
| `rpi4_64` | **Pi 4 B** (CYW43455), Pi 400 (CYW43456), CM4 (either chip), CM4S (no radio) | 26 entries, 8 files, 1.18 MiB: `brcmfmac43455-sdio.*` (generic, `4-model-b`, `4-compute-module`) -> `cypress/cyfmac43455-sdio.*`, `brcmfmac43456-sdio.*` (generic, `400`, `4-compute-module`), `BCM4345C0.hcd`, `BCM4345C5.hcd` + board links | Cypress; **Synaptics Wi-Fi EULA** for `brcmfmac43456-sdio.*` (Pi 400/CM4 only; binary distribution, no termination-by-notice clause) | every Synaptics Bluetooth file, 43430/43436*, 43439, `BCM43430*`, `BCM4343A2` |
| `rpizero2w_64` | **Zero 2 W** (RP3A0 with CYW43436; the radio reports chip 43430 rev A1 or B0 depending on the board revision) | 21 entries, 8 files, 0.92 MiB: `brcmfmac43430-sdio.raspberrypi,model-zero-2-w.{bin,txt}` -> `43436s`, `brcmfmac43430b0-sdio.<board>.{bin,clm_blob,txt}` -> `43436`, the `43436`/`43436s` names, generic `brcmfmac43430-sdio.clm_blob` -> `cypress/cyfmac43430-sdio.clm_blob` (the rev-A1 board name has no `.clm_blob`, so the kernel falls back to it, as on Raspberry Pi OS); `BCM43430A1/B0.<board>.hcd` -> `synaptics/SYN43430A1/B0.hcd` | Synaptics Wi-Fi EULA, Cypress (the CLM blob), **Synaptics End-User Software Tool License** (Bluetooth) | 43455/43456/43439, all `BCM4345*`, generic `BCM43430A1.hcd`/`BCM43430B0.hcd`, `cyfmac43430-sdio.bin` |

Licence texts on the boot FAT (`licenses/`, written by `post-image.sh` from the same list; checked by `boot.firmware-licences` and
`boot.firmware-notice`): every image `rpi-firmware.LICENCE.broadcom`, `brcmfmac_sdio-firmware-rpi.LICENSE` (it holds the Broadcom Wi-Fi,
Cypress and Synaptics Wi-Fi texts) and `NOTICE.txt` naming the board and exactly its licences; **only the Zero 2 W image** also
`SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt`.

**Boot firmware** was already per board (Buildroot `BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI` / `_PI4`): Pi 3 B+ and Zero 2 W `bootcode.bin`
(their boot ROM loads it), `start.elf`, `fixup.dat`; Pi 4 `start4.elf`, `fixup4.dat` (EEPROM boot, no `bootcode.bin`). The image check now
requires exactly this set (`firmware.boot-files`). The 358 prebuilt DT overlays are unchanged (section 4, OPEN_QUESTIONS #95).

Hardware check of the lists: gate P1-4 (the firmware names in `dmesg` per board and Zero 2 W radio revision).

## 6. Source provision for a later public release (prepared, nothing published; OPEN_QUESTIONS #75)
`scripts/os_source_bundle.sh <board> [--no-overlay-source]` (after `os_build.sh <board> build legal`) assembles under the git-ignored
`.lab/source-bundle/<board>-<commit12>/`:
* `legal-info/` of the build (hard links, no extra space): source tarballs and patches of every package, licence texts, `manifest.csv`,
  `host-*`, `buildroot.config`, Buildroot's `legal-info.sha256`;
* `buildroot-<commit>.tar.gz`: the Buildroot source at the pinned commit (closes legal-info's warning "the Buildroot source code has not
  been saved"; Buildroot is GPL-2.0+ and its skeleton and scripts are in the image);
* `linux-overlays-<commit>.tar.gz`: raspberrypi/linux at **`957fc9f9724b3b973a7842d5abbd382b7e3f5e2d`**, the commit the prebuilt overlays
  were built from (rpi-firmware `extra/git_hash`); our kernel `21b41014` is in `legal-info/sources/linux-custom`;
* `toolchain/`: the Bootlin toolchain's `README.txt`, `summary.csv` (component versions and licences) and `buildroot.config`, the list of
  toolchain runtime files in the image (`files-in-image.txt`: glibc LGPL-2.1+, libgcc_s/libstdc++/libatomic GPL-3.0+ with the GCC Runtime
  Library Exception 3.1), those licence texts (from Debian's common licences and the libgcc-s1 copyright in btbridge-os) and pointers to
  Bootlin's `downloads/releases/sources/` and `.../licenses/` (to be mirrored for a release; not downloaded) - closes legal-info's
  "toolchain has no licence files defined";
* `hifi-bridge-<commit>.tar.gz`: this repository at the commit (`git archive`: tracked files only, never `secrets/` or `.lab/`);
* `MANIFEST.json` + `SHA256SUMS`: commits, versions, URLs, the sha256 of every file outside `legal-info/` (legal-info through the hash of its
  own `legal-info.sha256`), `"published": false`. A dirty working tree is refused (the bundle must correspond to a commit).

Result of the P2 run: docs/IMAGE_BUILD.md section 10. For a release (G4 L-1/L-3): one bundle per board and release, built from the release
commit and mirrored next to the image (or a written offer, OPEN_QUESTIONS #43/#96); the Bootlin sources mirrored.

## 7. Export control and Synaptics: what the licences say, what P2 changed, what remains (OPEN_QUESTIONS #73/#74)
The project's reading of the texts - not legal advice and not a clearance.
* **Export control (#73).** Broadcom Wi-Fi licence §7.1 (in `brcmfmac_sdio-firmware-rpi.LICENSE`): use and export are subject to U.S.
  export laws; "YOU MAY NOT EXPORT, RE-EXPORT OR TRANSFER THE SOFTWARE OR ANY DIRECT PRODUCT OF THE SOFTWARE EXCEPT AS PERMITTED UNDER
  THOSE LAWS"; transfer to Cuba, Iran, North Korea, Sudan and Syria is prohibited. The Cypress and Synaptics texts have their own
  compliance-with-law clauses. Per-board pruning **does not change this**: every image still contains Broadcom/Cypress Wi-Fi firmware
  (the `brcm/brcmfmac*.bin` names fall under the Broadcom licence even where they point to Cypress files). Remains for the owner/legal
  review before any public download (G4 L-3): whether the download page needs an export notice or a restriction, whether GitHub's own
  trade controls are relied on, and whether the destinations named in the text need a current check.
* **Synaptics (#74).** Two different texts: (a) the *Synaptics* Wi-Fi binary-distribution EULA (43436/43436s for the Zero 2 W, 43456 for
  the Pi 400/CM4 in the Pi 4 image): use only with Synaptics chips, no modification, no termination-by-notice clause found; (b) the
  *Synaptics End-User Software Tool License* for the Bluetooth patches `SYN43430A1/B0.hcd`: limited and revocable - Synaptics may end it by
  written notice (§10), after which future releases must not include the files without authorisation (§11). **P2:** (b) is in the Zero 2
  W image only (its byte-identical copy `BCM43430B0.hcd` is gone from the other images too); the Pi 3 B+ image carries no Synaptics file
  at all; the Pi 4 image carries (a) only for the Pi 400/CM4 chip. **Remains:** Zero 2 W Bluetooth depends on (b); if Synaptics ended it,
  Zero 2 W releases would need written authorisation or would lose Bluetooth (owner decision whether the Zero 2 W stays a supported board
  under that risk); dropping Pi 400/CM4 support would remove (a) from the Pi 4 image (OPEN_QUESTIONS #94).
