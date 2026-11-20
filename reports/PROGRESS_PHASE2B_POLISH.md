# Progress log - phase 2b final polish and pre-hardware readiness

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (none here). Newest entries at the bottom.
Scope: owner work package of 2026-11-20 (DECISIONS #120-#126). Baseline `develop` = `origin/develop` = `77ba736` (CI 44721926361 17/17).

## Plan (one subagent at a time; each package merged, tested and pushed before the next)
| # | Package | Model | Status |
|---|---|---|---|
| P1 | Persistence audit + RAM-only logs, partition layout 64/384/384/data, measured data minimum, size checks, manifest, provisioning, flash tool | Opus | pending |
| P2 | U-Boot env with libubootenv + full default env, corruption simulation; per-board firmware pruning, licence texts, source-bundle prep; rebuild all three boards | Opus | pending |
| P3 | Wizard network step (hotspot-only setup), reusing the network state machine | Opus | pending |
| P4 | Integration flake #86: cold-lab reproduction, root cause, isolation fix | Opus | pending |
| P5 | MQTT #50 reset behaviour; macOS signing/notarisation pipeline + guide; firmware risk docs | Sonnet | pending |
| P6 | Final QA: full suites, image checks, secret scans, CI, docs, handoff | coordinator | pending |

## 2026-11-20
- Owner decisions recorded as DECISIONS #120-#126.
- P1 started (Opus agent, worktree branch `wip/p1-persist-layout`).
- Coordinator, in parallel and without touching P1's files:
  * `8ace337` fix(mqtt): the stream title survived a Bluetooth disconnect (WebUI and retained MQTT state kept showing the last track);
    a clean MQTT stop now publishes a cleared retained state before `offline` (DECISIONS #124, OPEN_QUESTIONS #50 resolved). UNIT: unit
    suite 1067 passed.
  * `87d0b23` build(flashtool): macOS signing/notarisation prepared (DECISIONS #126): manual workflow `flashtool-macos.yml`, `macos/build.sh`,
    `macos/sign_and_notarize.sh`, empty entitlements, docs/MACOS_SIGNING.md, STATIC `tests/entry/test_workflows.py` (entry suite 100 passed,
    static exit 0, flash tool suite 158 passed, Linux package rebuilt ok). Never run on macOS.
