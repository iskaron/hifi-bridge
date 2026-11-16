# Hi-Fi Bridge A/B boot selection for RAUC's U-Boot backend (P3 - PROVISIONAL, follows the RAUC documentation's U-Boot example).
# NOT booted on any board yet. Kernel and device-tree overlays are taken from the selected rootfs slot; the firmware DTB on the boot
# partition is shared by both slots (known limitation, docs/PHASE2B_PLAN.md).
test -n "${BOOT_ORDER}" || setenv BOOT_ORDER "A B"
test -n "${BOOT_A_LEFT}" || setenv BOOT_A_LEFT 3
# slot B is EMPTY on a freshly flashed card: no attempts until RAUC has installed it (RAUC then sets BOOT_ORDER/BOOT_B_LEFT itself)
test -n "${BOOT_B_LEFT}" || setenv BOOT_B_LEFT 0

setenv bootargs
setenv rootpart
for BOOT_SLOT in "${BOOT_ORDER}"; do
  if test "x${rootpart}" != "x"; then
    # a slot was already chosen
  elif test "x${BOOT_SLOT}" = "xA"; then
    if test ${BOOT_A_LEFT} -gt 0; then
      setexpr BOOT_A_LEFT ${BOOT_A_LEFT} - 1
      echo "Hi-Fi Bridge: slot A, ${BOOT_A_LEFT} attempts left"
      setenv rootpart 2
      setenv rauc_slot A
    fi
  elif test "x${BOOT_SLOT}" = "xB"; then
    if test ${BOOT_B_LEFT} -gt 0; then
      setexpr BOOT_B_LEFT ${BOOT_B_LEFT} - 1
      echo "Hi-Fi Bridge: slot B, ${BOOT_B_LEFT} attempts left"
      setenv rootpart 3
      setenv rauc_slot B
    fi
  fi
done

if test -n "${rootpart}"; then
  saveenv
else
  echo "Hi-Fi Bridge: no bootable slot left - resetting attempts and rebooting"
  setenv BOOT_A_LEFT 3
  setenv BOOT_B_LEFT 3
  saveenv
  reset
fi

setenv bootargs "console=tty1 root=/dev/mmcblk0p${rootpart} rootfstype=ext4 ro rootwait rauc.slot=${rauc_slot}"
load mmc 0:${rootpart} ${kernel_addr_r} /boot/Image || reset
booti ${kernel_addr_r} - ${fdt_addr}
reset
