#!/usr/bin/env bash
# EXPERIMENT (lab only, btbridge-lab): freeze ONE sync-group member's Squeezelite (SIGSTOP) while the bridge streams to the group
# with the audio-path trace on, then thaw it. Shows the lab signature of a player-side stall (docs/STREAM_TRACE.md, G1-S).
# usage: scripts/exp_group_member_stall.sh [stop_at_s=30] [stop_for_s=8] [total_s=75]   (whole seconds)
# Touches only the lab project's player3 container; always sends SIGCONT on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose
STOP_AT=${1:-30} STOP_FOR=${2:-8} TOTAL=${3:-75}
scripts/lab_up.sh -q >/dev/null
cd docker
P3=$(docker compose ps -q player3)
[ -n "$P3" ] || { echo "lab player3 not running" >&2; exit 2; }
thaw() { docker exec "$P3" sh -c 'pkill -CONT -x squeezelite' >/dev/null 2>&1 || true; }
trap thaw EXIT
T0=$(date +%s.%N)
( sleep "$STOP_AT"; docker exec "$P3" sh -c 'pkill -STOP -x squeezelite'; echo "EXP player3 STOPPED at +${STOP_AT}s";
  sleep "$STOP_FOR"; thaw; echo "EXP player3 CONTINUED at +$((STOP_AT + STOP_FOR))s" ) &
docker compose run --rm -T runner python3 -m btkit.exp_group_member_stall "$T0" "$STOP_AT" "$STOP_FOR" "$TOTAL"
wait
