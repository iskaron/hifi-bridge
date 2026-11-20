# os/ – Buildroot external tree for the Hi-Fi Bridge appliance

**Internal DEVELOPMENT images are built for all three boards (owner approval for builds before gate G1; phase 2b package 6, 2026-11-19:
all image checks pass, secret scan clean, `make ota` round trip passes). Nothing has been booted.** "Builds" is not "boots": every board statement is BUILD-VERIFIED (compiles, image layout and contents checked in Docker) and
HARDWARE-PENDING (docs/HARDWARE_GATE_CHECKLIST.md, G2). Measurements, reproducibility and sizes: docs/IMAGE_BUILD.md. Licences of the
firmware: docs/IMAGE_LICENSING.md.

```
br2-external/
  external.desc, external.mk, Config.in
  configs/hifi_bridge_{rpi3_64,rpi4_64,rpizero2w_64}_defconfig   GENERATED - do not edit; edit fragments/ and run scripts/os_defconfigs.sh generate
  fragments/common.fragment          package set + read-only root + reproducibility (every line is a requirement; the check fails if Kconfig drops one)
  fragments/<board>.fragment         firmware config, compatible string
  fragments/dev-build.fragment       ONLY for internal development builds (scripts/os_build.sh): variant "development", own update trust
                                     with the temporary development CA, download dir + ccache
  package/hifi-bridge/               the product (Python + precompiled bytecode, systemd units, user hifi-bridge uid 990), update trust,
                                     build variant (development | release)
  board/hifi-bridge/
    post-build.sh                    keyring from the explicit trust choice, RAUC system.conf, fstab, units, os-release + build-info.json
    post-image.sh, genimage.cfg.in   MBR card: boot (FAT) | rootfs-A | rootfs-B | data ; licences on the FAT; manifest with the provisioning slot
    uboot/boot.cmd                   A/B selection with boot-count (provisional, RAUC U-Boot backend)
    rauc/system.conf.in              slots, keyring, check-purpose=codesign
    firmware/                        config.txt per board (U-Boot; no miniuart-bt), cmdline.txt
    licenses/                        licence texts that the upstream packages do not carry (Synaptics Bluetooth firmware)
    overlay/                         growdata + data-directory units
    keys/                            PUBLIC CA certificates only (release-ca.pem does not exist yet)
tools/
  license_report.py                  licence policy over `make show-info`
  image_check.py                     static checks of a built card image (used by scripts/os_check_image.sh)
  secret_scan.py                     credentials/private keys/tokens in image trees and raw images
  tree_manifest.py                   per-file manifest + comparison (reproducibility)
  size_plan.py                       card layout arithmetic (DECISIONS #120) checked against the measured builds
```

## Commands (all in Docker; Buildroot pinned at `679b9ea`, 2026.02.x LTS)
```bash
scripts/os_fetch_buildroot.sh          # shallow fetch into .lab/buildroot (~80 MB, not versioned)
scripts/os_defconfigs.sh check         # resolve + no dropped option + committed defconfigs up to date
scripts/os_defconfigs.sh generate      # after editing fragments/
scripts/os_licenses.sh                 # reports/evidence/os_licenses_<board>.md (show-info only, no build)
scripts/run_os.sh                      # all of the above + tests/os + tests/flashtool

# internal development images (one board at a time; hours for a first build, minutes incrementally)
scripts/os_build.sh rpi3_64            # build + legal-info + SBOM   -> .lab/br-out/rpi3_64/images/*.img
scripts/os_check_image.sh rpi3_64      # all image checks + secret scan -> reports/evidence/os_image_rpi3_64.md
scripts/os_check_ota.sh rpi3_64        # `make ota` bundle of the built rootfs, checked against the image's own keyring
```
Environment: `HIFI_LAB` (lab dir, default `.lab`; a git worktree points it at the main checkout's `.lab`), `HIFI_OS_JOBS`/`HIFI_OS_CPUS`
(default 3), `HIFI_OS_MEM` (default 8g), `HIFI_OS_MIN_FREE_GB` (default 40). `scripts/os_build.sh <board> clean build` starts over
(Buildroot does not remove packages that were dropped from the configuration; after removing a package, build clean).

**Update trust:** the committed defconfigs still make the choice explicit (the build fails until it is made, docs/UPDATE_TRUST.md).
Development builds use `own` trust with a **temporary development CA** created by `scripts/os_dev_ca.sh` in `.lab/dev-update-ca/`
(git-ignored, keys 0600). Only its public certificate is mounted into the build container. Never use it for anything but development.
