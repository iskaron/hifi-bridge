#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers of ALL entry scripts (make demo, lab, tests, hardware kit). Source it, do not run it:
#     . "$(dirname "$0")/lib/common.sh"
# It provides
#   * output with a step counter and elapsed time, a quiet mode for CI (HB_QUIET=1 or --quiet handled by the caller),
#   * hb_run: runs a long command, shows filtered progress and a heartbeat while it is silent, keeps the full log, prints its tail on failure,
#   * hb_preflight: docker, compose, daemon, versions, disk, ports, extra tools - ALL problems at once, in English with a German hint,
#   * the image registry: hb_ensure_image builds missing project images (in dependency order), hb_ensure_pulled pulls third-party images.
# Nothing here installs anything on the host and nothing touches containers, networks or volumes: it only inspects, builds and pulls images.

[ -n "${HB_COMMON_LOADED:-}" ] && return 0
HB_COMMON_LOADED=1

HB_ROOT=${HB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}

# Minimum versions (docs/DEMO.md "Requirements"; evidence in docs/DOCKER_REQUIREMENTS.md). Tested range of the project: see there.
HB_MIN_ENGINE=${HB_MIN_ENGINE:-24.0}
HB_MIN_COMPOSE=${HB_MIN_COMPOSE:-2.20}

HB_QUIET=${HB_QUIET:-0}                  # 1: only step lines, warnings and errors (CI); full logs stay in $HB_LOGDIR
HB_HEARTBEAT=${HB_HEARTBEAT:-15}         # seconds of silence after which hb_run says that it is still working
HB_START=$(date +%s)
HB_STEP_N=0
HB_STEP_TOTAL=0
HB_LOGDIR=${HB_LOGDIR:-"${TMPDIR:-/tmp}/hifi-bridge-logs-$(id -u)"}

# ---------------------------------------------------------------------------------------------------------------- output
hb_clock() {                                                         # "m:ss" since the script started
  local s=$(( $(date +%s) - HB_START ))
  printf '%d:%02d' $((s / 60)) $((s % 60))
}

hb_duration() {                                                      # seconds -> "45 s" / "3 min 05 s"
  local s=$1
  if [ "$s" -ge 60 ]; then printf '%d min %02d s' $((s / 60)) $((s % 60)); else printf '%d s' "$s"; fi
}

hb_steps() { HB_STEP_TOTAL=$1; HB_STEP_N=0; }
hb_step() {                                                          # always printed, also in quiet mode
  HB_STEP_N=$((HB_STEP_N + 1))
  printf '[%d/%d %s] %s\n' "$HB_STEP_N" "$HB_STEP_TOTAL" "$(hb_clock)" "$*"
}
hb_info() { [ "$HB_QUIET" = 1 ] || printf '      %s\n' "$*"; }
hb_warn() { printf 'WARNING: %s\n' "$*" >&2; }
hb_err() { printf 'ERROR: %s\n' "$*" >&2; }

hb_parse_quiet() {                                                   # usage: hb_parse_quiet "$@" ; sets HB_QUIET, leaves HB_ARGS
  HB_ARGS=()
  local a
  for a in "$@"; do
    case "$a" in
      -q|--quiet) HB_QUIET=1 ;;
      *) HB_ARGS+=("$a") ;;
    esac
  done
}

# -------------------------------------------------------------------------------------------------- long-running commands
hb_logfile() {                                                       # a fresh log file for a label
  mkdir -p "$HB_LOGDIR" && chmod 700 "$HB_LOGDIR" 2>/dev/null
  local n; n=$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-40)
  mktemp "$HB_LOGDIR/$(date +%H%M%S)-$n.XXXXXX.log"
}

