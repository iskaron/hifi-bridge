# os/ – Buildroot external tree for the Hi-Fi Bridge appliance (phase-2b SKELETON)

**Nothing here has been built for a board or booted.** What is verified (in Docker, `scripts/run_os.sh`):
* the three defconfigs resolve, and no required option is silently dropped;
* the licence policy (`make show-info`);
* `post-build.sh` (update trust) with real RAUC;
* `post-image.sh` (card layout) with dummy content;
* the U-Boot script compiles.

See docs/PHASE2B_PLAN.md and the gate checklist (docs/HARDWARE_GATE_CHECKLIST.md, G2).

```
br2-external/
  external.desc, external.mk, Config.in
  configs/hifi_bridge_{rpi3_64,rpi4_64,rpizero2w_64}_defconfig   GENERATED - do not edit; edit fragments/ and run scripts/os_defconfigs.sh generate
  fragments/common.fragment          package set (every line is a requirement; the check fails if Kconfig drops one)
  fragments/<board>.fragment         firmware config, compatible string
  package/hifi-bridge/               the product (Python, systemd units, user hifi-bridge uid 990), update-trust choice
  board/hifi-bridge/
    post-build.sh                    keyring from the explicit trust choice, RAUC system.conf, fstab, units
    post-image.sh, genimage.cfg.in   MBR card: boot (FAT) | rootfs-A | rootfs-B | data ; manifest with the provisioning slot
    uboot/boot.cmd                   A/B selection with boot-count (provisional, RAUC U-Boot backend)
    rauc/system.conf.in              slots, keyring, check-purpose=codesign
    firmware/                        config.txt per board (U-Boot; no miniuart-bt), cmdline.txt
    overlay/                         growdata + data-directory units
    keys/                            PUBLIC CA certificates only (release-ca.pem does not exist yet)
tools/license_report.py              licence policy over `make show-info`
```

Commands (all in Docker; Buildroot is pinned at `679b9ea`, 2026.02.x LTS):
```bash
scripts/os_fetch_buildroot.sh          # shallow fetch into .lab/buildroot (~80 MB, not versioned)
scripts/os_defconfigs.sh check         # resolve + no dropped option + committed defconfigs up to date
scripts/os_defconfigs.sh generate      # after editing fragments/
scripts/os_licenses.sh                 # reports/evidence/os_licenses_<board>.md
scripts/run_os.sh                      # all of the above + tests/os + tests/flashtool
```

A real build (phase 2b step 1, after the hardware gate G1 and owner approval) would be:
`make -C .lab/buildroot O=$PWD/output/rpi3 BR2_EXTERNAL=$PWD/os/br2-external hifi_bridge_rpi3_64_defconfig && make -C … O=…`.
Before that, choose the update trust (`menuconfig`); the build fails on purpose until you do (docs/UPDATE_TRUST.md).
