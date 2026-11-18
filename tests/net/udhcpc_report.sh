#!/bin/sh
# busybox udhcpc script for the live hotspot DHCP check: reports the lease (never configures anything) and checks the policy:
# an address from the hotspot range, NO default router and NO DNS server (no fake captive portal, docs/NETWORK.md).
# shellcheck disable=SC2154  # ip, subnet, router, dns, serverid come from udhcpc in the environment
case "$1" in
  bound|renew)
    exec >>/tmp/lease.txt
    echo "lease: ip=$ip mask=$subnet router=${router:-none} dns=${dns:-none} server=$serverid"
    rc=0
    case "$ip" in 10.42.0.1[0-9]|10.42.0.[2-5][0-9]|10.42.0.60) echo "PASS lease address in 10.42.0.10-60" ;; *) echo "FAIL lease address $ip"; rc=1 ;; esac
    [ -z "${router:-}" ] && echo "PASS no default router" || { echo "FAIL router $router"; rc=1; }
    [ -z "${dns:-}" ] && echo "PASS no DNS server" || { echo "FAIL dns $dns"; rc=1; }
    [ "$serverid" = "10.42.0.1" ] && echo "PASS served by the device" || { echo "FAIL server $serverid"; rc=1; }
    exit $rc ;;
esac
exit 0