# Reads a command's merged output on stdin: writes ALL of it to $HB_PUMP_LOG, prints the lines matching $HB_FILTER (ERE; default: all)
# unless quiet, and says "still working" after $HB_HEARTBEAT seconds without output.
hb_pump() {
  local line carry="" last now t0 rc seen="" filter
  t0=$(date +%s); last=$t0
  while :; do
    if IFS= read -r -t 1 line; then
      line=$carry$line; carry=""
      line=${line%$'\r'}
      printf '%s\n' "$line" >>"$HB_PUMP_LOG"
      seen=$line
      filter=${HB_FILTER:-.}
      if [ "$HB_QUIET" != 1 ] && [[ $line =~ $filter ]]; then
        printf '      %s\n' "${line:0:150}"
        last=$(date +%s)
      fi
    else
      rc=$?
      if [ "$rc" -gt 128 ]; then                                     # read timed out: keep any partial line
        carry=$carry$line
        now=$(date +%s)
        if [ "$HB_QUIET" != 1 ] && [ $((now - last)) -ge "$HB_HEARTBEAT" ]; then
          printf '      ... still working, %s elapsed%s\n' "$(hb_duration $((now - t0)))" "${seen:+ (last output: ${seen:0:70})}"
          last=$now
        fi
        continue
      fi
      [ -n "$carry$line" ] && printf '%s\n' "$carry$line" >>"$HB_PUMP_LOG"
      break
    fi
  done
}

# hb_run "what" command args...   (HB_FILTER selects which output lines are shown). Returns the command's exit status.
hb_run() {
  local label=$1 rc t0; shift
  HB_PUMP_LOG=$(hb_logfile "$label"); export HB_PUMP_LOG
  t0=$(date +%s)
  { "$@" 2>&1 | hb_pump; rc=${PIPESTATUS[0]}; } || true            # the group is exempt from errexit, so rc is always read
  if [ "$rc" -ne 0 ]; then
    hb_err "$label failed (exit $rc) after $(hb_duration $(( $(date +%s) - t0 ))). Last lines of its output:"
    tail -n 30 "$HB_PUMP_LOG" | sed 's/^/      | /' >&2
    printf '      Full log: %s\n' "$HB_PUMP_LOG" >&2
  else
    hb_info "$label: done in $(hb_duration $(( $(date +%s) - t0 )))"
  fi
  return "$rc"
}

# ------------------------------------------------------------------------------------------------------------ pre-flight
HB_PROBLEMS=0
# hb_problem "English text" "deutscher Hinweis" ["upgrade hint (English)" ["Aktualisieren (Deutsch)"]]
hb_problem() {
  HB_PROBLEMS=$((HB_PROBLEMS + 1))
  {
    printf 'PRE-FLIGHT FAILED: %s\n' "$1"
    [ -n "${3:-}" ] && printf '      How to fix:  %s\n' "$3"
    printf '      Deutsch:     %s\n' "$2"
    [ -n "${4:-}" ] && printf '      Beheben:     %s\n' "$4"
  } >&2
}

# 24.0.9 / v2.24.5 / 2.40.3+ds1-0ubuntu1 / 29.1.3-rd -> 24.0.9 / 2.24.5 / 2.40.3 / 29.1.3
hb_version_clean() { printf '%s' "$1" | sed -E 's/^[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/'; }

