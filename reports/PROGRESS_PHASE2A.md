# Progress log — Phase 2a + hardware-validation preparation

Running log, newest entries at the bottom. Evidence labels as in docs/ACCEPTANCE.md. Nothing here is REAL-HARDWARE.

## 2026-11-15
- Started work package (DECISIONS #22-#25). Git identity check: 10/10 commits already `Oliver Meyer <olm@cativa.net>` (author and committer) -> no rewrite.
- PipeWire capture re-investigated (docs/PIPEWIRE_CAPTURE.md, INTEGRATION real PipeWire 0.3.65 + 1.4.2 tools, SIMULATED node):
  root cause of the earlier failure = WirePlumber 0.4.13 quits without logind; `parec --raw` is bit-exact on both versions when the
  graph allows 44.1 kHz; `pw-cat -` is AU (not raw) on 1.4; **privacy defect found and fixed**: a missing target node silently records
  the default source (microphone) -> bridge now always sets node.dont-fallback/dont-reconnect, default tool = parec, no-data watchdog.
- Config store (schema + atomic store + secrets 0600) with 22 unit tests.
- Hardware validation kit `hwkit/` (runbook hwkit/README.md): SBC/AAC-robust indexed test track (tone pairs, checksum), analyser
  (drops/repeats/gaps/latency), D-Bus trace recorder with MAC pseudonymisation, replay through the real translator with segment expectations,
  PCM capture via the bridge's PipeWireCapture, structured results with a catalogue mirrored from the plan (32 test IDs), CLI, bundle script.
  Self-tests: 26 (real FFmpeg SBC 328k/229k + AAC 256k/128k + 48 kHz path pass) + capture through real PipeWire on both versions.
  Bugs found by the kit's own tests: MAC addresses inside object paths were NOT anonymised (\b after "dev_"), replay clock origin, partial
  first block counted twice. All fixed with regression tests.
- Auth module (setup code, PBKDF2, sessions, CSRF, rate limit; 13 unit tests). LMS read-only player/sync-group listing. Device management
  (paired list/remove/alias/trust) on BlueZ adapter + simulator, D-Bus tested against the fake BlueZ.
- Static power-off guard was a substring check -> false positive on reading the power state; replaced by a call-pattern guard with its own test.
- ApplianceService (composition; in-place reconfiguration of LMS server/target/format/token without dropping the phone;
  UnconfiguredLms guard; status headline; diagnostics bundle without secrets), SystemMonitor (procfs/sysfs, injectable paths), 13 unit tests.
- MQTT: stdlib 3.1.1 client (LWT, retain, keepalive, reconnect backoff, TLS, auth) + HA discovery bridge (14 entities, commands pairing/audio/test).
  UNIT 8 + INTEGRATION 4 against REAL Eclipse Mosquitto 2.0.20 (auth listener, retained, last will on abrupt loss via TCP proxy,
  reconnect, end-to-end with real LMS/Squeezelite incl. "MQTT test button never touches a foreign source"). 2/2 mutants killed.
  NOT verified: import into a real Home Assistant instance.
- WebUI (stdlib server, server-rendered + progressive JS): setup wizard (setup code -> password -> Bluetooth name -> Lyrion discovery/manual ->
  player/group -> test sound), login/logout, status dashboard, pairing page (window, paired phones, labels, removal), settings
  (Lyrion, target, playback, Bluetooth, MQTT, appearance, password, stream token), diagnostics (test sound, LMS check, states, faults,
  logs, redacted bundle download), system page. DE/EN (263 keys, completeness tests), light/dark/auto, "Hi-Fi & elegant" CSS, own logo
  (not the Bluetooth trademark). Security: CSRF + Origin check, SameSite=Strict HttpOnly cookies, strict CSP, body limit, no open redirect.
  16 HTTP-level unit tests; 5/5 security mutants killed.
- WebUI e2e against REAL LMS (test_12): wizard with real UDP discovery, real players + an existing sync group as targets, test sound measured
  at the real Squeezelite output, owner actions never touch foreign music. Found a real bug: wizard's test-sound button was blocked by the
  wizard guard (fixed + regression test).
- REAL BROWSER tests (Chromium/Playwright 1.49.1 in Docker, tests/browser): full owner journey at 390 px and 1280 px, light/dark, DE switch,
  live updates without reload, pairing countdown, no horizontal overflow, no console/CSP errors, axe-core WCAG audit: 0 violations on 11 pages.
  Found 3 real bugs invisible to HTTP tests: (1) every form POST rejected because Referrer-Policy no-referrer makes Chromium send
  "Origin: null" -> fixed (same-origin policy + Sec-Fetch-Site rule, tests); (2) CSP violation on plain-text error pages; (3) COOP header
  console error on HTTP. While fixing (1) I briefly broke indentation so the CSRF check became unreachable - caught by the unit tests before
  any commit. Visual review of screenshots led to: wrapping nav on phones, non-stretched cards, consistent buttons, early CPU sample.
- Clean-slate full run 2026-11-15T19:47Z (reports/evidence/full_run_phase2a.log): unit 171, integration 54, outage lms/player exit 0,
  PipeWire 9+1skip / 10, hwkit 26, browser 1 - all exit codes 0. Phase-1 numbers re-measured consistent (start 1.54 s WAV / 1.90 s FLAC,
  pause 0.03-0.05 s drop_restart, takeover 2.03 s, grace 30.06 s).
- hwkit bundle built (dist/, git-ignored): 96 KB code + phone tracks; the delivered AAC tracks decode clean through the analyser.
- Work package complete; handoff written. Stopped before phase 2b (Buildroot/OTA/flash tool).
