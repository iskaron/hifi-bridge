#!/usr/bin/env bash
# Start a headless PipeWire stack inside a disposable container (no audio hardware, no host access):
# private D-Bus session bus -> pipewire -> wireplumber -> pipewire-pulse.
# SOURCE this file (". start_pipewire.sh"): it exports XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS into the caller.
# (Running it inside $(...) would hang: the daemons keep the substitution pipe open.)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/pw}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
LOG=${PW_LOG:-/tmp/pw-logs}; mkdir -p "$LOG"
# No systemd-logind in a container (nor, possibly, in a Buildroot appliance): WirePlumber 0.4.x quits when its bluez
# monitor's logind plugin cannot start ("failed to start systemd logind monitor" -> "disconnected from pipewire").
# Disable seat monitoring for both config formats (0.4 Lua, 0.5 SPA-JSON). Finding documented in docs/PIPEWIRE_CAPTURE.md.
export XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-/tmp/pw-config}
# Graph clock: allow 44.1 kHz so a 44.1 kHz source is not resampled to 48 kHz and back (bit-exactness, CPU).
# PW_ALLOWED_RATES="" keeps PipeWire's default (48 kHz only) - used to document the resampling effect.
mkdir -p "$XDG_CONFIG_HOME/pipewire/pipewire.conf.d"
if [ "${PW_ALLOWED_RATES-44100 48000}" != "" ]; then
  printf 'context.properties = {\n  default.clock.allowed-rates = [ %s ]\n}\n' "${PW_ALLOWED_RATES-44100 48000}" > "$XDG_CONFIG_HOME/pipewire/pipewire.conf.d/10-rates.conf"
else
  rm -f "$XDG_CONFIG_HOME/pipewire/pipewire.conf.d/10-rates.conf"
fi
mkdir -p "$XDG_CONFIG_HOME/wireplumber/bluetooth.lua.d" "$XDG_CONFIG_HOME/wireplumber/wireplumber.conf.d"
echo 'bluez_monitor.properties["with-logind"] = false' > "$XDG_CONFIG_HOME/wireplumber/bluetooth.lua.d/80-no-logind.lua"
printf 'wireplumber.profiles = {\n  main = {\n    monitor.bluez.seat-monitoring = disabled\n  }\n}\n' > "$XDG_CONFIG_HOME/wireplumber/wireplumber.conf.d/80-no-logind.conf"
dbus-daemon --session --fork --nopidfile --address="unix:path=$XDG_RUNTIME_DIR/dbus.sock" </dev/null >/dev/null 2>&1
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/dbus.sock"
pipewire        </dev/null >"$LOG/pipewire.log"     2>&1 &
for _ in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break; sleep 0.1; done
wireplumber     </dev/null >"$LOG/wireplumber.log"  2>&1 &
pipewire-pulse  </dev/null >"$LOG/pipewire-pulse.log" 2>&1 &
for _ in $(seq 100); do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
pactl info >/dev/null || { echo "pipewire-pulse did not come up" >&2; tail -5 "$LOG"/*.log >&2; return 1 2>/dev/null || exit 1; }
export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XDG_CONFIG_HOME
