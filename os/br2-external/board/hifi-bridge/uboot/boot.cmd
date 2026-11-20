# Hi-Fi Bridge A/B boot selection for RAUC's U-Boot backend (P3 - PROVISIONAL, follows the RAUC documentation's U-Boot example).
# NOT booted on any board yet. Kernel and device-tree overlays are taken from the selected rootfs slot; the firmware DTB on the boot
# partition is shared by both slots (known limitation, docs/PHASE2B_PLAN.md).
#
# The environment exists twice on the boot FAT (uboot.env, uboot-redund.env; uboot.fragment): saveenv below and fw_setenv (RAUC) always
# rewrite the older copy, so a power cut during a write keeps the previous state (review N-6). The card ships both copies with the slot
# state and hifi_env=1 (uboot-env.txt).

# Both copies unreadable: U-Boot runs on its built-in default environment - no hifi_env, no slot state. Do not act like a fresh card
# (that would never try B, although B may be the only complete slot if RAUC was writing A): give A, then B three attempts each, save
# that as a new environment, and mark this boot on the kernel command line (hifi.bootenv=reset) so that it is visible in Linux.
setenv hifi_bootenv
if test "${hifi_env}" != "1"; then
  echo "Hi-Fi Bridge: WARNING - boot environment lost (both copies invalid): trying slot A, then slot B"
  setenv BOOT_ORDER "A B"
  setenv BOOT_A_LEFT 3
  setenv BOOT_B_LEFT 3
  setenv hifi_env 1
  setenv hifi_bootenv hifi.bootenv=reset
fi
# single variables missing (never written by RAUC or by this script in any other way): fresh-card defaults
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

# panic=10: a slot whose kernel loads but cannot run (e.g. a damaged root file system) restarts after 10 s instead of hanging, so the
# attempt counter decremented above can fall back to the other slot
setenv bootargs "console=tty1 root=/dev/mmcblk0p${rootpart} rootfstype=ext4 ro rootwait panic=10 rauc.slot=${rauc_slot} ${hifi_bootenv}"
load mmc 0:${rootpart} ${kernel_addr_r} /boot/Image || reset
booti ${kernel_addr_r} - ${fdt_addr}
reset
