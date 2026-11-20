#!/usr/bin/env bash
# Network management with the REAL tools (docs/NETWORK.md "Evidence"), all in disposable containers named hifi-net-* on own internal
# networks without a host address. Nothing touches the host network, no kernel module is loaded, no Wi-Fi hardware is needed.
#  1. tests/net in one container (own namespace, CAP_NET_ADMIN): nft/dnsmasq/hostapd/wpa_supplicant parse the generated files,
#     systemd-networkd applies generated addresses, netlink reads them back, the control-socket client talks to a real wpa_supplicant,
#     the agent sets a real Avahi host name, systemd-analyze verifies the units.
#  2. live firewall + DHCP across containers: a "device" with the hotspot ruleset and the generated dnsmasq, a LAN client and a hotspot
#     client probing ports, ICMP and a DHCP lease; UDP probes from a spoofed source port 3483 (only the discovery reply port may be
#     reachable, review N-8) and the bridge's real Lyrion discovery behind the firewall against a fake Lyrion server on the LAN.
# Usage: scripts/run_net.sh [pytest args]     Output: reports/evidence/network_realtools.txt
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-net:dev || exit 1
IMG=btbridge-net:dev
LIM=(--cpus 2 --memory 1g)
OUT=reports/evidence/network_realtools.txt
NI=(--internal -o com.docker.network.bridge.inhibit_ipv4=true)
# The hotspot test network cannot be --internal: Docker's isolation rules for internal networks drop packets from 0.0.0.0, i.e. every
# DHCP DISCOVER. It still gets NO host address and NO masquerading, so its containers have no route anywhere (checked below).
HI=(-o com.docker.network.bridge.inhibit_ipv4=true -o com.docker.network.bridge.enable_ip_masquerade=false)
cleanup() {
  local c
  for c in hifi-net-tools hifi-net-dev hifi-net-lanclient hifi-net-hsclient hifi-net-lms; do
    for _ in 1 2 3; do docker container inspect "$c" >/dev/null 2>&1 || break; docker rm -f "$c" >/dev/null 2>&1 || sleep 1; done
  done
  for n in hifi-net-lan hifi-net-hs; do
    for _ in 1 2 3; do docker network inspect "$n" >/dev/null 2>&1 || break; docker network rm "$n" >/dev/null 2>&1 || sleep 1; done
  done
  return 0
}
trap cleanup EXIT
cleanup
mkdir -p reports/evidence
{
  echo "# network management with real tools - $(date -u +%Y-%m-%dT%H:%M:%SZ) - label INTEGRATION (Docker, no Wi-Fi hardware)"
  docker run --rm --network none "$IMG" sh -c 'echo "nft: $(nft --version)"; echo "dnsmasq: $(dnsmasq --version | head -1)";
    echo "hostapd: $(hostapd -v 2>&1 | head -1)"; echo "wpa_supplicant: $(wpa_supplicant -v | head -1)"; echo "iw: $(iw --version)";
    echo "systemd-networkd: $(/usr/lib/systemd/systemd-networkd --version | head -1)"; echo "avahi: $(avahi-daemon --version)"'
} > "$OUT" 2>&1

echo "### 1. tests/net (single container, own namespace)" | tee -a "$OUT"
docker run --rm --name hifi-net-tools "${LIM[@]}" --network none --cap-add NET_ADMIN --cap-add NET_RAW --cap-add NET_BROADCAST -v "$PWD":/work "$IMG" \
  python3 -m pytest -p no:cacheprovider -v tests/net "$@" 2>&1 | tee -a "$OUT"
T=${PIPESTATUS[0]}

echo "### 2. live firewall and hotspot DHCP (three containers)" | tee -a "$OUT"
F=0
docker network create "${NI[@]}" --subnet 192.168.77.0/24 --gateway 192.168.77.254 hifi-net-lan >/dev/null || F=1
docker network create "${HI[@]}" --subnet 10.42.0.0/24 --gateway 10.42.0.254 hifi-net-hs >/dev/null || F=1
docker create --name hifi-net-dev "${LIM[@]}" --network hifi-net-lan --ip 192.168.77.10 --cap-add NET_ADMIN --cap-add NET_RAW \
  -v "$PWD":/work:ro "$IMG" python3 /work/tests/net/fw_device.py >/dev/null || F=1
docker network connect --ip 10.42.0.1 hifi-net-hs hifi-net-dev || F=1
docker start hifi-net-dev >/dev/null || F=1
for _ in $(seq 40); do docker logs hifi-net-dev 2>&1 | grep -q '^ready' && break; sleep 0.5; done
docker logs hifi-net-dev 2>&1 | tee -a "$OUT"
probe() {   # name network args...
  local name=$1 net=$2; shift 2
  docker run --rm --name "$name" "${LIM[@]}" --network "$net" --cap-add NET_RAW --cap-add NET_ADMIN -v "$PWD":/work:ro "$IMG" "$@" 2>&1 | tee -a "$OUT"
  return "${PIPESTATUS[0]}"
}
probe hifi-net-lanclient hifi-net-lan python3 /work/tests/net/fw_probe.py 192.168.77.10 --open 80,443,8090 --closed 2222,53 --ping \
  --udp-sport 3483 --udp-open 5353 --udp-closed 5355,40000 || F=1
# Lyrion discovery still works through the narrowed rule: a fake Lyrion server on the LAN answers the device's broadcast
docker run -d --name hifi-net-lms "${LIM[@]}" --network hifi-net-lan --ip 192.168.77.20 -v "$PWD":/work:ro "$IMG" \
  python3 /work/tests/net/fw_lms.py >/dev/null || F=1
for _ in $(seq 40); do docker logs hifi-net-lms 2>&1 | grep -q '^ready' && break; sleep 0.5; done
docker exec hifi-net-dev python3 /work/tests/net/fw_discover.py 192.168.77.20 2>&1 | tee -a "$OUT"
[ "${PIPESTATUS[0]}" -eq 0 ] || F=1
docker logs hifi-net-lms 2>&1 | tee -a "$OUT"
probe hifi-net-hsclient hifi-net-hs sh -c 'python3 /work/tests/net/fw_probe.py 10.42.0.1 --open 80,443 --closed 8090,2222,53 --ping \
  && busybox udhcpc -i eth0 -n -q -f -t 4 -T 2 -s /work/tests/net/udhcpc_report.sh; cat /tmp/lease.txt; ! grep -q FAIL /tmp/lease.txt && grep -q "PASS served" /tmp/lease.txt \
  && { busybox ping -c 1 -W 2 192.168.77.10 >/dev/null 2>&1 && echo "FAIL hotspot client reaches the LAN" || echo "PASS hotspot client cannot reach the LAN"; } \
  | tee /dev/stderr | grep -q PASS' || F=1
echo "network exit codes: tests=$T live=$F" | tee -a "$OUT"
[ "$T" -eq 0 ] && [ "$F" -eq 0 ]
