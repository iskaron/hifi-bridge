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

### P1 (persistence audit + partition layout; branch `wip/p1-persist-layout` from `040cc3b`)
- 2026-11-20 start. Audit of write paths (bridge, units, overlay, built rpi3_64 target tree via debugfs): journald has no config
  (Storage=auto, no /var/log/journal -> RAM by accident, unbounded by explicit limits); no systemd-coredump, kernel core_pattern default
  `core` + systemd DefaultLimitCORE not set; `faults.jsonl` (bounded 200, fsync) is written to /data; timesyncd touches the clock file on
  /data every 60 s; hifi-status.txt rewritten on the boot FAT on every boot (timestamped lines); OTA `.upload`/`.part` and pid-named
  atomic-write temp files can survive a power cut forever (no boot-time cleanup); remote syslog exists (TLS, bounded in-memory queue, no spool).
- Measured ext4 overhead (mke2fs 1.47.2, Docker): 256 MiB with `-b 4096 -i 65536` leaves 225 MiB for non-root; default options (1 KiB
  blocks, 4096 bytes/inode) leave 217 MiB at 256 MiB and waste 16 MiB on inode tables.
- Commits `306e3f3` (bridge: RAM fault history, boot cleanup, status file only on change, PYTHONDONTWRITEBYTECODE) and `85b25d3`
  (OS: 64/384/384/256 MiB layout, os/storage.json, journald volatile 16M/2M, no core dumps, timesync 900 s, image checks persist.*/size.*,
  size_plan rewrite, flash tool MIN_BYTES 1.8e9 / "smaller than 2 GB").
- BUILD rpi3_64 (incremental, 16:44-16:54 UTC, build+legal+sbom ~10 min): image check 0 FAIL / 119 PASS / 20 INFO, secret scan 0 findings,
  OTA check PASS (bundle 77 190 603 bytes = 73.6 MiB, unchanged by the bigger slot). Card image 1092 MiB apparent, 192 MiB on disk.
  rootfs 193.3 MiB used of 384 (50 %), data 225.8 MiB available before growth (need 216). Negative control before the build: the
  package-6 image fails 10 of the new checks (as expected).
- Tests (final, after all commits): UNIT tests/unit as root on an internal network: 1079 passed, 1 failed
  (`test_stream_faults::test_a_reader_that_falls_a_whole_ring_behind_is_skipped_forward_and_counted`, timing under load - stream code
  untouched; 5/5 green on rerun). As non-root with `--network none` 17 D-Bus/mDNS tests fail identically on baseline 040cc3b
  (environment). STATIC tests/os + tests/flashtool 182 passed; flash tool suite (scripts/run_flashtool.sh, incl. GUI) 159 passed;
  run_static.sh exit 0; os_defconfigs.sh check PASS x3; secret scan (pinned Gitleaks image, `dir` over the branch's patches - the
  worktree's .git cannot be mounted for `git` mode) no leaks.
- Evidence: reports/evidence/os_image_rpi3_64.md, os_ota_rpi3_64.txt (header line "Final polish P1 rebuild ... commit 85b25d3"),
  os_secret_scan_rpi3_64.json, os_size_plan.{md,json} (rpi3_64 only; the other boards follow in P2).
