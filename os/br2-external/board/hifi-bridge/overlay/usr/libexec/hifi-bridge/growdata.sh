#!/bin/sh
# growdata.sh DISK PARTNO - grow partition PARTNO (must be the last one) to the end of DISK. Idempotent: does nothing when it already
# ends within 1 MiB of the disk end. Works on block devices and, for tests, on image files (no kernel re-read then).
# PHASE-2B SKELETON: tested on image files in Docker (tests/os), not on an SD card.
set -eu
disk="$1"; part="$2"
if [ -b "$disk" ]; then
	size_sectors=$(( $(blockdev --getsize64 "$disk") / 512 ))
else
	size_sectors=$(( $(stat -c %s "$disk") / 512 ))
fi
line=$(sfdisk --dump "$disk" | sed -n "s/^[^ ]*[^0-9]${part} : start= *\([0-9]*\), size= *\([0-9]*\),.*/\1 \2/p")
[ -n "$line" ] || { echo "growdata: partition $part not found on $disk" >&2; exit 1; }
start=${line% *}; psize=${line#* }
last=$(( start + psize ))
if [ $(( size_sectors - last )) -le 2048 ]; then
	echo "growdata: partition $part already fills the disk"
	exit 0
fi
echo ", +" | sfdisk --no-reread --no-tell-kernel -N "$part" "$disk" >/dev/null
if [ -b "$disk" ]; then partx -u "$disk" || true; fi
echo "growdata: partition $part grown"
