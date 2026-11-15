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
