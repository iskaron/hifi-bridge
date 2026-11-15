#!/usr/bin/env bash
# Everything, all in Docker: unit + integration (real LMS/Squeezelite/Mosquitto, fake BlueZ) + outage scenarios + PipeWire capture
# (real PipeWire 0.3.65 and 1.4.2) + hardware-kit self-tests (real SBC/AAC codecs) + real-browser UI tests (Chromium + axe-core).
# Output is tee'd to $LOG (default reports/evidence/full_run.log). For a clean-slate run: scripts/lab_down.sh first.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG="${LOG:-reports/evidence/full_run.log}"
mkdir -p reports/evidence
scripts/lab_up.sh >/dev/null
{
  echo "### date: $(date -u +%FT%TZ)"; docker --version; git rev-parse --short HEAD 2>/dev/null
  echo "### UNIT";          scripts/run_unit.sh -q                                    ; U=$?
  echo "### INTEGRATION";   scripts/run_integration.sh -q                             ; I=$?
  echo "### OUTAGE lms";    scripts/test_outage.sh lms     | grep -vE "Container"; O1=${PIPESTATUS[0]}
  echo "### OUTAGE player"; scripts/test_outage.sh player1 | grep -vE "Container"; O2=${PIPESTATUS[0]}
  echo "### PIPEWIRE";      scripts/run_pipewire.sh -q                                ; P=$?
  echo "### HWKIT";         scripts/run_hwkit.sh -q                                   ; H=$?
  echo "### BROWSER";       scripts/run_browser.sh -q                                 ; B=$?
  echo "exit codes: unit=$U integration=$I outage_lms=$O1 outage_player=$O2 pipewire=$P hwkit=$H browser=$B"
} 2>&1 | tee "$LOG"