hb_version_ge() {                                                    # $1 >= $2 (dotted numbers, missing parts count as 0)
  local IFS=. i x y
  local -a a b
  read -ra a <<<"$1"; read -ra b <<<"$2"
  for i in 0 1 2 3; do
    x=${a[i]:-0}; y=${b[i]:-0}
    x=${x//[!0-9]/}; y=${y//[!0-9]/}
    [ $((10#${x:-0})) -gt $((10#${y:-0})) ] && return 0
    [ $((10#${x:-0})) -lt $((10#${y:-0})) ] && return 1
  done
  return 0
}

HB_ENGINE_VERSION=""
HB_COMPOSE_VERSION=""

hb_check_docker() {                                                  # $1 = "compose" when Compose is needed
  local out
  if ! command -v docker >/dev/null 2>&1; then
    hb_problem "Docker is not installed (no 'docker' command found)." \
               "Docker ist nicht installiert (kein Befehl 'docker' gefunden)." \
               "install Docker Engine with the Compose plugin: https://docs.docker.com/engine/install/" \
               "Docker Engine samt Compose-Plugin installieren: https://docs.docker.com/engine/install/"
    return 1
  fi
  if ! out=$(docker version --format '{{.Server.Version}}' 2>&1); then
    if printf '%s' "$out" | grep -qi 'permission denied'; then
      hb_problem "Docker is installed but this user may not use it (permission denied on the Docker socket)." \
                 "Docker ist installiert, aber dieser Benutzer darf es nicht verwenden (Zugriff auf den Docker-Socket verweigert)." \
                 "add your user to the 'docker' group (sudo usermod -aG docker \$USER), then log out and in again" \
                 "Benutzer zur Gruppe 'docker' hinzufuegen (sudo usermod -aG docker \$USER), danach neu anmelden"
    else
      hb_problem "The Docker daemon is not reachable (is Docker running?)." \
                 "Der Docker-Daemon ist nicht erreichbar (laeuft Docker?)." \
                 "start it (Linux: sudo systemctl start docker; Docker Desktop: start the application) and try again" \
                 "starten (Linux: sudo systemctl start docker; Docker Desktop: Anwendung starten) und erneut versuchen"
    fi
    return 1
  fi
  HB_ENGINE_VERSION=$(hb_version_clean "$out")
  if [ -z "$HB_ENGINE_VERSION" ]; then
    hb_warn "could not read the Docker Engine version from: $out (continuing)"
  elif ! hb_version_ge "$HB_ENGINE_VERSION" "$HB_MIN_ENGINE"; then
    hb_problem "Docker Engine is too old: found $HB_ENGINE_VERSION, required $HB_MIN_ENGINE or newer." \
               "Docker Engine ist zu alt: gefunden $HB_ENGINE_VERSION, benoetigt wird mindestens $HB_MIN_ENGINE." \
               "upgrade Docker Engine: https://docs.docker.com/engine/install/ (Debian/Ubuntu: use Docker's apt repository instead of the distribution's old docker.io package). Check with: docker version" \
               "Docker Engine aktualisieren: https://docs.docker.com/engine/install/ (Debian/Ubuntu: Dockers apt-Repository statt des alten Distributionspakets docker.io verwenden). Pruefen mit: docker version"
  fi
  [ "${1:-}" = compose ] || return 0
  if ! out=$(docker compose version 2>&1); then
    if command -v docker-compose >/dev/null 2>&1; then
      hb_problem "Only the old standalone 'docker-compose' (v1) was found; Docker Compose v2 ('docker compose') is required." \
                 "Es wurde nur das alte eigenstaendige 'docker-compose' (v1) gefunden; benoetigt wird Docker Compose v2 ('docker compose')." \
                 "install the plugin package 'docker-compose-plugin' (Docker's apt repository) or 'docker-compose-v2' (Ubuntu), see https://docs.docker.com/compose/install/" \
                 "Plugin-Paket 'docker-compose-plugin' (Dockers apt-Repository) bzw. 'docker-compose-v2' (Ubuntu) installieren, siehe https://docs.docker.com/compose/install/"
    else
      hb_problem "Docker Compose v2 is not installed ('docker compose' failed)." \
                 "Docker Compose v2 ist nicht installiert ('docker compose' schlug fehl)." \
                 "install the plugin package 'docker-compose-plugin': https://docs.docker.com/compose/install/linux/" \
                 "Plugin-Paket 'docker-compose-plugin' installieren: https://docs.docker.com/compose/install/linux/"
    fi
    return 1
  fi
  HB_COMPOSE_VERSION=$(hb_version_clean "$(printf '%s' "$out" | head -n 1)")
  if [ -z "$HB_COMPOSE_VERSION" ]; then
    hb_warn "could not read the Docker Compose version from: $out (continuing)"
  elif ! hb_version_ge "$HB_COMPOSE_VERSION" "$HB_MIN_COMPOSE"; then
    hb_problem "Docker Compose is too old: found $HB_COMPOSE_VERSION, required $HB_MIN_COMPOSE or newer." \
               "Docker Compose ist zu alt: gefunden $HB_COMPOSE_VERSION, benoetigt wird mindestens $HB_MIN_COMPOSE." \
               "upgrade the Compose plugin: install 'docker-compose-plugin' from Docker's apt repository (https://docs.docker.com/engine/install/) or update Docker Desktop. Check with: docker compose version" \
               "Compose-Plugin aktualisieren: 'docker-compose-plugin' aus Dockers apt-Repository installieren (https://docs.docker.com/engine/install/) bzw. Docker Desktop aktualisieren. Pruefen mit: docker compose version"
  fi
}

hb_free_mb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print int($4 / 1024) }'; }

# hb_check_disk MB LABEL: free space in the project directory and, when readable, in Docker's data directory
hb_check_disk() {
  local need=$1 label=$2 free root
  free=$(hb_free_mb "$HB_ROOT")
  if [ -n "$free" ] && [ "$free" -lt "$need" ]; then
    hb_problem "Not enough free disk space in the project directory: $free MB free, about $need MB needed for $label." \
               "Zu wenig freier Speicher im Projektverzeichnis: $free MB frei, fuer $label werden etwa $need MB benoetigt."
  fi
  root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
  if [ -n "$root" ] && [ -d "$root" ] && [ -r "$root" ]; then
    free=$(hb_free_mb "$root")
    if [ -n "$free" ] && [ "$free" -lt "$need" ]; then
      hb_problem "Not enough free disk space for Docker images ($root): $free MB free, about $need MB needed for $label." \
                 "Zu wenig freier Speicher fuer Docker-Images ($root): $free MB frei, fuer $label werden etwa $need MB benoetigt." \
                 "free space, e.g. 'docker image prune' (only unused images; this project never prunes by itself)" \
                 "Platz schaffen, z. B. 'docker image prune' (nur unbenutzte Images; dieses Projekt raeumt nie selbst auf)"
    fi
  fi
}

hb_port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# hb_check_ports "p1 p2 ..." [ENV_HINT]
hb_check_ports() {
  local busy="" p
  for p in $1; do hb_port_busy "$p" && busy="$busy $p"; done
  [ -z "$busy" ] && return 0
  hb_problem "Port(s) already in use on this computer:$busy" \
             "Port(s) auf diesem Rechner bereits belegt:$busy" \
             "${2:-choose other ports (see the documentation of this command)}" \
             "andere Ports waehlen (siehe Dokumentation dieses Befehls)"
  return 1
}

# hb_preflight [--compose] [--disk MB LABEL] [--ports "p1 p2" HINT] [--tools "git make"]
# Checks everything and reports ALL problems; returns 2 (and prints how to fix them) when anything is missing.
hb_preflight() {
  local compose="" disk="" disk_label="" ports="" ports_hint="" tools="" t
  while [ $# -gt 0 ]; do
    case "$1" in
      --compose) compose=compose ;;
      --disk) disk=$2; disk_label=$3; shift 2 ;;
      --ports) ports=$2; ports_hint=$3; shift 2 ;;
      --tools) tools=$2; shift ;;
    esac
    shift
  done
  HB_PROBLEMS=0
  hb_check_docker "$compose" && hb_info "docker engine $HB_ENGINE_VERSION${HB_COMPOSE_VERSION:+, compose $HB_COMPOSE_VERSION} (required: engine >= $HB_MIN_ENGINE${compose:+, compose >= $HB_MIN_COMPOSE})"
  for t in $tools; do
    command -v "$t" >/dev/null 2>&1 || hb_problem "Required tool '$t' was not found on this computer." "Benoetigtes Programm '$t' wurde auf diesem Rechner nicht gefunden." \
      "install it with your package manager (for example: sudo apt install $t)" "mit der Paketverwaltung installieren (zum Beispiel: sudo apt install $t)"
  done
  [ -n "$disk" ] && hb_check_disk "$disk" "$disk_label"
  [ -n "$ports" ] && hb_check_ports "$ports" "$ports_hint"
  [ "$HB_PROBLEMS" -eq 0 ] && return 0
  hb_err "$HB_PROBLEMS prerequisite(s) missing - nothing was started or changed."
  return 2
}

hb_require() { hb_preflight "$@" || exit $?; }

# ----------------------------------------------------------------------------------------------------- image registry
# hb_image_info NAME sets HB_I_FILE (Dockerfile), HB_I_CTX (build context), HB_I_DEPS (project images it is built FROM), HB_I_ARGS
# (build args), HB_I_WHY and HB_I_MB (what it is for / approximate size). Paths are relative to the repository root.
hb_image_info() {
  HB_I_FILE=""; HB_I_CTX=""; HB_I_DEPS=""; HB_I_ARGS=""; HB_I_WHY=""; HB_I_MB=""
  case "$1" in
    btbridge-lab:dev)       HB_I_FILE=docker/Dockerfile.lab;       HB_I_CTX=docker; HB_I_MB=390;  HB_I_WHY="lab runner: bridge runtime, test tools, software Squeezelite players" ;;
    btbridge-hwkit:dev)     HB_I_FILE=docker/Dockerfile.hwkit;     HB_I_CTX=docker; HB_I_MB=630;  HB_I_DEPS=btbridge-lab:dev; HB_I_WHY="hardware kit: lab image plus FFmpeg (SBC/AAC)" ;;
    btbridge-pw:bookworm)   HB_I_FILE=docker/Dockerfile.pipewire;  HB_I_CTX=docker; HB_I_MB=210;  HB_I_ARGS="BASE=debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc"; HB_I_WHY="PipeWire 0.3.65 capture tests" ;;
    btbridge-pw:trixie)     HB_I_FILE=docker/Dockerfile.pipewire;  HB_I_CTX=docker; HB_I_MB=240;  HB_I_ARGS="BASE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2";   HB_I_WHY="PipeWire 1.4 capture tests" ;;
    btbridge-avahi:dev)     HB_I_FILE=docker/Dockerfile.avahi;     HB_I_CTX=docker; HB_I_MB=125;  HB_I_WHY="real Avahi for the mDNS interoperability tests" ;;
    btbridge-browser:dev)   HB_I_FILE=docker/Dockerfile.browser;   HB_I_CTX=docker; HB_I_MB=2600; HB_I_WHY="Chromium/WebKit (Playwright) for the browser tests" ;;
    btbridge-static:dev)    HB_I_FILE=docker/Dockerfile.static;    HB_I_CTX=docker; HB_I_MB=165;  HB_I_WHY="systemd-analyze and static checks" ;;
    btbridge-syslog:dev)    HB_I_FILE=docker/Dockerfile.syslog;    HB_I_CTX=docker; HB_I_MB=90;   HB_I_WHY="real rsyslog (TLS) for the remote-syslog test" ;;
    btbridge-py314:dev)     HB_I_FILE=docker/Dockerfile.py314;     HB_I_CTX=docker; HB_I_MB=215;  HB_I_WHY="Python 3.14 compatibility run" ;;
    btbridge-security:dev)  HB_I_FILE=docker/Dockerfile.security;  HB_I_CTX=docker; HB_I_MB=155;  HB_I_WHY="bandit security analysis" ;;
    btbridge-os:dev)        HB_I_FILE=docker/Dockerfile.os;        HB_I_CTX=docker; HB_I_MB=520;  HB_I_WHY="Buildroot prerequisites, genimage and flash-tool tests" ;;
    btbridge-entry:dev)     HB_I_FILE=docker/Dockerfile.entry;     HB_I_CTX=docker; HB_I_MB=130;  HB_I_WHY="tests of the entry scripts and compose files" ;;
    hifi-demo:dev)          HB_I_FILE=demo/Dockerfile;             HB_I_CTX=.;      HB_I_MB=400;  HB_I_WHY="the interactive demo: bridge node, phones, rooms, gateway (one image)" ;;
    *) return 1 ;;
  esac
}

