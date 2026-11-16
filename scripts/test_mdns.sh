#!/usr/bin/env bash
# shellcheck disable=SC2034  # OUT*, LOG*, BR are read inside the eval'd check expressions
# INTEGRATION: the bridge's stdlib mDNS responder against a REAL Avahi 0.8 peer on the isolated lab network.
# Checks: name resolution, DNS-SD browse of _https._tcp, conflict handling (Avahi owns hifi-bridge.local -> responder takes
# hifi-bridge-2.local), goodbye on stop. Prints PASS/FAIL per check; exit code 0 only if all pass.
set -uo pipefail
# NOTE: on this development host Docker cannot deliver signals to containers ("unable to signal init: permission denied"), so
# `docker stop` ends in SIGKILL. Clean stops are therefore requested INSIDE the container (pkill -TERM), as a service manager would.
cd "$(dirname "$0")/../docker"  || exit 1
C="docker compose --profile mdns"
fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
$C build -q avahi >/dev/null
$C rm -sf mdns-responder avahi >/dev/null 2>&1
$C up -d avahi mdns-responder >/dev/null 2>&1
sleep 6
RIP=$($C exec -T mdns-responder python3 -c "from btbridge.net.addresses import local_ipv4; print(local_ipv4()[0])")
echo "responder ip: $RIP"
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
echo "avahi-resolve: $OUT"
[ -n "$RIP" ] || { echo "FAIL responder not running (no IP)"; fail=1; RIP=unset-responder-ip; }
check "avahi resolves hifi-bridge.local to the responder" '[[ "$OUT" == *"$RIP"* ]]'
BR=$($C exec -T avahi timeout 8 avahi-browse -rpt _https._tcp 2>&1)
echo "$BR" | grep '^=' | head -3
check "avahi browse finds the _https._tcp service on port 8443" 'echo "$BR" | grep -q "^=;.*;Hi-Fi\\\\032Bridge\\\\032\\\\040Lab\\\\041;Secure Web Site;local;hifi-bridge.local;$RIP;8443;"'
# conflict: Avahi claims hifi-bridge.local for another (fake) address, then the responder starts
$C exec -T mdns-responder pkill -TERM -f btbridge.net.mdns >/dev/null 2>&1; sleep 3
$C exec -T -d avahi avahi-publish -a -R hifi-bridge.local 172.30.250.250 >/dev/null 2>&1
sleep 3
$C start mdns-responder >/dev/null 2>&1; sleep 7
LOG=$($C logs --no-color mdns-responder 2>&1 | tail -5)
echo "$LOG"
check "responder detects the conflict and takes hifi-bridge-2.local" 'echo "$LOG" | grep -q "mdns name: hifi-bridge-2.local"'
OUT2=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge-2.local 2>&1)
check "avahi resolves hifi-bridge-2.local to the responder" '[[ "$OUT2" == *"$RIP"* ]]'
OUT3=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
check "hifi-bridge.local still belongs to the other device" '[[ "$OUT3" == *"172.30.250.250"* ]]'
# goodbye: after a clean stop Avahi must forget the name quickly (cache flushed by TTL-0 records)
$C exec -T avahi pkill -f avahi-publish >/dev/null 2>&1
$C exec -T mdns-responder pkill -TERM -f btbridge.net.mdns >/dev/null 2>&1; sleep 3
LOG2=$($C logs --no-color mdns-responder 2>&1 | tail -2)
check "responder sends the goodbye on SIGTERM" 'echo "$LOG2" | grep -q "goodbye sent"'
OUT4=$($C exec -T avahi timeout 6 avahi-resolve-host-name -4 hifi-bridge-2.local 2>&1)
check "after stop (goodbye) the name no longer resolves" '[[ "$OUT4" != *"$RIP"* ]]'
$C rm -sf mdns-responder >/dev/null 2>&1

echo "### avahi backend (production preference, DECISIONS #49): bridge publisher + its own avahi-daemon vs. the Avahi peer"
$C exec -T avahi sh -c 'pkill -f avahi-publish; true' >/dev/null 2>&1
$C up -d mdns-avahi-pub >/dev/null 2>&1; sleep 8
PIP=$($C exec -T mdns-avahi-pub python3 -c "from btbridge.net.addresses import local_ipv4; print(local_ipv4()[0])" 2>/dev/null)
[ -n "$PIP" ] || { echo "FAIL avahi backend: publisher not running (no IP)"; fail=1; PIP=unset-publisher-ip; }
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
echo "avahi-resolve: $OUT (publisher ip $PIP)"
check "avahi backend: peer resolves hifi-bridge.local to the publisher" '[[ "$OUT" == *"$PIP"* ]]'
BR=$($C exec -T avahi timeout 8 avahi-browse -rpt _https._tcp 2>&1)
check "avahi backend: peer browses the _https._tcp service" 'echo "$BR" | grep -q "^=;.*;Hi-Fi\\\\032Bridge\\\\032\\\\040Avahi\\\\041;Secure Web Site;local;hifi-bridge.local;$PIP;8443;"'
$C exec -T mdns-avahi-pub pkill -TERM -f btbridge.net.avahi >/dev/null 2>&1; sleep 3
LOGA=$($C logs --no-color mdns-avahi-pub 2>&1 | tail -3)
check "avahi backend: goodbye on SIGTERM" 'echo "$LOGA" | grep -q "goodbye sent"'
OUT=$($C exec -T avahi timeout 6 avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
check "avahi backend: after stop the name no longer resolves" '[[ "$OUT" != *"$PIP"* ]]'
$C rm -sf mdns-avahi-pub >/dev/null 2>&1
$C exec -T -d avahi avahi-publish -a -R hifi-bridge.local 172.30.250.251 >/dev/null 2>&1; sleep 3
$C up -d mdns-avahi-pub >/dev/null 2>&1; sleep 10
LOGB=$($C logs --no-color mdns-avahi-pub 2>&1 | tail -4)
echo "$LOGB"
check "avahi backend: conflict -> hifi-bridge-2.local" 'echo "$LOGB" | grep -q "mdns name: hifi-bridge-2.local"'
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge-2.local 2>&1)
check "avahi backend: peer resolves hifi-bridge-2.local to the publisher" '[[ "$OUT" == *"$PIP"* ]]'
$C rm -sf mdns-responder mdns-avahi-pub avahi >/dev/null 2>&1
exit $fail
