#!/usr/bin/env bash
# Manual guest-experience walkthrough with a SIMULATED phone (no hardware). Leaves the demo container running.
set -euo pipefail
cd "$(dirname "$0")/../docker"
../scripts/lab_up.sh >/dev/null
docker rm -f demo >/dev/null 2>&1 || true
docker compose run -d --name demo -T runner python3 -m btkit.demo >/dev/null
sleep 3
C="docker compose run --rm -T runner curl -s"
echo "connect:"; $C http://demo:8099/connect; echo; echo "play:"; $C http://demo:8099/play; echo
echo "(wait ~3 s) LMS status of player1:"; sleep 3
docker compose run --rm -T runner python3 -c "from btkit.lms_rpc import Lms,PLAYERS;s=Lms().req(PLAYERS['player1'],'status','-',1,'tags:u');print(s['mode'],s['playlist_loop'][0]['url'].split('/s/')[0]+'/s/***')"
echo "Further: docker compose run --rm -T runner curl -s http://demo:8099/{pause|notify|disconnect|diag|status}"
echo "Stop: docker rm -f demo"
