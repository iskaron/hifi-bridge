#!/bin/sh
# Private-pilot service manager for the Hi-Fi Bridge on a STOCK Raspberry Pi OS Lite (NOT the appliance image). docs/PILOT.md.
# Run as the NORMAL user that already runs the manual hwkit launch (never with sudo; the one root step, `loginctl enable-linger`, asks
# for sudo itself). Idempotent. Never touches ~/bridge-data content, BlueZ bonds, PipeWire/WirePlumber config, /boot, Lyrion or HA.
#
#   pilot_service.sh check                 read-only prerequisites and current state (exit 1 on a FAIL)
#   pilot_service.sh install [--start]     install + enable the user unit (and linger); start only with --start
#   pilot_service.sh start | stop | restart | status | logs [-f]
#   pilot_service.sh disable               stop + disable (back to the manual launch; unit and settings stay)
#   pilot_service.sh uninstall             disable + remove the unit (settings file, data directory, backups stay)
# options: --app-dir DIR (default: the unpacked bundle this script is in)  --data-dir DIR (default: ~/bridge-data)
# HIFI_PILOT_ROOT=<dir> (tests only): DRY RUN - every path below <dir>, no systemctl/loginctl/sudo is run, only printed.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${HIFI_PILOT_ROOT:-}
UNIT=hifi-bridge-pilot.service
CMD=${1:-}; [ $# -eq 0 ] || shift
APP_DIR=$(cd "$HERE/../.." && pwd); DATA_DIR=${ROOT}${HOME}/bridge-data
START=no FOLLOW=no
while [ $# -gt 0 ]; do
  case "$1" in
    --app-dir) [ $# -ge 2 ] || { echo "--app-dir needs a value" >&2; exit 2; }; APP_DIR=$2; shift ;;
    --data-dir) [ $# -ge 2 ] || { echo "--data-dir needs a value" >&2; exit 2; }; DATA_DIR=$2; shift ;;
    --start) START=yes ;;
    -f) FOLLOW=yes ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -z "$ROOT" ] || [ -z "$DATA_DIR" ] || case "$DATA_DIR" in "$ROOT"/*) ;; *) DATA_DIR=${ROOT}${DATA_DIR} ;; esac

USER_HOME=${ROOT}${HOME}
UNIT_DIR=$USER_HOME/.config/systemd/user
CONF_DIR=$USER_HOME/.config/hifi-bridge
ENV_FILE=$CONF_DIR/pilot.env
BACKUP_DIR=$CONF_DIR/backup
LINGER_MARK=$CONF_DIR/linger-enabled-by-pilot
ME=$(id -un)
RC=0

say() { printf '%s\n' "$*"; }
ok() { printf 'OK    %s\n' "$*"; }
info() { printf 'INFO  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; RC=1; }
die() { printf 'refusing: %s\n' "$*" >&2; exit "${2:-2}"; }
# run a service-manager command, or only print it in the dry run
sc() { if [ -n "$ROOT" ]; then say "would run: $*"; else "$@"; fi; }
user_sc() { sc systemctl --user "$@"; }

safe_path() { case "$1" in ''|*[!A-Za-z0-9._/@+-]*) return 1 ;; /*) return 0 ;; *) return 1 ;; esac; }

preconditions() {
  [ -n "$ROOT" ] || [ "$(id -u)" -ne 0 ] || die "run as the normal user, not root/sudo: the unit belongs to the user whose PipeWire session records the phone" 4
  MODEL=$(tr -d '\0' < "$ROOT/proc/device-tree/model" 2>/dev/null || true)
  case "$MODEL" in
    *"Raspberry Pi"*) ;;
    *) die "this is not a Raspberry Pi (model: ${MODEL:-unknown}); the pilot is for the test Pi only" 3 ;;
  esac
  safe_path "$APP_DIR" || die "--app-dir must be an absolute path of letters, digits and . _ / @ + - only"
  safe_path "$DATA_DIR" || die "--data-dir must be an absolute path of letters, digits and . _ / @ + - only"
}

app_ok() { [ -f "$APP_DIR/bridge/btbridge/appliance.py" ] && [ -x "$APP_DIR/hwkit/pi-setup/hifi-bridge-pilot-wait.sh" ]; }
revision() { cat "$APP_DIR/dist/HWKIT_REVISION" 2>/dev/null || echo unknown; }

render_unit() {
  sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@DATA_DIR@|$DATA_DIR|g" "$HERE/hifi-bridge-pilot.service"
}

manual_running() {   # a hand-started bridge would own the ports 8080/8443/8090
  [ -z "$ROOT" ] || return 1
  command -v pgrep >/dev/null 2>&1 || return 1
  pgrep -f 'btbridge\.appliance' >/dev/null 2>&1
}

unit_active() { [ -z "$ROOT" ] && systemctl --user is-active --quiet "$UNIT" 2>/dev/null; }

linger_on() { [ "$(loginctl show-user "$ME" -p Linger --value 2>/dev/null || echo no)" = yes ]; }

cmd_check() {
  preconditions
  ok "board: $MODEL; user: $ME; bundle revision: $(revision)"
  if app_ok; then ok "bridge code: $APP_DIR/bridge/btbridge"; else fail "no bridge code in $APP_DIR (unpack the bundle there, or pass --app-dir)"; fi
  if [ -d "$DATA_DIR" ]; then
    mode=$(stat -c %a "$DATA_DIR" 2>/dev/null || echo ?)
    case "$mode" in 700) ok "data directory $DATA_DIR (mode 700)" ;; *) warn "data directory $DATA_DIR has mode $mode; 'install' tightens it to 700 (it holds the owner's secrets)" ;; esac
    loose=$(find "$DATA_DIR" -type f \( -perm -g=r -o -perm -o=r \) 2>/dev/null | wc -l)
    [ "$loose" -eq 0 ] || warn "$loose file(s) in the data directory are group/other readable (the 700 directory still keeps other users out)"
  else
    info "data directory $DATA_DIR does not exist yet (the bridge creates it; a NEW directory means a new owner setup)"
  fi
  if [ -n "$ROOT" ]; then info "dry run: no live system queries"; return 0; fi
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then ok "python3 >= 3.9"; else fail "python3 >= 3.9 missing"; fi
  if python3 -c 'import jeepney' 2>/dev/null; then ok "python3-jeepney"; else fail "python3-jeepney missing (sudo apt install python3-jeepney)"; fi
  for t in busctl ip systemctl loginctl; do if command -v "$t" >/dev/null 2>&1; then ok "$t"; else fail "$t missing"; fi; done
  if linger_on; then ok "linger is on: the user manager (and the bridge) start at boot and survive SSH logout"; else warn "linger is off: 'install' enables it (needs sudo once)"; fi
  for u in pipewire wireplumber pipewire-pulse; do
    state=$(systemctl --user is-active "$u.service" 2>/dev/null || true); info "$u.service: ${state:-unknown} (the pilot unit pulls them in at start)"
  done
  if [ -f /etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf ]; then ok "WirePlumber A2DP input role (50-hifi-bridge-a2dp-input.conf)"
  else warn "50-hifi-bridge-a2dp-input.conf missing: run setup_test_pi.sh (it is part of the working hwkit deployment)"; fi
  if [ -f /etc/wireplumber/wireplumber.conf.d/80-no-logind.conf ]; then ok "WirePlumber headless seat override (80-no-logind.conf)"
  else warn "80-no-logind.conf missing: needed on a headless Pi (setup_test_pi.sh --headless)"; fi
  if [ -f "$UNIT_DIR/$UNIT" ]; then
    if render_unit | cmp -s - "$UNIT_DIR/$UNIT"; then ok "unit installed and current"; else warn "unit installed but differs from this bundle ('install' replaces it and keeps a backup)"; fi
    info "enabled: $(systemctl --user is-enabled "$UNIT" 2>/dev/null || true); active: $(systemctl --user is-active "$UNIT" 2>/dev/null || true)"
  else
    info "unit not installed"
  fi
  if ! unit_active && manual_running; then warn "a manually started bridge is running: stop it (Ctrl-C in its terminal) before 'start'"; fi
  return 0
}

backup() {   # $1 = existing file to keep before it is replaced
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  cp -p "$1" "$BACKUP_DIR/$(basename "$1").$(date +%Y%m%d%H%M%S)"
  info "previous $(basename "$1") kept in $BACKUP_DIR"
}

cmd_install() {
  preconditions
  app_ok || die "no bridge code in $APP_DIR (unpack the bundle there, or pass --app-dir)"
  mkdir -p "$UNIT_DIR" "$CONF_DIR"; chmod 700 "$CONF_DIR"
  tmp=$(mktemp "$CONF_DIR/unit.XXXXXX"); trap 'rm -f "$tmp"' EXIT
  render_unit > "$tmp"
  if [ -f "$UNIT_DIR/$UNIT" ] && cmp -s "$tmp" "$UNIT_DIR/$UNIT"; then
    ok "unit unchanged: $UNIT_DIR/$UNIT"
  else
    [ ! -f "$UNIT_DIR/$UNIT" ] || backup "$UNIT_DIR/$UNIT"
    install -m 0644 "$tmp" "$UNIT_DIR/$UNIT"; ok "unit written: $UNIT_DIR/$UNIT"
  fi
  if [ -f "$ENV_FILE" ]; then
    ok "settings file kept: $ENV_FILE"
  else
    umask 077
    cat > "$ENV_FILE" <<'EOF'
# Optional environment for the Hi-Fi Bridge pilot service (read at start; `pilot_service.sh restart` applies a change). One VAR=value per line.
# Opt-in audio-path trace (docs/STREAM_TRACE.md): one summary line per second in the journal. Leave it OFF for daily use.
#HIFI_STREAM_TRACE=1
EOF
    chmod 600 "$ENV_FILE"; ok "settings file created (all commented out): $ENV_FILE"
  fi
  if [ -d "$DATA_DIR" ]; then
    mode=$(stat -c %a "$DATA_DIR" 2>/dev/null || echo 700)
    case "$mode" in 700) ;; *) chmod go-rwx "$DATA_DIR"; ok "data directory $DATA_DIR tightened from $mode to 700 (content untouched)" ;; esac
  else
    info "data directory $DATA_DIR does not exist: the bridge will create it, with a NEW owner setup. Pass --data-dir if the existing one is elsewhere."
  fi
  if [ -n "$ROOT" ]; then
    say "would run: loginctl enable-linger $ME (if not already on)"
  elif linger_on; then
    ok "linger already on"
  else
    say "enabling linger so the bridge starts at boot and survives SSH logout (needs sudo once): loginctl enable-linger $ME"
    if loginctl enable-linger "$ME" 2>/dev/null || sudo loginctl enable-linger "$ME"; then : > "$LINGER_MARK"; ok "linger enabled"; else fail "could not enable linger: run  sudo loginctl enable-linger $ME"; fi
  fi
  user_sc daemon-reload
  user_sc enable "$UNIT"
  if [ "$START" = yes ]; then cmd_start; else
    say ""; say "Installed and enabled, NOT started. Stop the manual launch (Ctrl-C), then:  sh $0 start"
    say "Back to the manual launch at any time:  sh $0 disable"
  fi
}

cmd_start() {
  preconditions
  if ! unit_active && manual_running; then die "a manually started bridge is running: stop it (Ctrl-C in its terminal) first - it owns the ports and the Bluetooth agent"; fi
  user_sc start "$UNIT"
  [ -n "$ROOT" ] || cmd_status
}

cmd_stop() { preconditions; user_sc stop "$UNIT"; }
cmd_restart() { preconditions; if ! unit_active && manual_running; then die "a manually started bridge is running: stop it first"; fi; user_sc restart "$UNIT"; }

cmd_status() {
  preconditions
  say "bundle revision: $(revision)"
  [ -z "$ROOT" ] || { say "dry run: no live system queries"; return 0; }
  systemctl --user show "$UNIT" -p LoadState -p UnitFileState -p ActiveState -p SubState -p Result -p NRestarts -p ExecMainStartTimestamp -p MainPID 2>/dev/null || true
  if linger_on; then say "linger: yes"; else say "linger: no"; fi
  if command -v curl >/dev/null 2>&1; then
    say "healthz: $(curl -fsS -m 3 http://127.0.0.1:8080/healthz 2>/dev/null || echo 'no answer on 127.0.0.1:8080')"
  fi
  say "--- last 15 log lines"
  journalctl --user -u "$UNIT" -n 15 --no-pager 2>/dev/null || journalctl "_SYSTEMD_USER_UNIT=$UNIT" -n 15 --no-pager 2>/dev/null || say "(no journal access: add the user to the adm/systemd-journal group)"
}

cmd_logs() {
  preconditions
  [ -z "$ROOT" ] || { say "dry run"; return 0; }
  if [ "$FOLLOW" = yes ]; then journalctl --user -u "$UNIT" -f --no-pager; else journalctl --user -u "$UNIT" -n 200 --no-pager; fi
}

cmd_disable() {
  preconditions
  user_sc disable --now "$UNIT"
  say "Pilot service disabled and stopped. Manual launch as before:"
  say "  cd $APP_DIR && export PYTHONPATH=\"\$PWD/bridge:\$PWD/hwkit\" PYTHONUNBUFFERED=1"
  say "  python3 -u -m btbridge.appliance --data \"$DATA_DIR\" --source bluez        # add HIFI_STREAM_TRACE=1 in front for a trace run"
  say "  (an earlier launch also passed --boot-dir /boot/firmware: optional; on stock Pi OS it only logs a non-fatal permission error)"
}

cmd_uninstall() {
  cmd_disable
  if [ -f "$UNIT_DIR/$UNIT" ]; then rm -f "$UNIT_DIR/$UNIT"; ok "unit removed"; fi
  user_sc daemon-reload
  say "Kept: $ENV_FILE, $BACKUP_DIR, the data directory $DATA_DIR, BlueZ bonds, PipeWire config."
  if [ -f "$LINGER_MARK" ]; then say "Linger was enabled by this installer and is still on. Only if nothing else of yours needs it:  sudo loginctl disable-linger $ME"; fi
}

case "$CMD" in
  check) cmd_check ;;
  install) cmd_install ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  disable) cmd_disable ;;
  uninstall) cmd_uninstall ;;
  *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
exit $RC
