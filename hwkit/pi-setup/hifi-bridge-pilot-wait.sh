#!/bin/sh
# ExecStartPre of the pilot user unit: wait (bounded) until BlueZ answers on the system bus and a default route exists, so a start at
# boot does not race bluetooth.service and the network (a user unit cannot order itself after system units). NEVER fails: after the
# deadline the bridge starts anyway and its own reconnect loops (BlueZ re-sync, LMS poll, PipeWire retry) take over.
# HIFI_WAIT_MAX_S: deadline in seconds (default 60); HIFI_WAIT_STEP_S: poll interval (default 1; tests use 0).
set -u
MAX=${HIFI_WAIT_MAX_S:-60}; STEP=${HIFI_WAIT_STEP_S:-1}
bluez_up() { busctl --system --no-pager --timeout=5 get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Powered >/dev/null 2>&1; }
route_up() { [ -n "$(ip -o route show default 2>/dev/null)" ]; }
waited=0; bt=no; net=no
while :; do
  [ "$bt" = yes ] || { bluez_up && bt=yes; }
  [ "$net" = yes ] || { route_up && net=yes; }
  [ "$bt" = yes ] && [ "$net" = yes ] && break
  [ "$waited" -ge "$MAX" ] && break
  sleep "$STEP"; waited=$((waited + 1))
done
echo "pilot-wait: bluez=$bt default-route=$net after ${waited}s$([ "$bt$net" = yesyes ] || echo ' (deadline: starting anyway)')"
exit 0