# shellcheck disable=SC2034  # used by scripts/build_images.sh
HB_ALL_IMAGES="btbridge-lab:dev btbridge-hwkit:dev btbridge-pw:bookworm btbridge-pw:trixie btbridge-avahi:dev btbridge-browser:dev \
btbridge-static:dev btbridge-syslog:dev btbridge-py314:dev btbridge-security:dev btbridge-os:dev btbridge-entry:dev hifi-demo:dev"

hb_image_present() { docker image inspect "$1" >/dev/null 2>&1; }

# hb_build_image NAME [reason]: builds the image from its Dockerfile (no check, BuildKit plain progress filtered to the steps).
# Extra flags for docker build (e.g. --pull --no-cache) come from $HB_BUILD_FLAGS.
hb_build_image() {
  local name=$1 reason=${2:-} arg
  hb_image_info "$name" || { hb_err "unknown project image '$name' (scripts/lib/common.sh)"; return 2; }
  local -a args=()
  for arg in $HB_I_ARGS; do args+=(--build-arg "$arg"); done
  hb_info "building $name - ${reason:-$HB_I_WHY} (about $HB_I_MB MB)"
  # shellcheck disable=SC2086  # HB_BUILD_FLAGS is a list of flags on purpose
  HB_FILTER='^#[0-9]+ (\[|CACHED|ERROR)|^Step [0-9]+/|^ERROR|naming to|writing image|^Successfully' BUILDKIT_PROGRESS=plain \
    hb_run "build $name" docker build ${HB_BUILD_FLAGS:-} "${args[@]}" -f "$HB_ROOT/$HB_I_FILE" -t "$name" "$HB_ROOT/$HB_I_CTX"
}

