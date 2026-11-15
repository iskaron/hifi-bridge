# Progress log — Phase 2a + hardware-validation preparation

Running log, newest entries at the bottom. Evidence labels as in docs/ACCEPTANCE.md. Nothing here is REAL-HARDWARE.

## 2026-11-15
- Started work package (DECISIONS #22-#25). Git identity check: 10/10 commits already `Oliver Meyer <olm@cativa.net>` (author and committer) -> no rewrite.
- PipeWire capture re-investigated (docs/PIPEWIRE_CAPTURE.md, INTEGRATION real PipeWire 0.3.65 + 1.4.2 tools, SIMULATED node):
  root cause of the earlier failure = WirePlumber 0.4.13 quits without logind; `parec --raw` is bit-exact on both versions when the
  graph allows 44.1 kHz; `pw-cat -` is AU (not raw) on 1.4; **privacy defect found and fixed**: a missing target node silently records
  the default source (microphone) -> bridge now always sets node.dont-fallback/dont-reconnect, default tool = parec, no-data watchdog.
- Config store (schema + atomic store + secrets 0600) with 22 unit tests.
