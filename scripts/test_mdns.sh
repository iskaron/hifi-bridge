#!/usr/bin/env bash
# shellcheck disable=SC2034  # OUT*, LOG*, BR are read inside the eval'd check expressions
# INTEGRATION: the bridge's stdlib mDNS responder against a REAL Avahi 0.8 peer on the isolated lab network.
# Checks: name resolution, DNS-SD browse of _https._tcp, conflict handling (Avahi owns hifi-bridge.local -> responder takes
# hifi-bridge-2.local), goodbye on stop. Prints PASS/FAIL per check; exit code 0 only if all pass.
set -uo pipefail
# NOTE: on this development host Docker cannot deliver signals to containers ("unable to signal init: permission denied"), so
# `docker stop` ends in SIGKILL. Clean stops are therefore requested INSIDE the container (pkill -TERM), as a service manager would.
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"
hb_require --compose
hb_ensure_images btbridge-lab:dev btbridge-avahi:dev
cd "$(dirname "$0")/../docker"  || exit 1
C="docker compose --profile mdns"
fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
# wait_for SECONDS 'condition' - poll every 0.5 s instead of fixed sleeps (fixed sleeps made this script timing-dependent)
wait_for() { local end=$(( $(date +%s) + $1 )); while ! eval "$2" >/dev/null 2>&1; do [ "$(date +%s)" -ge "$end" ] && return 1; sleep 0.5; done; }
logs() { $C logs --no-color "$1" 2>&1; }
count() { logs "$1" | grep -c "$2"; }
# gone NAME IP - after a goodbye (TTL 0) RFC 6762 lets caches keep a record for 1 s; without a goodbye it would stay for its TTL (120 s).
# Polls up to 10 s and records how long it took.
gone() { local t0; t0=$(date +%s.%N); if wait_for 10 "! \$C exec -T avahi timeout 3 avahi-resolve-host-name -4 $1 | grep -q '$2'"; then
  GONE_S=$(awk -v a="$(date +%s.%N)" -v b="$t0" "BEGIN{printf \"%.1f\", a-b}"); return 0; fi; GONE_S=">10"; return 1; }
diag() { echo "  diag: pkill rc=$1; processes:"; $C exec -T "$2" ps -eo pid,stat,args 2>&1 | sed 's/^/    /'; logs "$2" | tail -4 | sed 's/^/    /'; }
$C build -q avahi >/dev/null
$C rm -sf mdns-responder mdns-avahi-pub mdns-nogw avahi >/dev/null 2>&1
$C up -d avahi mdns-responder >/dev/null 2>&1
wait_for 30 'logs mdns-responder | grep -q "mdns name:"'
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
G0=$(count mdns-responder "goodbye sent")
$C exec -T mdns-responder pkill -TERM -f btbridge.net.mdns >/dev/null 2>&1
wait_for 20 '[ "$(count mdns-responder "goodbye sent")" -gt "$G0" ]'
wait_for 20 '! $C exec -T avahi timeout 3 avahi-resolve-host-name -4 hifi-bridge.local | grep -q "$RIP"'
$C exec -T -d avahi avahi-publish -a -R hifi-bridge.local 172.30.250.250 >/dev/null 2>&1
wait_for 20 '$C exec -T avahi timeout 3 avahi-resolve-host-name -4 hifi-bridge.local | grep -q 172.30.250.250'
$C start mdns-responder >/dev/null 2>&1
wait_for 30 'logs mdns-responder | grep -q "mdns name: hifi-bridge-2.local"'
LOG=$(logs mdns-responder | tail -5)
echo "$LOG"
check "responder detects the conflict and takes hifi-bridge-2.local" 'echo "$LOG" | grep -q "mdns name: hifi-bridge-2.local"'
OUT2=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge-2.local 2>&1)
check "avahi resolves hifi-bridge-2.local to the responder" '[[ "$OUT2" == *"$RIP"* ]]'
OUT3=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
check "hifi-bridge.local still belongs to the other device" '[[ "$OUT3" == *"172.30.250.250"* ]]'
# goodbye: after a clean stop Avahi must forget the name quickly (cache flushed by TTL-0 records)
$C exec -T avahi pkill -f avahi-publish >/dev/null 2>&1
G1=$(count mdns-responder "goodbye sent")
$C exec -T mdns-responder pkill -TERM -f btbridge.net.mdns >/dev/null 2>&1; PK=$?
wait_for 20 '[ "$(count mdns-responder "goodbye sent")" -gt "$G1" ]' || diag "$PK" mdns-responder
check "responder sends the goodbye on SIGTERM" '[ "$(count mdns-responder "goodbye sent")" -gt "$G1" ]'
gone hifi-bridge-2.local "$RIP"; G=$?
check "after stop (goodbye) the name no longer resolves (gone after ${GONE_S} s)" '[ $G -eq 0 ]'
$C rm -sf mdns-responder >/dev/null 2>&1

