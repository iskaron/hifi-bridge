# Progress log - phase 2b: software completion and hardware readiness

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, BUILD (built in Docker, never booted), REAL-HARDWARE (none here). Newest entries at the bottom.
Scope: owner work package of 2026-11-18 (DECISIONS #73/#74).

## 2026-11-18
- Baseline: `develop` = `origin/develop` = `486dfee`. Host (read-only): 4 CPUs, 22 GB RAM (~11 GB available), Docker data filesystem 109 GB
  free, /home 185 GB free; Docker Engine 29.1.3, Compose 2.40.3. The owner's demo (`hifi-demo`) and the shared lab (`btbridge-lab`) are
  running and are left untouched by the work streams.
- Work split into five parallel streams, each in its own git worktree, merged into `develop` by the coordinator:
  1. Buildroot images (Pi Zero 2 W, 3 B+, 4), SBOM, licences incl. Broadcom firmware, image secret scan - one board build at a time
     (`--cpus=3 --memory=8g`), outputs in `.lab/br-out/`.
  2. Network management (Ethernet/Wi-Fi, IPv4/IPv6, Wi-Fi XOR hotspot fallback, firewall, hostname, time sync, recovery).
  3. OTA/update management on RAUC (check, signed upload/download, progress, A/B, health check, rollback, backup/restore).
  4. Flash tool GUI (PySide6, platform disk adapters, privileged helper, packaging preparation, LGPL).
  5. Entry points and demo (Docker/Compose minimum versions + pre-flight check, automatic image builds, progress output, demo loudness,
     clean-environment test, Lyrion isolation regression tests, pinned images).
- Independent review of the existing product code (read-only; repro scripts run in `--network none` containers): 6 HIGH, 12 MEDIUM,
  12 LOW findings, all HIGH reproduced except the Avahi half of H5 (code reading). Highlights: unauthenticated memory exhaustion via
  `Content-Length: -1` (H1); a slow/unreachable Lyrion holds the controller lock long enough for the systemd watchdog to restart the bridge
  in a loop (H2); the stream URL's address fixed at startup (H3); a bad MQTT CA or a long non-ASCII device name crash the start (H4/H5); a
  power cut during CA creation leaves a permanent crash loop (H6). Flash-tool findings M11/M12 handed to the flash-tool stream; all others to
  a sixth stream (review fixes, test-first).
- **Interruption (~14:20):** all parallel agents stopped on the account rate limit. Owner instruction: one subagent at a time, finish each
  package completely, order 1 review fixes → 2 entry/demo → 3 network → 4 OTA → 5 flash tool → 6 Buildroot → 7 QA. All interrupted work
  was saved as `chore(wip)` commits on `wip/<package>` branches (nothing discarded; key scan of the branches clean). Resume point:
  `reports/PHASE2B_WORKSTATE.md`.

### Package 1 - review fixes (done)
- Opus agent, test-first, 30 commits, merged as `618e395`: all 28 in-scope findings fixed (H1-H6, M1-M10, L1-L12; M10 only the
  non-product part; L11 hardware-pending). Unit suite 296 → 428 tests. Details and test names: `reports/REVIEW_PHASE2B_EXISTING_CODE.md`.
  DECISIONS #75-#77, OPEN_QUESTIONS #50-#56.
- Full run after the merge (`reports/evidence/full_run_pkg1_review_fixes.log`, 2026-11-18T15:36Z): static, unit 428, outages, PipeWire
  11+1 skip / 12, hwkit 32, syslog 2, Python 3.14 427+1 skip, security, OS 46, Home Assistant 7/7, demo stack (11 chain + 8 UI), browser 5
  green. Four reds, analysed:
  * integration 1/55 (MQTT last will): **test bug** - fixed retained topic; a stale "online" from an earlier run satisfied the first
    check before the client connected. Fresh topic per run; 3/3 green after the fix.
  * demo unit 1/52 (pairing window): **test race** - the trust flag can be set before the paired list; now waits. 6/6 green.
  * secrets: two Gitleaks false positives (a fake token in a new unit test, now neutral; the PEM header quoted in the image scanner's
    docstring on `wip/os`) → `.gitleaksignore` with commit-specific fingerprints. Rescan clean (133 commits).
  * mDNS 8/13: **environment, not the change** - the identical failure on the pre-package commit `c475683`. On the lab network (no host
    address since DECISIONS #71, hence no default route) an unbound multicast send/`IP_ADD_MEMBERSHIP` on INADDR_ANY fails with
    ENETUNREACH; the same suite was green locally on 2026-11-17 and in CI. Left to CI (fresh networks) as the deciding run; to be analysed
    further in package 7 if it persists.
- Pushed `82b2bb3`; GitHub Actions run 44350223892.

### Package 2 - demo and entry points (done)
- Sonnet agent (resumed on `wip/entry`), merged as `46483a0`. Docker minimums Engine 24.0 + Compose 2.20 (Engine >= 29: Compose >= 2.24.5)
  from a dind matrix of six engines x two/three Compose versions; `gw_priority` removed (the owner's failure); pre-flight check
  (`make doctor`); shared `scripts/lib/common.sh` with image registry, automatic builds/pulls, progress + heartbeat; demo loudness
  (-42.1 → -25.7 dBFS RMS at the DAC, product audio unchanged); clean-clone runs of `make demo` + `hwkit_bundle.sh` in empty dind sandboxes
  on 24.0.9/2.20.3, 25.0.5/2.24.5 (the owner's pair) and 29.1.5/2.36.2 - all PASS; static + runtime isolation regression tests with
  negative control; every third-party image pinned by tag + digest (Lyrion 9.1.2). 90 entry tests. DECISIONS #78-#82, OPEN_QUESTIONS
  #48/#49 resolved, #57/#58 new.
- Full run after the merge (`reports/evidence/full_run_pkg2_entry.log`): entry 90, unit 428, Python 3.14, security, Gitleaks (146 commits),
  OS 46, Home Assistant 7/7, demo unit 52, **demo stack incl. the new runtime isolation check** (rc=0), browser 5, PipeWire, hwkit, syslog,
  outages green. Reds: mDNS (same host-environment issue as in package 1, green in CI) and integration 1/55: the first test right after the
  lab's Lyrion was recreated on the newly pinned 9.1.2 image received 0 blocks; not reproducible (4 reruns, 8/8 green) - recorded as a
  cold-start flake, watched in CI (cold Lyrion on every run).
- Package 2 CI: GitHub Actions run 44372722429 on `242184b` - 14/14 green (the cold-start flake did not recur).

### Package 3 - network management (in progress)
- Opus agent resumed on `wip/network`; `develop` merged into the branch (`3b66948`). Interrupted once more by the account rate limit
  (~19:00, reset 20:10); resumed 23:02.

### Package 3 - network management (done)
- Opus agent on `wip/network`, merged as `926e945`: root network agent `hifi-netd` (six-request socket API, strict re-validation), pure
  state machine (client Wi-Fi XOR hotspot, 60 s fallback, retries, trials with 3-min confirmation and rollback), rtnetlink/wpa/hostapd/iw
  adapters, WebUI network pages (DE/EN, axe-clean), nftables firewall, networkd/timesyncd/hostnamed, Avahi host name, `network-reset`
  recovery, docs/NETWORK.md, hardware tests N-1..N-11. Real-tool suite `scripts/run_net.sh` (nft, dnsmasq, hostapd, wpa_supplicant,
  networkd, avahi in containers; live firewall + DHCP) - now in CI (job `network`) and in the full run. Real bug found and fixed: passphrases
  with `#`/`"` were truncated by wpa_supplicant (DECISIONS #87). DECISIONS #83-#89, OPEN_QUESTIONS #59-#63.
- Full run (`reports/evidence/full_run_pkg3_network.log`): unit 618, network 16 + live, browser 9, demo stack, Home Assistant, OS, secrets
  (160 commits) etc. green; mDNS red (host environment, as before). Integration 1/55 red again: `test_end_to_end_bit_exact[wav]`, the first
  test after the lab is **recreated** - reproduced by force-recreating LMS + players: the first output cannot be aligned with the reference
  ("samples altered?", first audio at frame 190). Not seen in CI (cold LMS, fresh state, green) and not after a warm start: points at the
  lab's persisted LMS state in `.lab/`; analysed by a separate investigation (below).
- Investigation of `test_end_to_end_bit_exact[wav]` after a lab recreate (harness issue, not the product):
  - Evidence: the Squeezelite log of the failing session (`/captures/player1/squeezelite.log`, session of 22:28 UTC) shows the player
    connecting and receiving `audg gainL: 3840` (about 6 % gain) from Lyrion's persisted prefs, and *no further gain change* at all
    until the test stream (`audg 3840` again at stream start, and again 7 minutes later in the second run). So the player played at a
    fraction of unity gain: the generator no longer matches the stored samples ("samples altered?"), which is exactly the analyser's
    message. The sessions that passed show `audg 65536` right after the reset. The bridge's start sequence sends no volume command in
    this test (its commands are `play`/`release`/`power_on`/`volume_delta` and the first phone volume value does not jump), so the
    product is ruled out.
  - Root cause: the lab's `reset()` assumed the volume command lands. Lyrion's persisted per-player volume (DECISIONS #47, kept in
    `.lab/lms-config`) is applied when a player (re)connects, and a recreated Lyrion has no client objects before that (JSON-RPC to an
    unconnected player is dropped: `RemoteDisconnected`). Nothing waited for the players, and nothing checked the gain the player
    actually applied; the last suite leaves a low volume behind, so the first test after a recreate ran at it. The exact lost-command
    interleaving could not be re-created afterwards (my first run rewrote the prefs to volume 100; about a dozen further recreate attempts with
    a persisted volume of 6 passed), so the diagnosis is from the log evidence above, not from a re-played failure.
  - Fix (`testkit/btkit/lab.py`): `Lab.reset()` first waits until Lyrion lists all four lab players as connected, then sends the usual
    commands and confirms both Lyrion's `mixer volume == 100` **and the gain Squeezelite really applied** (last `audg` in its log
    == 65536), retrying (with a 99 -> 100 nudge) up to 10 times and raising a clear error otherwise.
  - Verification: persisted volume 6 on all players, then `docker compose up -d --force-recreate lms player1 player2 player3 legacy1`
    and `scripts/run_integration.sh -k test_end_to_end_bit_exact` immediately: 3 of 3 runs green (2 passed each; the log shows `audg 36`
    on connect followed by `audg 65536` from the reset), plus a warm run green.
- Package 3 CI: GitHub Actions run 44420917417 on `c4d95ba` - 15/15 green (new job `network`). Lab cold-start fix `c76c541` pushed.

### Package 4 - OTA update management (in progress)
- Opus agent resumed on `wip/ota` (2 commits + saved WIP: RAUC D-Bus client, simulated RAUC service, update check, streamed transfers,
  manager and health-check drafts).
- OTA agent reported the package complete (805 unit, 12 real-RAUC 1.13 tests, 11 browser, `make ota` with dev keys). Coordinator review:
  the optional passphrase encryption of backups used a self-built keystream (HMAC-SHA256 in counter mode) - against DECISIONS #50
  ("never own cryptography"). Sent back: standard cipher via the openssl CLI (pass-fd, no key material on command lines) + encrypt-then-MAC.
  Interrupted by the account rate limit (reset 04:00), resumed 06:47.
- Backup cipher replaced (`bee5b60`): scrypt → AES-256-CTR via the openssl CLI (pass-fd) + encrypt-then-MAC; 817 unit, 12 real-RAUC.

### Package 4 - OTA update management (done)
- Merged as `e8a95ff`: `bridge/btbridge/ota/` (RAUC D-Bus client, update check, resumable download + streamed upload, state machine with
  boot reconciliation, health check, backup), WebUI `/settings/update` + `/settings/backup` (DE/EN, axe-clean), D-Bus policy, health and
  reboot units, masked upstream mark-good, `make ota` with throw-away keys, docs/OTA.md, gate items G2 P3-9..P3-11. New suite
  `scripts/run_ota.sh` against the real RAUC 1.13 service - wired into CI (job `ota`) and the full run. DECISIONS #90-#95, OPEN_QUESTIONS
  #64-#67. A real slot install is impossible in Docker (no loop device): RAUC verifies the signature and stops at the bundle mount (asserted).
- Full run (`reports/evidence/full_run_pkg4_ota.log`): **integration 55/55** (the lab cold-start fix works), unit 817, OTA 12, network,
  browser 11, demo stack, HA, OS 49 etc. green; mDNS red (host environment). Python 3.14: 11 errors + 1 failure - the 3.14 image had no
  `dbus-daemon` for the new RAUC client tests; added to `docker/Dockerfile.py314` (the appliance has D-Bus) → 816 passed, 1 skipped.
- CI on `fe7f59e`: 15/16 - python314 red on the negative-Content-Length memory test (client body allocated after the baseline, 4 MiB margin
  too tight: +4.1 MiB) → body allocated first, 32 MiB vs. a 12 MiB limit (`fc13b0d`). CI on `fc13b0d`: python314 green, but two other jobs red:
  * demo: the new runtime isolation check reported "tcp 172.29.253.1:9000 connected" - a **false positive**: with `inhibit_ipv4` the bridge
    does not take the gateway address, so Docker gave `.1` to one of the project's own containers (Lyrion). Seen on Engine 28.0.4 in CI and
    locally on 29.1.3. The host-address check (no IPv4 on the bridge) already covers the host; candidates owned by a project container are
    now skipped and reported as a note.
  * pipewire (bookworm, 0.3.65): pipewire-pulse refused connections for 30 s on a cold runner (OPEN_QUESTIONS #26 again); the start script
    now restarts a pipewire-pulse that exited during start-up once (recorded in its log) and prints all three daemon logs on failure.
  Both suites green locally (demo stack rc=0, PipeWire 11+1/12, static 0, entry 90).
- CI on `d4a19d5`: GitHub Actions run 44483643387 - **16/16 green**. Package 4 done.

### Package 5 - flash tool GUI (in progress)
- Opus agent resumed on `wip/flashtool` (2 commits + saved WIP: board table, publisher key store, catalogue, resumable downloads, image
  verification; helper/platform adapters and M11 in progress).
- Interrupted twice by account limits (incl. a weekly Opus limit); work saved as `chore(wip)` commits each time, resumed successfully.

### Package 5 - flash tool (done)
- Opus agent on `wip/flashtool`, merged as `1e8c7c5`: PySide6 wizard (DE/EN, light/dark/system, accessible), elevated one-shot helper with a
  strict JSON-lines protocol and connect-back token (pkexec/osascript/UAC), Linux/macOS/Windows disk adapters (recorded outputs, fakes),
  catalogue + resumable downloads + signature/hash checks, custom publisher keys, provisioning via the device's validators, owner sheet,
  P7 trust bootstrap, review fixes M11/M12, Linux PyInstaller package built + smoke-tested in Docker, Windows/macOS prepared only, LGPL
  texts shipped (the PySide6 wheels contain none). New suite `scripts/run_flashtool.sh`: 158 tests, flash tool 100 % line + branch; wired
  into CI (job `flashtool`) and the full run (+ package build). DECISIONS #96-#99, OPEN_QUESTIONS #68-#72. 22 screenshots `flashtool-*.png`.
- Full run (`reports/evidence/full_run_pkg5_flashtool.log`): all suites green incl. integration 55/55, flash tool + package, OS 127; only
  mDNS red (host environment, green in CI).
- Package 5 CI: GitHub Actions run 44504983152 on `897a502` - **17/17 green** (new job `flashtool`).

### Package 6 - Buildroot images (in progress)
- Opus agent resumed on `wip/os` (4 commits + saved WIP; Pi 3 B+ had been built once). Task: merge the network/OTA OS changes, build all
  three boards one at a time, image checks, SBOM, licences, sizes.

### Package 6 - Buildroot images (done, results BUILD-VERIFIED only)
- Opus agent on `wip/os` (18 commits), merged into develop. All three boards **BUILD-VERIFIED** in Docker (`--cpus=3 --memory=8g`,
  `BR2_JLEVEL=3`): rpi3_64 3631 s (warm ccache) / 6980 s (no ccache), rpizero2w_64 3645 s, rpi4_64 7485 s. Card image 644 MiB each,
  rootfs ~185 MiB used of 256 MiB, dev RAUC bundle ~77 MB.
- Image checks (`scripts/os_check_image.sh`): 0 FAIL / 89 PASS per board; secret scan of rootfs, boot FAT and raw card: 0 findings;
  `make ota` bundle accepted by the image keyring, foreign-signed bundle refused; systemd-analyze verify of all 12 `hifi-*` units clean.
- Reproducibility (rpi3_64, ccache vs. no ccache): MBR, boot FAT, data, slot B identical; 7394/7398 rootfs files identical, the 4 others
  differ only by ccache-rewritten `__FILE__` paths -> release builds without ccache (DECISIONS #102).
- Fixed on the way: post-image symlink size bug, host-python `_ssl`, invalid `update-trust.json` with commas in a CA subject, stock
  wpa_supplicant/avahi-dnsconfd enabled, unaligned partitions, rootfs 1 KiB blocks + journal, rw remount / no `/var` strategy / kernel
  outside the slot, several non-reproducibility sources.
- Firmware licences read from the archives (docs/IMAGE_LICENSING.md): publicly redistributable unmodified, with texts, only for Raspberry Pi
  hardware; Synaptics BT licence was missing upstream and is now shipped. OPEN_QUESTIONS #35 largely resolved; new #73-#77 (export control,
  revocable Synaptics licence, release source obligations, partition sizes / 2 GB card, machine-id/DUID). DECISIONS #100-#104.
- Finding from the agent fixed by me: OTA backup `BrokenPipeError` race when openssl exits before reading the key (`f365404`, regression test).
- Not verified: anything at runtime (boot, U-Boot env, RAUC install/rollback, power cuts, radio firmware) - HARDWARE-PENDING.

### Package 7 - final QA (in progress)
- 7a: independent Opus review of packages 3-6 -> `reports/REVIEW_PHASE2B_NEW_CODE.md`: 14 findings (1 High, 5 Medium, 8 Low).
- Package 6 CI (run 44597478524 on `fa82736`): 16/17 green; homeassistant test_05 sent a command while the entity was still unavailable
  (test race) -> waits with `wait_online` now; HA suite 7/7 locally.
- 7b-1 (Opus agent, `wip/qa-fixes`, merged): N-1 (High, hotspot-only onboarding dead end), N-2/N-3 (trial consistency), N-4 (restore
  before hifi-netd), N-5/N-10 (OTA stuck states), N-7 (wpa control socket shared by two threads), N-9, N-11 (DE/EN texts for every
  raisable error code, enforced by a test), N-12 (no neighbour SSIDs in diagnostics). Unit 890, net 16 + live, OTA 12, static 0, entry 90,
  browser 11. DECISIONS #105-#110, OPEN_QUESTIONS #78.
- Also: OS networkd DUID from the MAC (OPEN_QUESTIONS #77), OTA backup BrokenPipe race (`f365404`).
- 7b-2 (Opus agent, `wip/qa-os`, merged; interrupted once by a rate limit, WIP saved and resumed): N-6 two-copy U-Boot environment
  (proved with real RAUC 1.13 + libubootenv, torn newest copy -> previous valid state), N-8 discovery on fixed port 3484 + resolved
  LLMNR/mDNS off (live spoof probes dropped), N-13 growdata, N-14 root-owned boot FAT. rpi3_64 rebuilt incrementally (283 s):
  image check 0 FAIL / 103 PASS, secret scan 0, OTA check pass; rpi4_64/rpizero2w_64 evidence predates the fixes. Unit 895, OS 162,
  net 17 + live, OTA 13, static 0, entry 90. DECISIONS #111-#115, OPEN_QUESTIONS #79-#82.
- CI on `0d3b8f2` (run 44692532202): 16/17; security failed on Bandit B104 for the new discovery bind -> justified nosec (`0657a0a`).
- 7b-3 (Sonnet agent, `wip/qa-cov`, merged): coverage of bridge/btbridge 94.9 -> 98.1 % lines, 91.8 -> 95.9 % branches (unit 1063);
  real bug found: login back-off overflow (`94504e0`); PipeWire flakes root-caused (pulse runtime-dir race 9/150 -> 0/300; pipewire-pulse
  1.4.2 SIGSEGV on unload-module); local mDNS suite 18/18 (multicast dropped on internal Docker networks; own non-internal network without
  host address); demo shows Network and Software update against SIMULATED back-ends. DECISIONS #116-#119, OPEN_QUESTIONS #83-#85.
