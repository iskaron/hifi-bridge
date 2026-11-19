#!/usr/bin/env bash
# Runtime isolation check of a running compose project (lab or demo): no container may be able to reach a Lyrion server (or any other
# service) on the Docker host or the LAN. Regression test for the finding of DECISIONS #71: through the host's own address on an internal
# Docker network, the setup wizard's UDP discovery was answered by a REAL Lyrion server on the host.
#
#   scripts/check_isolation.sh PROJECT [--allow-edge SERVICE]...      exit 0 = isolated, 1 = a check failed, 2 = cannot run
#
# For every network of the project:  `internal`, bridge option inhibit_ipv4 (the host has no address on it) - except networks that only
# the --allow-edge services (the demo's gateway, which publishes to 127.0.0.1) are attached to.
# On the host: no IPv4 address on the bridge of an internal network (needs the `ip` command; skipped with a notice otherwise).
# In every container that is attached ONLY to internal networks: TCP 9000 and 3483 and a UDP 3483 discovery packet to the candidate
# host addresses (network gateway, first address of each subnet) fail; the discovery BROADCAST is answered only by containers of this
# project; the names host.docker.internal / gateway.docker.internal do not resolve.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
PROJECT=${1:?usage: check_isolation.sh PROJECT [--allow-edge SERVICE]...}; shift
ALLOW=" "
while [ $# -gt 0 ]; do case "$1" in --allow-edge) ALLOW="$ALLOW$2 "; shift ;; esac; shift; done
hb_require
rc=0
fail() { echo "FAIL  $*"; rc=1; }
ok() { echo "ok    $*"; }

mapfile -t CIDS < <(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")
[ "${#CIDS[@]}" -gt 0 ] || { echo "no running containers in project $PROJECT"; exit 2; }

# ---- networks (static properties as the daemon sees them)
mapfile -t NETS < <(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")
declare -A NET_INTERNAL_ONLY=()
for n in "${NETS[@]}"; do
  name=$(docker network inspect "$n" --format '{{.Name}}')
  internal=$(docker network inspect "$n" --format '{{.Internal}}')
  inhibit=$(docker network inspect "$n" --format '{{index .Options "com.docker.network.bridge.inhibit_ipv4"}}')
  svcs=$(docker network inspect "$n" --format '{{range .Containers}}{{.Name}} {{end}}')
  only_allowed=1
  for c in $svcs; do
    s=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$c" 2>/dev/null)
    case "$ALLOW" in *" $s "*) ;; *) only_allowed=0 ;; esac
  done
  if [ "$internal" = true ] && [ "$inhibit" = true ]; then
    ok "network $name: internal, inhibit_ipv4 (the host has no address on it)"
    NET_INTERNAL_ONLY[$name]=1
    br="br-$(docker network inspect "$n" --format '{{.Id}}' | cut -c1-12)"
    if command -v ip >/dev/null 2>&1; then
      if ip -4 -o addr show "$br" 2>/dev/null | grep -q inet; then fail "the host has an IPv4 address on $br (network $name)"; else ok "host: no IPv4 address on $br"; fi
    else
      echo "note  'ip' not available on this host: bridge address check skipped (the container probes below still apply)"
    fi
  elif [ -n "$svcs" ] && [ "$only_allowed" = 1 ] && [ "$internal" != true ]; then
    ok "network $name: not internal, attached only to the allowed edge service(s):$svcs"
  else
    fail "network $name: internal=$internal inhibit_ipv4=${inhibit:-unset}, attached: ${svcs:-nothing} - a reachable host address is possible"
  fi
done

# ---- containers attached only to internal networks must not reach the host or the LAN
PROBE='
import socket, sys, time
hosts, own = sys.argv[1].split(","), set(sys.argv[2].split(","))
bad = []
for h in hosts:
    if not h:
        continue
    for port in (9000, 3483):
        s = socket.socket(); s.settimeout(1.0)
        try:
            s.connect((h, port)); bad.append("tcp %s:%d connected" % (h, port))
        except OSError:
            pass
        finally:
            s.close()
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(1.0)
    try:
        u.sendto(b"e\x00\x00\x00\x00IPAD\x00NAME\x00JSON\x00", (h, 3483)); d, a = u.recvfrom(512); bad.append("udp %s:3483 answered by %s" % (h, a[0]))
    except OSError:
        pass
    finally:
        u.close()
b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); b.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); b.settimeout(0.5)
try:
    b.sendto(b"e\x00\x00\x00\x00IPAD\x00NAME\x00JSON\x00", ("255.255.255.255", 3483))
    end = time.time() + 1.5
    while time.time() < end:
        try:
            d, a = b.recvfrom(512)
        except OSError:
            continue
        if a[0] not in own:
            bad.append("discovery broadcast answered by %s (not a container of this project)" % a[0])
except OSError:
    pass
for name in ("host.docker.internal", "gateway.docker.internal"):
    try:
        bad.append("%s resolves to %s" % (name, socket.gethostbyname(name)))
    except OSError:
        pass
print("\n".join(bad))
'
OWN=""
for c in "${CIDS[@]}"; do
  OWN="$OWN$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}},{{end}}' "$c")"
done
# The probe runs in a throw-away sidecar attached to each internal network (same vantage point as the project's own containers; the Lyrion and
# Mosquitto images have no Python). The sidecar uses the entry-test image and is removed afterwards. Services attached to an allowed edge
# network are reported, not probed.
for c in "${CIDS[@]}"; do
  svc=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$c")
  nets=$(docker inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c")
  for n in $nets; do
    [ -n "${NET_INTERNAL_ONLY[$n]:-}" ] && continue
    case "$ALLOW" in *" $svc "*) echo "note  $svc is an allowed edge service (it only forwards fixed targets)" ;; *) fail "$svc is attached to $n, which is not internal+inhibit_ipv4" ;; esac
  done
done
hb_ensure_image btbridge-entry:dev >/dev/null
for name in "${!NET_INTERNAL_ONLY[@]}"; do
  gw=$(docker network inspect "$name" --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | tr '\n' ',')
  first=$(docker network inspect "$name" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -E '^[0-9]+\.' | sed -E 's#\.[0-9]+/.*#.1#' | tr '\n' ',')
  cands=""
  # With inhibit_ipv4 the bridge does not take the gateway address, so Docker may give ".1" to one of the project's own containers
  # (seen on Engine 28.0.4 in CI: the demo's Lyrion answered on .1). An address owned by a project container cannot be the host - the
  # check above already proves the host has no address on this bridge - so it is not probed as a host candidate.
  for a in $(echo "$gw$first" | tr ',' '\n' | sort -u); do
    case ",$OWN" in *",$a,"*) echo "note  $a belongs to a container of this project on $name: not a host candidate" ;; *) cands="$cands$a," ;; esac
  done
  out=$(printf '%s' "$PROBE" | docker run --rm -i --network "$name" --cap-drop ALL --read-only \
        btbridge-entry:dev python3 -I - "${cands%,}" "${OWN%,}" 2>&1) || { fail "network $name: probe could not run: $out"; continue; }
  if [ -n "$out" ]; then fail "containers on $name can reach the host or LAN: $(echo "$out" | tr '\n' ';')"
  else ok "network $name: TCP 9000/3483 and UDP 3483 discovery to the candidate host addresses (${cands%,}) and the broadcast reach nothing outside the project"; fi
done
echo "isolation check of project $PROJECT: rc=$rc"
exit $rc
