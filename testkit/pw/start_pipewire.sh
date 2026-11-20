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
for _ in $(seq 150); do [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break; sleep 0.1; done
wireplumber     </dev/null >"$LOG/wireplumber.log"  2>&1 &
# pipewire-pulse creates $XDG_RUNTIME_DIR/pulse with stat() + mkdir() and treats EEXIST as fatal ("mkdir() /run/pw/pulse failed: File exists",
# "failed to create context"; PipeWire 1.4.2 and 0.3.65). The readiness poll below runs `pactl`, and libpulse creates the very same directory
# when it connects: started at the wrong microsecond, the poll made pipewire-pulse fail (reproduced in 3-6 % of start-ups in a tight loop;
# OPEN_QUESTIONS #26, docs/PIPEWIRE_CAPTURE.md). The harness therefore creates the directory first, as a service manager's RuntimeDirectory
# or tmpfiles entry would on a device.
mkdir -p "$XDG_RUNTIME_DIR/pulse" && chmod 700 "$XDG_RUNTIME_DIR/pulse"
# A crashed pipewire-pulse leaves no trace in its log (PipeWire 1.4.2 can die with SIGSEGV while a module is unloaded under connected
# streams), so each run is wrapped: its exit status, and the signal, is appended to the log. The wrapper bash lives as long as the
# daemon, so $PW_PULSE_PID can be polled with kill -0.
pw_start_pulse() {
  # A fresh bash (not a forked subshell): a subshell would inherit the caller's saved stdout (the `. script >/dev/null` redirect keeps the
  # original in fd 10) and hold the capture pipe of `$(...)`/subprocess callers open for as long as the daemon lives (found by running the suite).
  bash -c 'pipewire-pulse </dev/null >>"$1" 2>&1
    rc=$?; sig=""; [ "$rc" -gt 128 ] && sig=" (killed by SIG$(kill -l $((rc - 128))))"
    echo "### harness: pipewire-pulse exited with status $rc$sig at $(date +%T)" >>"$1"' _ "$LOG/pipewire-pulse.log" </dev/null >/dev/null 2>&1 &
  PW_PULSE_PID=$!
}
: >"$LOG/pipewire-pulse.log"
pw_start_pulse
# Up to 30 s: cold CI runners are slow. Should pipewire-pulse still exit during start-up, restart it ONCE and record that in its log,
# as the device's service manager would.
PW_PULSE_RESTARTS=0
for _ in $(seq 300); do
  pactl info >/dev/null 2>&1 && break
  if [ "$PW_PULSE_RESTARTS" -eq 0 ] && ! kill -0 "$PW_PULSE_PID" 2>/dev/null; then
    echo "### harness: pipewire-pulse exited during start-up - restarting it once" >>"$LOG/pipewire-pulse.log"
    pw_start_pulse
    PW_PULSE_RESTARTS=1
  fi
  sleep 0.1
done
pactl info >/dev/null || { echo "pipewire-pulse did not come up (restarts: $PW_PULSE_RESTARTS)" >&2
  for f in pipewire-pulse wireplumber pipewire; do echo "--- $f.log" >&2; tail -n 12 "$LOG/$f.log" >&2; done; return 1 2>/dev/null || exit 1; }
export PW_PULSE_RESTARTS
export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XDG_CONFIG_HOME
