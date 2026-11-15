# HANDOFF_FOR_CHATGPT template

Write to `reports/HANDOFF_FOR_CHATGPT.md` before stopping at a major milestone.

## 1. Status and requested review
Phase, date/time, status = `COMPLETE / PARTIAL / BLOCKED`, single-sentence summary; exact decisions desired from ChatGPT/owner.

## 2. Guest experience outcome
Can an ordinary visitor pair, connect, press Play and hear Lyrion? List the tested path and limitations.

## 3. Implementation / architecture
Summary, module boundaries, changed files, uncommitted Git status (read-only), notable design decisions, decisions deferred, third-party deps and licenses.

## 4. Tests and environment
Commands with **actual outputs** and PASS/FAIL/SKIP, device model, OS, LMS version, Squeezelite/legacy model, network config and reproduction steps. Distinguish measured / integrated / simulated / not tested.

## 5. Timing and quality measurements
For each real scenario: number of runs, method, Play -> sound, Pause -> silence, resume, jitter/dropouts, CPU/RAM/radio conditions. If no real rig say "NOT MEASURED — hardware unavailable".

## 6. Risks, defects, and unverified claims
Severity/impact/repro/workarounds. Especially ownership race, LMS stream compatibility, phone volume, pause buffering, old devices, Zero 2 W, OTA if relevant.

## 7. Next best actions
Independent remaining tasks and proposed fixes, effort/tradeoffs; do not start next phase until approved.

## 8. Operator handoff
A concise copy-paste paragraph the owner can send to ChatGPT alongside the file and repo snapshot. Redact secrets.
