#!/usr/bin/env bash
# Everything: unit + integration (incl. fake-BlueZ D-Bus tests) + both outage scenarios.
# Output is tee'd to $LOG (default reports/evidence/full_run.log). For a clean-slate run: scripts/lab_down.sh first.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG="${LOG:-reports/evidence/full_run.log}"
mkdir -p reports/evidence
scripts/lab_up.sh >/dev/null
{
  echo "### date: $(date -u +%FT%TZ)"; docker --version
  echo "### UNIT";        scripts/run_unit.sh -q                       ; U=$?
  echo "### INTEGRATION"; scripts/run_integration.sh -q               ; I=$?
  echo "### OUTAGE lms";  scripts/test_outage.sh lms     | grep -vE "Container"; O1=$?
  echo "### OUTAGE player"; scripts/test_outage.sh player1 | grep -vE "Container"; O2=$?
  echo "exit codes: unit=$U integration=$I outage_lms=$O1 outage_player=$O2"
} 2>&1 | tee "$LOG"
