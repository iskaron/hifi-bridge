#!/usr/bin/env bash
# EXPERIMENT (lab only, btbridge-lab): stall ONLY the audio data connection of one sync-group member while the bridge streams to
# the group (trace on); its Slimproto control connection stays up, so LMS keeps it "connected". Stand-in for a member whose Wi-Fi
# data path stops delivering (G1-S, docs/STREAM_TRACE.md). No NET_ADMIN: an extra lab player "player4" (MAC 02:00:00:00:00:04)
# runs behind the userspace relay btkit.stall_relay; the group is player1 + player2 + player4.
# usage: scripts/exp_group_member_datastall.sh [stop_at_s=30] [stop_for_s=40] [total_s=110] [small_buffer_kb]   (whole seconds)
#   small_buffer_kb (e.g. 128:256): also add "player5", a healthy member whose Squeezelite runs with that small stream:output buffer
#   (-b), so the group is player1 + player2 + player4 (stalled) + player5 - tests whether a small-buffer member turns a SHORT stall
#   into LMS's 30 s rebuffer give-up.
# Touches only the lab project; removes the stall flag and the extra player container on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose
STOP_AT=${1:-30} STOP_FOR=${2:-40} TOTAL=${3:-110} SMALL=${4:-}
scripts/lab_up.sh -q >/dev/null
cd docker
NAME=btbridge-lab-player4-relay NAME5=btbridge-lab-player5-smallbuf
FLAG=/captures/player4/stall
docker rm -f "$NAME" "$NAME5" >/dev/null 2>&1 || true
docker compose run -d --rm --no-deps --name "$NAME" --entrypoint sh player3 -c \
  "mkdir -p /captures/player4 && rm -f $FLAG && (python3 -m btkit.stall_relay lms $FLAG &) && sleep 1 && \
   exec python3 -m btkit.run_player --name lab-player4 --mac 02:00:00:00:00:04 --server 127.0.0.1:3483 --capture /captures/player4" >/dev/null
MEMBERS=player4
if [ -n "$SMALL" ]; then
  docker compose run -d --rm --no-deps --name "$NAME5" player3 python3 -m btkit.run_player --name lab-player5-smallbuf \
    --mac 02:00:00:00:00:05 --server lms:3483 --capture /captures/player5 --extra -b "$SMALL" >/dev/null
  MEMBERS=player4,player5
fi
cleanup() { docker exec "$NAME" rm -f "$FLAG" >/dev/null 2>&1 || true; docker rm -f "$NAME" "$NAME5" >/dev/null 2>&1 || true; }
trap cleanup EXIT
T0=$(date +%s.%N)
( sleep "$STOP_AT"; docker exec "$NAME" touch "$FLAG"; echo "EXP player4 DATA STALLED at +${STOP_AT}s";
  sleep "$STOP_FOR"; docker exec "$NAME" rm -f "$FLAG"; echo "EXP player4 data RESUMED at +$((STOP_AT + STOP_FOR))s" ) &
docker compose run --rm -T runner python3 -m btkit.exp_group_member_stall "$T0" "$STOP_AT" "$STOP_FOR" "$TOTAL" "$MEMBERS" datastall
wait
LMS=$(docker compose ps -q lms)
echo "--- lab LMS server.log (player.source, this run)"
docker exec "$LMS" sh -c 'tail -n 400 /config/logs/server.log' | grep -E "decoder: |jumpToTime|new playing state|playerBufferReady" | tail -n 30 || true
