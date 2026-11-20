#!/bin/bash
# Reproduction of the pipewire-pulse 1.4.2 CRASH while a module-pipe-source is unloaded under connected or reconnecting streams
# (docs/PIPEWIRE_CAPTURE.md "Harness flakes"): run INSIDE a btbridge-pw container (scripts/run_pipewire_repro.sh does that).
# pipewire-pulse dies with SIGSEGV (exit 139) and writes NOTHING to its log.
#
# usage: repro_unload_crash.sh [N] [consumer] [vanish]
#   consumer  alt (default) | never | always | race | sup
#               alt     every second cycle the consumer is killed just before the unload
#               never   the consumer stays attached during the unload
#               always  the consumer is killed 0.2 s before the unload
#               race    the consumer is killed immediately before the unload
#               sup     a supervised consumer (like PipeWireCapture: restarts parec 0.2 s after each exit, also while the node is missing)
#   vanish    unload (default: `pactl unload-module`) | destroy (`pw-cli destroy <node>`, what tests/pipewire/conftest.vanish_node does)
# Prints "iteration N: ..." and the exit status of pipewire-pulse when it died; exit status 1 when it crashed.
N=${1:-100}; CONSUMER=${2:-alt}; VANISH=${3:-unload}
export XDG_RUNTIME_DIR=/run/pw; mkdir -p "$XDG_RUNTIME_DIR/pulse"; chmod -R 700 "$XDG_RUNTIME_DIR"
export XDG_CONFIG_HOME=/tmp/pw-config; mkdir -p /tmp/pw-logs
dbus-daemon --session --fork --nopidfile --address="unix:path=$XDG_RUNTIME_DIR/dbus.sock" </dev/null >/dev/null 2>&1
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/dbus.sock"
pipewire </dev/null >/tmp/pw-logs/pipewire.log 2>&1 &
for _ in $(seq 100); do [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break; sleep 0.1; done
wireplumber </dev/null >/tmp/pw-logs/wp.log 2>&1 &
( pipewire-pulse </dev/null >/tmp/pw-logs/pp.log 2>&1; echo "### pipewire-pulse exited, status $?" >>/tmp/pw-logs/pp.log ) &
for _ in $(seq 100); do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
FIFO=/tmp/src.fifo; mkfifo "$FIFO"; exec 9<>"$FIFO"
( while :; do head -c 3528 /dev/zero >&9; sleep 0.02; done ) &
if [ "$CONSUMER" = sup ]; then
  ( while :; do parec --device=bluez_input.X --property=node.dont-fallback=true --raw >/dev/null 2>&1; sleep 0.2; done ) &
fi
crashed=0
for i in $(seq "$N"); do
  m=$(pactl load-module module-pipe-source source_name=bluez_input.X file="$FIFO" format=s16le rate=44100 channels=2) || { echo "iteration $i: load failed"; crashed=1; break; }
  parec --device=bluez_input.X --property=node.dont-fallback=true --raw >/dev/null 2>&1 &
  kp=$!
  sleep "0.$((RANDOM % 9 + 1))"
  case "$CONSUMER" in
    always) kill $kp; sleep 0.2 ;;
    race) kill $kp ;;
    never | sup) : ;;
    *) if [ $((i % 2)) = 0 ]; then kill $kp; fi ;;
  esac
  if [ "$VANISH" = destroy ]; then
    id=$(pw-dump | python3 -c "import json,sys; print(next(o['id'] for o in json.load(sys.stdin) if o.get('info',{}).get('props',{}).get('node.name')=='bluez_input.X'))")
    pw-cli destroy "$id" >/dev/null 2>&1; sleep 0.3          # the module unloads itself with its node
  else
    pactl unload-module "$m" >/dev/null 2>&1
  fi
  pactl info >/dev/null 2>&1 || { echo "iteration $i: pipewire-pulse is gone after the $VANISH"; crashed=1; break; }
  kill $kp 2>/dev/null; wait $kp 2>/dev/null
done
sleep 1
echo "CONSUMER=$CONSUMER VANISH=$VANISH iterations=$i crashed=$crashed"; grep "### pipewire-pulse exited" /tmp/pw-logs/pp.log
exit $crashed
