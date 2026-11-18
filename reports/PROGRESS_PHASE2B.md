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
