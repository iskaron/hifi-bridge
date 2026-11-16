#!/usr/bin/env bash
# Lists (default) or removes (--apply) ONLY superseded images of THIS project: untagged images whose build history contains the project's
# Dockerfile marker lines, plus the duplicate Home Assistant "stable" tag pulled for the lab. Never touches anything else; no prune.
# Run it yourself after checking the list:  scripts/cleanup_own_images.sh            (dry run)
#                                            scripts/cleanup_own_images.sh --apply    (remove)
set -uo pipefail
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
MARKERS='PYTHONPATH=/work/bridge|python3-numpy python3-pytest python3-jeepney dbus squeezelite|pipewire pipewire-bin pipewire-pulse wireplumber pulseaudio-utils|playwright==1.49.1 pytest==8.3.4|avahi-daemon avahi-utils dbus python3 procps tini|systemd python3 shellcheck|rsyslog rsyslog-gnutls tini|pytest==8.4.2 numpy==2.3.5|bandit==1.8.6|genimage mtools dosfstools e2fsprogs squashfs-tools'
for id in $(docker images -f dangling=true -q | sort -u); do
  if docker history --no-trunc "$id" 2>/dev/null | grep -qE "$MARKERS"; then
    size=$(docker image inspect "$id" --format '{{.Size}}')
    echo "project image (superseded): $id  $((size / 1000000)) MB"
    [ $APPLY -eq 1 ] && docker rmi "$id"
  fi
done
if docker image inspect ghcr.io/home-assistant/home-assistant:stable >/dev/null 2>&1; then
  echo "duplicate tag: ghcr.io/home-assistant/home-assistant:stable (same image as the pinned 2026.11.1)"
  [ $APPLY -eq 1 ] && docker rmi ghcr.io/home-assistant/home-assistant:stable
fi
[ $APPLY -eq 0 ] && echo "(dry run - nothing removed; use --apply)"
exit 0