echo "### avahi backend (production preference, DECISIONS #49): bridge publisher + its own avahi-daemon vs. the Avahi peer"
$C exec -T avahi sh -c 'pkill -f avahi-publish; true' >/dev/null 2>&1
$C up -d mdns-avahi-pub >/dev/null 2>&1
wait_for 30 'logs mdns-avahi-pub | grep -q "mdns name:"'
PIP=$($C exec -T mdns-avahi-pub python3 -c "from btbridge.net.addresses import local_ipv4; print(local_ipv4()[0])" 2>/dev/null)
[ -n "$PIP" ] || { echo "FAIL avahi backend: publisher not running (no IP)"; fail=1; PIP=unset-publisher-ip; }
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge.local 2>&1)
echo "avahi-resolve: $OUT (publisher ip $PIP)"
check "avahi backend: peer resolves hifi-bridge.local to the publisher" '[[ "$OUT" == *"$PIP"* ]]'
BR=$($C exec -T avahi timeout 8 avahi-browse -rpt _https._tcp 2>&1)
check "avahi backend: peer browses the _https._tcp service" 'echo "$BR" | grep -q "^=;.*;Hi-Fi\\\\032Bridge\\\\032\\\\040Avahi\\\\041;Secure Web Site;local;hifi-bridge.local;$PIP;8443;"'
$C exec -T mdns-avahi-pub pkill -TERM -f btbridge.net.avahi >/dev/null 2>&1; PK=$?
wait_for 20 'logs mdns-avahi-pub | grep -q "goodbye sent"' || diag "$PK" mdns-avahi-pub
check "avahi backend: goodbye on SIGTERM" 'logs mdns-avahi-pub | grep -q "goodbye sent"'
gone hifi-bridge.local "$PIP"; G=$?
check "avahi backend: after stop the name no longer resolves (gone after ${GONE_S} s)" '[ $G -eq 0 ]'
$C rm -sf mdns-avahi-pub >/dev/null 2>&1
$C exec -T -d avahi avahi-publish -a -R hifi-bridge.local 172.30.250.251 >/dev/null 2>&1
wait_for 20 '$C exec -T avahi timeout 3 avahi-resolve-host-name -4 hifi-bridge.local | grep -q 172.30.250.251'
$C up -d mdns-avahi-pub >/dev/null 2>&1
wait_for 30 'logs mdns-avahi-pub | grep -q "mdns name: hifi-bridge-2.local"'
LOGB=$(logs mdns-avahi-pub | tail -4)
echo "$LOGB"
check "avahi backend: conflict -> hifi-bridge-2.local" 'echo "$LOGB" | grep -q "mdns name: hifi-bridge-2.local"'
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-bridge-2.local 2>&1)
check "avahi backend: peer resolves hifi-bridge-2.local to the publisher" '[[ "$OUT" == *"$PIP"* ]]'

echo "### a device on a LAN without a gateway (no default route) must still announce (the bridge's own responder)"
$C exec -T avahi sh -c 'pkill -f avahi-publish; true' >/dev/null 2>&1
$C up -d mdns-nogw >/dev/null 2>&1
wait_for 30 'logs mdns-nogw | grep -q "mdns name:"'
NIP=$($C exec -T mdns-nogw python3 -c "from btbridge.net.addresses import local_ipv4; print(local_ipv4()[0])" 2>/dev/null)
[ -n "$NIP" ] || { echo "FAIL gateway-less device: responder not running (no IP)"; fail=1; NIP=unset-nogw-ip; }
check "gateway-less device really has no default route" '! $C exec -T mdns-nogw awk '"'"'$2 == "00000000"'"'"' /proc/net/route | grep -q .'
OUT=$($C exec -T avahi avahi-resolve-host-name -4 hifi-nogw.local 2>&1)
echo "avahi-resolve: $OUT (responder ip $NIP)"
check "gateway-less device: peer resolves hifi-nogw.local" '[[ "$OUT" == *"$NIP"* ]]'
BR=$($C exec -T avahi timeout 8 avahi-browse -rpt _https._tcp 2>&1)
check "gateway-less device: peer browses its _https._tcp service" 'echo "$BR" | grep -q "^=;.*;Hi-Fi\\\\032Bridge\\\\032\\\\040no\\\\032gateway\\\\041;Secure Web Site;local;hifi-nogw.local;$NIP;8443;"'
$C rm -sf mdns-nogw >/dev/null 2>&1

echo "### isolation of the mDNS test network (not internal, but no host address and no masquerading)"
NETINFO=$(docker network inspect btbridge-lab_mdns --format '{{.Internal}} masq={{index .Options "com.docker.network.bridge.enable_ip_masquerade"}} inhibit={{index .Options "com.docker.network.bridge.inhibit_ipv4"}}' 2>&1)
echo "$NETINFO"
check "mDNS test network: bridge without host address, no masquerading" '[[ "$NETINFO" == *"masq=false"* && "$NETINFO" == *"inhibit=true"* ]]'
# Docker still reserves .1 as the "gateway" and installs a default route to it, but nobody holds that address (inhibit_ipv4): connections to it
# and to a documentation address (RFC 5737, never routed) must fail, from a container on that network.
GWIP=$($C exec -T avahi python3 -c "
import socket, struct
for line in open('/proc/net/route').read().splitlines()[1:]:
    f = line.split()
    if f[1] == '00000000':
        print(socket.inet_ntoa(struct.pack('<I', int(f[2], 16)))); break" 2>/dev/null)
echo "default gateway on the test network: ${GWIP:-none} (reserved by Docker, held by nobody)"
check "mDNS test network: nothing answers for the gateway or for an outside address" '$C exec -T avahi python3 -c "
import socket, sys
for host in (sys.argv[1], \"192.0.2.1\"):
    if not host: continue
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((host, 80)); sys.exit(1)
    except OSError:
        pass
" "$GWIP"'
$C rm -sf mdns-responder mdns-avahi-pub mdns-nogw avahi >/dev/null 2>&1
exit $fail
