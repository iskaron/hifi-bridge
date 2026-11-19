#!/bin/sh
# Runs INSIDE the disposable Docker-in-Docker container. $1 = compose binary. The compose CLI calls are the ones scripts/demo.sh makes
# (health is read with `docker inspect`, not with `ps --format {{.Health}}`, which needs a newer Compose).
dc=$1; f=/matrix/features.yml; fail=0
ok() { echo "  ok   $1"; }; bad() { echo "  FAIL $1: $2"; fail=1; }
DC="$dc -p hifi-entry-features -f $f"
echo "compose $($dc version --short)"
$DC down -v --remove-orphans >/dev/null 2>&1
o=$($DC config -q 2>&1) && ok "config (anchors, merge keys, interpolation, profiles, healthcheck, depends_on conditions, name:)" || bad config "$o"
o=$($DC up -d --remove-orphans 2>&1) && ok "up -d --remove-orphans (service_completed_successfully, service_healthy)" || bad "up -d" "$(echo "$o" | tail -2 | tr '\n' ' ')"
sleep 7
id=$($DC ps -q srv 2>&1); [ -n "$id" ] && ok "ps -q <service>" || bad "ps -q" "$id"
o=$($DC ps -q --status running srv 2>&1); [ -n "$o" ] && ok "ps -q --status running <service>" || bad "ps --status" "$o"
h=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id" 2>&1); [ "$h" = healthy ] && ok "health via docker inspect ($h)" || bad "health" "$h"
o=$($DC --profile ha ps 2>&1) && echo "$o" | grep -q srv && ok "--profile ha ps" || bad "ps" "$o"
o=$($DC exec -T srv sh -c 'echo hi' 2>&1); [ "$o" = hi ] && ok "exec -T" || bad "exec -T" "$o"
o=$($DC cp srv:/etc/hostname /tmp/hn.x 2>&1) && ok "cp <service>:path" || bad "cp" "$o"
o=$($DC --profile ha up -d opt 2>&1) && ok "--profile ha up -d <service>" || bad "--profile" "$o"
o=$($DC --profile ha stop 2>&1) && ok "--profile ha stop" || bad "stop" "$o"
o=$($DC logs --tail 5 srv 2>&1) && ok "logs --tail" || bad "logs" "$o"
o=$($DC --profile ha config --images 2>&1) && echo "$o" | grep -q alpine && ok "config --images (used to pull pinned images)" || bad "config --images" "$o"
o=$($DC restart srv 2>&1) && ok restart || bad restart "$o"
o=$($DC --profile ha down -v --remove-orphans 2>&1) && ok "down -v --remove-orphans" || bad down "$o"
[ $fail = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $fail
