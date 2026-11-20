#!/bin/bash
# Reproduction of the pipewire-pulse START-UP race (docs/PIPEWIRE_CAPTURE.md "Harness flakes"): run INSIDE a btbridge-pw container
# (scripts/run_pipewire_repro.sh does that). pipewire-pulse creates $XDG_RUNTIME_DIR/pulse with stat() + mkdir() and treats EEXIST as fatal;
# a `pactl` started at the same moment (the readiness poll of testkit/pw/start_pipewire.sh) creates the directory too.
#
# usage: repro_startup_race.sh [N] [poll|pre]
#   poll  the old harness: the directory does not exist, `pactl info` is started right after pipewire-pulse
#   pre   the fixed harness: the directory is created first (as start_pipewire.sh does now)
# Prints one line per start-up in which pipewire-pulse died and the number of failures out of N.
N=${1:-100}; MODE=${2:-poll}
export XDG_RUNTIME_DIR=/run/pw; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CONFIG_HOME=/tmp/pw-config; mkdir -p /tmp/pw-logs
dbus-daemon --session --fork --nopidfile --address="unix:path=$XDG_RUNTIME_DIR/dbus.sock" </dev/null >/dev/null 2>&1
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/dbus.sock"
pipewire </dev/null >/tmp/pw-logs/pipewire.log 2>&1 &
for _ in $(seq 100); do [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break; sleep 0.1; done
fail=0
for i in $(seq "$N"); do
  pkill -x pipewire-pulse; while pgrep -x pipewire-pulse >/dev/null; do sleep 0.02; done
  rm -rf "${XDG_RUNTIME_DIR:?}/pulse"
  if [ "$MODE" = pre ]; then mkdir -p "$XDG_RUNTIME_DIR/pulse"; chmod 700 "$XDG_RUNTIME_DIR/pulse"; fi
  : > /tmp/pw-logs/pp.log
  pipewire-pulse </dev/null >/tmp/pw-logs/pp.log 2>&1 &
  pid=$!
  for _ in $(seq 100); do
    pactl info >/dev/null 2>&1 && break
    kill -0 $pid 2>/dev/null || break
    sleep 0.05
  done
  if ! pactl info >/dev/null 2>&1; then
    fail=$((fail + 1)); echo "start-up $i: pipewire-pulse unavailable: $(grep -m1 -o 'mkdir.*exists' /tmp/pw-logs/pp.log)"
  fi
done
echo "MODE=$MODE N=$N failures=$fail"
[ "$fail" -eq 0 ]
