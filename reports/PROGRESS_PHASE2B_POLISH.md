# Progress log - phase 2b final polish and pre-hardware readiness

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (none here). Newest entries at the bottom.
Scope: owner work package of 2026-11-20 (DECISIONS #120-#126). Baseline `develop` = `origin/develop` = `77ba736` (CI 44721926361 17/17).

## Plan (one subagent at a time; each package merged, tested and pushed before the next)
| # | Package | Model | Status |
|---|---|---|---|
| P1 ✔ | Persistence audit + RAM-only logs, partition layout 64/384/384/data, measured data minimum, size checks, manifest, provisioning, flash tool | Opus | pending |
| P2 ✔ | U-Boot env with libubootenv + full default env, corruption simulation; per-board firmware pruning, licence texts, source-bundle prep; rebuild all three boards | Opus | pending |
| P3 ✔ | Wizard network step (hotspot-only setup), reusing the network state machine | Opus | pending |
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
- P1 merged as `8475549` (conflict only in this file). Coordinator: PRODUCT_SPEC log line updated to #121 (OPEN_QUESTIONS #89 resolved),
  OPEN_QUESTIONS #92 (macOS helper signature check). Merged-state suites: unit 1085 passed; run_os exit codes 0/0/0; entry 100 passed;
  static exit 0; flash tool 159 passed; Gitleaks over 268 commits rc=0. Pushed `8475549` to origin/develop.
- P2 started (Opus agent, branch `wip/p2-uboot-firmware`): libubootenv + full default env, corruption simulation, per-board firmware,
  source-bundle preparation, rebuild of all three boards.

### P2 (U-Boot environment, firmware per board, source provision, rebuild of all boards; branch `wip/p2-uboot-firmware` from `8475549`)
- 2026-11-20 start. libubootenv 0.3.7 (Buildroot pin, sha256 = Buildroot's hash) read in Docker: regular files in `fw_env.config` =
  `DEVICE_FILE`, incremental serials, writes the copy not read (in place, `fsync`), same selection rules as U-Boot 2026.01
  `env_check_redund()` (read in the U-Boot build tree); both copies bad -> `/etc/u-boot-initial-env`; a missing listed file fails the
  configuration (`realpath`); **finding:** a copy shorter than the environment makes the whole read fail (`-EIO`) -> default used and
  stored over the valid copy (upstream master unchanged). Patch written (`patches/libubootenv/0001`).
- SIMULATED comparison on the existing rpi3_64 copies (`os/tools/uboot_env_sim.py`, 13 cases): patched libubootenv all as intended;
  stock 0.3.7 loses the slot state on every truncated-copy case; the previous images' uboot-tools 2025.10 stored `bootcmd=bootp; setenv
  bootargs root=/dev/nfs ...` with both copies bad and spun at 100 % CPU (killed after 15 s) on every short copy.
- Implemented: `BR2_PACKAGE_LIBUBOOTENV=y`, target uboot-tools removed, `BR2_GLOBAL_PATCH_DIR` + our patch, `uboot/linux-default-env.txt`
  + post-build writing `/etc/u-boot-initial-env`; image check `boot.uboot-env-linux-default`, `boot.uboot-builtin-env`,
  `boot.uboot-env-corruption-matrix` (+ INFO comparison with the uboot-tools host build), `rauc.env-tool-libubootenv`,
  `rauc.env-tool-single`; `os/tools/build_libubootenv.sh` (native compile of the same source + patches, source in btbridge-os
  `/opt/src`, pin checked by `os_defconfigs.sh check`); real-RAUC suite now uses these tools (13 passed). Per-board radio firmware lists
  `firmware/radio-<compatible>.txt` (kernel name tables of our kernel read: brcmfmac `sdio.c`, `btbcm.c`), post-build pruning with
  sha256 check, post-image licence texts + NOTICE per board, image checks `firmware.*`; `scripts/os_source_bundle.sh`;
  `os_build.sh retarget`; evidence labels (`HIFI_EVIDENCE_LABEL`).
- Tests during development: tests/os 102 passed (new: test_uboot_env.py 15, test_radio_firmware.py 16, licence test x3 boards);
  `scripts/run_ota.sh` 13 passed with the image's libubootenv; `os_defconfigs.sh check` PASS x3 + libubootenv pin PASS.
- BUILD (incremental `retarget` builds, HIFI_LAB = main checkout's .lab, 3 CPUs / 8 GB): rpi3_64 make 542 s (+153 s after the
  file-list fix below), rpizero2w_64 627 s and rpi4_64 607 s (both `HIFI_OS_REBUILD="uboot host-uboot-tools"`: first rebuild since
  package 6, so the 7b-2 U-Boot fragment, boot.cmd and the P1 layout reach these boards for the first time). Buildroot applied our
  libubootenv patch ("Applying 0001-uboot_env-..." in the build log).
- Finding: after `retarget` the rpi3_64 image check failed `rauc.env-tool-single` - `packages-file-list.txt` concatenates every
  `build/*/.files-list.txt`, so the removed uboot-tools were still listed (target tree clean). `retarget` now also deletes those lists.
- Image checks (all three): 0 FAIL, 128 PASS, 21 INFO; corruption matrix 13/13; secret scans 0 findings (6073/6069/6074 files + raw
  card); OTA checks pass, bundles 76 056 010 / 75 945 422 / 76 289 483 bytes. Size plan regenerated for all three boards (rootfs
  191.4-191.9 MiB of 384, all checks ok). Evidence labelled "final polish P2".
- Source bundle rpi3_64 (`scripts/os_source_bundle.sh`, commit 6030a84): 11 files + legal-info (512 files, hard links), overlays
  source 957fc9f9 (358 overlay .dts), 21 toolchain runtime files listed, 1.2 GB apparent, under .lab/source-bundle/ only.
- Suites: `scripts/run_os.sh` defconfigs PASS x3 + libubootenv pin PASS, licences 53 packages/board 0 review, tests "215 passed";
  `scripts/run_static.sh` "static exit: 0"; `scripts/run_entry_tests.sh` "100 passed"; `scripts/run_ota.sh` "13 passed"; Gitleaks
  (pinned image, dir mode over the 8 branch patches) rc=0.
- Reproducibility (repeated, disk 135-144 GB free): `rpi3_64-repro` rebuilt from scratch in its existing directory (`clean`,
  `HIFI_OS_CCACHE=0`, make 6426 s) vs. the incremental P2 `rpi3_64` build: rootfs 7347 entries, 0 added, 0 removed, 4 changed (the
  four known ccache-affected files: libwireplumber, two WirePlumber modules, pylibmount.so); boot FAT and data partition bit-identical;
  image check of the repro build 0 FAIL / 128 PASS / 21 INFO, secret scan 0 (its evidence copies were reverted, the committed rpi3_64
  evidence is the main build's).
- CI run 44783605532 on `8475549` (P1 merge): 17/17 green.
- P2 merged as `23a4fa6`. Merged-state suites: run_os exit codes 0/0/0; run_ota tests=0; static exit 0; entry 100 passed; unit 1085
  passed; Gitleaks over 279 commits rc=0. Pushed `23a4fa6`.
- P3 started (Opus agent, branch `wip/p3-wizard-network`): wizard network step for hotspot-only setup.
- CI run 44817088946 on `23a4fa6` (P2 merge): 17/17 green.
- 2026-11-20 ~23:00: P3 agent stopped on the account rate limit. Its uncommitted work was saved unchanged as `88c6d8f` (chore(wip)) on
  `wip/p3-wizard-network` (new `views_setup_network.py`, tests, i18n, CSS in progress); the agent was resumed on the same branch.

### P3 (wizard network step, DECISIONS #122; branch `wip/p3-wizard-network` from `23a4fa6`)
- 2026-11-20 start. Read: views/views_network/server, netclient/netmanager/netagent, sim_net, the WebUI/network tests, browser and demo
  tests. Finding: the wizard had no server-side position (every redirect went to `/setup/bluetooth`) and the network page's handlers
  always answered to `/settings/network` - both needed for "resume after reconnect" and "stay in the wizard".
- Implemented (no change to netmanager/netclient/netagent; no state-machine bug found): step order Owner -> Network -> Bluetooth -> Lyrion
  -> Player -> Done (#137); `webui/views_setup_network.py` renders the step from the network page's pieces (`wifi_join` factored out of
  `wifi_card`, `trial_card(base, extra)`, handlers with a return address; #138); `views.wizard_resume` + `WebUI.wizard_at` (RAM) for every
  redirect into the wizard; one automatic scan when the radio has no list (#139); strings `wizard.net.*` DE/EN in `_generate_network.py`;
  network.css for the step (nested cards, explanation block, `<details>` with a disclosure marker); `.button.primary` in app.css.
- Interrupted by the account rate limit; the coordinator saved the work as WIP commit `88c6d8f` (later split into the commits below).
- Lab: `btkit.appliance_lab --boot-dir` applies provisioning before the SIMULATED agent (#140); compose service `appliance-setup`
  (profile browser, `tests/browser/setup-provision.json`: lab-only setup code + hotspot password, local_hostname `hifi-setup`).
- Tests (all in Docker; own compose projects `hifi-p3-unit`, `hifi-p3-browser`, `hifi-demo-test` with an own image tag `hifi-demo:p3`,
  so neither `btbridge-lab` nor `hifi-demo` nor the `hifi-demo:dev` image was touched; all removed afterwards):
  * UNIT `tests/unit` (root, internal lab network of an own project): **1106 passed**, 1 warning (pre-existing harness thread in
    `test_stream_faults.py`, untouched). New `tests/unit/test_webui_setup_network.py`: 21 tests.
  * BROWSER (`scripts/run_browser.sh` steps in project `hifi-p3-browser`): **13 passed** (final run after an axe "heading-order"
    finding (moderate) on the Ethernet variant was fixed with an h2 inside `<details>`; axe: no violations on the new pages). New
    `tests/browser/test_ui_wizard_network.py` (hotspot -> wrong password rolled back after the real 60 s -> retry -> new context at the
    device's name -> login -> Keep -> next step; DE desktop; keyboard `<details>`), `test_ui_browser.py` test_01 extended (offline
    and Ethernet states of the step).
  * DEMO stack (`scripts/test_demo.sh` steps, project `hifi-demo-test`, image `hifi-demo:p3`; part 6 "SD-card recovery" skipped because
    `scripts/demo.sh recover` would use the default image tag - recovery code untouched): isolation rc=0, setup UI 2 passed (the
    wizard joins the SIMULATED "Home"), chain 11 passed, console UI 8 passed, rc=0.
  * NET `scripts/run_net.sh`: 17 passed, live checks all PASS, "network exit codes: tests=0 live=0" (network code unchanged; the
    wizard flow itself is covered at the UNIT layer with the real agent over a UNIX socket - tests/net has no radio simulation).
  * STATIC `scripts/run_static.sh`: "static exit: 0"; ENTRY `scripts/run_entry_tests.sh` (compose file touched): "100 passed";
    SECURITY `scripts/run_security.sh`: bandit medium/high rc=0.
- Evidence files regenerated by the runs (bandit, network_realtools, demo measurements) were reverted (no content change of ours);
  unrelated screenshots regenerated by the browser run were reverted, the new curated ones are `wiz-0[1-4]-*.png` and
  `01c-wizard-network-ethernet-mobile-light.png`.
