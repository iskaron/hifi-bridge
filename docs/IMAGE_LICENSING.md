# Image licensing: Raspberry Pi boot firmware and Wi-Fi/Bluetooth firmware (G4 L-1, OPEN_QUESTIONS #35)

Status (2026-11-18): the licence texts below were **read from the exact, hash-verified source archives that the build uses**
(Buildroot 2026.02.x @ `679b9ea`) and from the upstream copyright files those archives come from. This is a reading of the texts by the
project, **not legal advice**; the owner decides on a public release (G4). Nothing here has been published.

## 1. What is in the image
| Buildroot package (version) | Files in the image | Where | Licence file(s) |
|---|---|---|---|
| `rpi-firmware` (`063bcab6`, github.com/raspberrypi/firmware; archive sha256 `812cb646…`) | `bootcode.bin` (Pi 3 B+/Zero 2 W), `start.elf`/`fixup.dat` (Pi 3 B+/Zero 2 W) or `start4.elf`/`fixup4.dat` (Pi 4); 358 prebuilt DT overlays `overlays/*.dtbo` + `overlay_map.dtb` | boot FAT | `boot/LICENCE.broadcom` (sha256 `c7283ff5…`, = Buildroot's hash) for the firmware blobs; the overlays are compiled Linux device-tree sources (see §4) |
| `brcmfmac_sdio-firmware-rpi` (`6596d332`, github.com/LibreELEC/brcmfmac_sdio-firmware-rpi; archive sha256 `d9835b33…`) | all `brcm/*.hcd`, `synaptics/*.hcd` (Bluetooth), `brcm/brcmfmac*`, `cypress/cyfmac*` (Wi-Fi: `.bin`, `.clm_blob`, NVRAM `.txt`) | rootfs `/usr/lib/firmware` | `LICENSE` (sha256 `ea8b7b7b…`, = Buildroot's hash), a Debian-format copyright file with three licences: *binary-redist-Broadcom-wifi*, *binary-redist-Cypress*, *Synaptics* |

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
  Synaptics files at all (Cypress `BCM4345C0.hcd`/`cyfmac43455`). **Option (not implemented, owner decision):** per-board firmware
  pruning so that each image only contains its own board's files (also saves ~2 MB).
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
  them (the product's `config.txt` uses no overlay). Owner decision.
* GPL/LGPL components: ship `make legal-info` (sources + licence texts + `manifest.csv`) per release, or a written offer (OPEN_QUESTIONS #43).
  Measured sizes per board are in docs/IMAGE_BUILD.md.
* AGPL-3.0-only bridge: source link in every WebUI footer (DECISIONS #72) plus `NOTICE.txt` on the FAT.
