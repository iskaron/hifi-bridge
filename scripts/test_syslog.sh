#!/usr/bin/env bash
# INTEGRATION: optional secure remote syslog -> REAL rsyslog (TLS). Certificates are created with the bridge's own CertManager.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose
hb_ensure_images btbridge-lab:dev btbridge-syslog:dev
scripts/lab_up.sh >/dev/null
cd docker || exit 1
docker compose --profile syslog rm -sf syslog >/dev/null 2>&1
docker volume rm -f btbridge-lab_syslog-certs btbridge-lab_syslog-out >/dev/null 2>&1
# 1. server certificate for syslog.lan from a fresh device CA (into the shared volume)
docker compose run --rm -T -v btbridge-lab_syslog-certs:/syslog-certs runner python3 -c \
  "from btbridge.net.certs import CertManager; CertManager('/syslog-certs','syslogca').issue_server(['syslog.lan']); print('certs ok')" || exit 1
docker compose run --rm -T -v btbridge-lab_syslog-certs:/syslog-certs runner chmod -R a+rX /syslog-certs/certs
# 2. rsyslog, 3. tests from the runner
docker compose --profile syslog up -d syslog >/dev/null 2>&1; sleep 3
docker compose run --rm -T -v btbridge-lab_syslog-certs:/syslog-certs -v btbridge-lab_syslog-out:/syslog-out runner \
  python3 -m pytest -p no:cacheprovider -v tests/syslog "$@"; rc=$?
docker compose --profile syslog logs --no-color syslog 2>&1 | grep -iE "error|warn" | head -5
docker compose --profile syslog rm -sf syslog >/dev/null 2>&1
exit $rc
