#!/bin/sh
# Runs INSIDE the disposable Docker-in-Docker container (scripts/test_docker_versions.sh). $1 = compose binary.
# Prints one line per observation and "RESULT: PASS|FAIL".
dc=$1; f=/matrix/topology.yml; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FAIL $1: expected $2, got $3"; fail=1; fi; }
echo "engine $(docker version --format '{{.Server.Version}} (API {{.Server.APIVersion}})')  compose $($dc version --short)"
$dc -f $f down -v --remove-orphans >/dev/null 2>&1
out=$($dc -f $f up -d 2>&1) || { echo "  FAIL up: $(echo "$out" | tail -2 | tr '\n' ' ')"; echo "RESULT: FAIL"; exit 1; }
sleep 2
# decoy "Lyrion on the host": listens on all addresses of the Docker host (here: the dind container) on 9000 and 3483
for p in 9000 3483; do (while true; do nc -l -p $p </dev/null >/dev/null 2>&1; done) & done
sleep 1
gwdef=$(docker exec hifi-entry-topology-gw-1 sh -c "ip route | grep '^default' | sed 's/.* dev //; s/ .*//'")
gwnet=$(docker exec hifi-entry-topology-gw-1 sh -c "ip -4 -o addr show $gwdef | awk '{print \$4}'")
chk "gateway's default route is on the non-internal network edge (172.29.250.x)" yes "$(case $gwnet in 172.29.250.*) echo yes;; *) echo "no: $gwnet";; esac)"
chk "internal-only service: default route points at a real gateway or does not exist (no usable route out)" yes \
  "$(docker exec hifi-entry-topology-srv-1 sh -c 'ip route | grep -c "^default" | grep -qx 0 && echo yes || echo "default route present (Engine <= 24 adds an unusable one)"')"
for n in demo edge control; do
  id=$(docker network inspect hifi-entry-topology_$n --format '{{.Id}}' | cut -c1-12)
  a=$(ip -4 -o addr show br-$id 2>/dev/null | awk '{print $4}')
  if [ $n = demo ]; then chk "host has NO IPv4 address on the internal network's bridge" "" "$a"; else chk "host has an address on $n (expected, normal bridge)" yes "$([ -n "$a" ] && echo yes || echo no)"; fi
done
chk "port published to 127.0.0.1:18999 reaches the gateway" ok "$(wget -q -T 3 -O /dev/null http://127.0.0.1:18999/ 2>/dev/null; wget -q -S -T 3 -O /dev/null http://127.0.0.1:18999/ 2>&1 | grep -q 'HTTP/1' && echo ok || echo no)"
for p in 9000 3483; do
  for cand in 172.29.251.1 172.29.251.254; do
    chk "internal service -> decoy host service ${cand}:$p" blocked "$(docker exec hifi-entry-topology-srv-1 sh -c "nc -w 2 -z $cand $p && echo CONNECTED || echo blocked")"
  done
  chk "POSITIVE CONTROL: service on a normal bridge reaches the same decoy via the host address 172.29.249.1:$p" CONNECTED \
    "$(docker exec hifi-entry-topology-ctl-1 sh -c "nc -w 2 -z 172.29.249.1 $p && echo CONNECTED || echo blocked")"
done
kill $(jobs -p) 2>/dev/null; pkill nc 2>/dev/null
$dc -f $f down -v --remove-orphans >/dev/null 2>&1
[ $fail = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $fail