# hb_ensure_image NAME [--always]: a missing image is built (after the project images it is built FROM); --always also refreshes an
# existing one (fast with a warm layer cache) so that code copied into the image never goes stale.
hb_ensure_image() {
  local name=$1 mode=${2:-} dep
  hb_image_info "$name" || { hb_err "unknown project image '$name' (scripts/lib/common.sh)"; return 2; }
  local deps=$HB_I_DEPS why=$HB_I_WHY
  for dep in $deps; do hb_ensure_image "$dep" || return $?; done
  if hb_image_present "$name"; then
    [ "$mode" = --always ] || { hb_info "image $name: present"; return 0; }
    hb_build_image "$name" "refreshing (layer cache makes this fast)"
  else
    hb_info "image $name is missing - first run: $why"
    hb_build_image "$name" "$why"
  fi
}

hb_ensure_images() { local i; for i in "$@"; do hb_ensure_image "$i" || return $?; done; }

# hb_ensure_pulled REF [reason]: pulls a third-party image once (pinned by digest in the compose files); shows what is downloaded and why.
hb_ensure_pulled() {
  local ref=$1 why=${2:-needed by the stack}
  hb_image_present "$ref" && { hb_info "image ${ref%%@*}: present"; return 0; }
  hb_info "pulling ${ref%%@*} - $why"
  HB_FILTER='Pulling from|Digest:|Status:|^[0-9a-f]{12}: (Pull complete|Already exists)' hb_run "pull ${ref%%@*}" docker pull "$ref"
}

# hb_pull_compose_images COMPOSE_FILE [compose args...]: pulls every image of the stack that is neither present nor built from this
# repository (project images: hb_ensure_image). Uses the exact (pinned) references from the compose file.
hb_pull_compose_images() {
  local f=$1 img refs; shift
  refs=$(docker compose -f "$f" "$@" config --images 2>/dev/null) || { hb_warn "could not list the images of $f (compose will pull them itself)"; return 0; }
  for img in $refs; do
    case "$img" in btbridge-*|hifi-demo:*) continue ;; esac     # built from this repository: hb_ensure_image
    hb_ensure_pulled "$img" "third-party service of $(basename "$(dirname "$f")")/$(basename "$f")" || return $?
  done
}
