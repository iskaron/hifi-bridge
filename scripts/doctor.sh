#!/usr/bin/env bash
# Checks this computer for everything `make demo` and the test scripts need and says what is missing - changes nothing, starts nothing.
#   scripts/doctor.sh        (make doctor)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
echo "Hi-Fi Bridge environment check (details and minimum versions: docs/DOCKER_REQUIREMENTS.md)"
hb_preflight --compose --tools "make" --disk 3000 "the demo images" \
  --ports "${DEMO_HTTP_PORT:-18080} ${DEMO_HTTPS_PORT:-18443} ${DEMO_CONSOLE_PORT:-18100} ${DEMO_LMS_PORT:-19000}" \
  "free the ports or set DEMO_HTTP_PORT, DEMO_HTTPS_PORT, DEMO_CONSOLE_PORT, DEMO_LMS_PORT (docs/DEMO.md); a demo that is already running keeps its ports"
rc=$?
if [ $rc -eq 0 ] || command -v docker >/dev/null 2>&1; then
  echo "project images (built or pulled automatically by the scripts that need them):"
  for i in $HB_ALL_IMAGES; do
    if docker image inspect "$i" >/dev/null 2>&1; then printf '  present  %s\n' "$i"; else printf '  missing  %s (built on first use)\n' "$i"; fi
  done
fi
[ $rc -eq 0 ] && echo "OK: nothing prevents 'make demo'." || echo "Not ready: see the messages above (English; German hint below each)."
exit $rc
