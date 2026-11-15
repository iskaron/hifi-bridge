# HANDOFF_FOR_CHATGPT — Phase 2a + hardware-validation preparation

Previous handoffs are in git history: phase 1 is `reports/HANDOFF_PHASE1_AUDIO_SPIKE.md`; phase 1.5 is this file at commit `51a1e85`.
Running log of this package: `reports/PROGRESS_PHASE2A.md`.

## 1. Status and requested review
- **Package:** Phase 2a (software product layers) + preparation of hardware validation. **Date:** 2026-11-15. **Status: COMPLETE** for every task in the
  package that can be done without hardware. The **hardware gate is still open:** nothing has run on real Bluetooth, phones, Squeezeboxes or a Pi.
- **Summary:** The bridge is now an owner-configurable appliance in software.
  - It has persistent settings, owner authentication with a one-time setup code, a setup wizard, pairing management, and a German/English
    "Hi-Fi & elegant" WebUI in light and dark.
  - It also has diagnostics, system monitoring, and MQTT with Home Assistant discovery.
  - A hardware validation kit is ready for the Pi.
  - The PipeWire capture question was re-investigated against real PipeWire, which uncovered and fixed a **privacy defect** (fallback capture of a microphone).
  - Everything runs and is tested in Docker.
- **Decisions requested:**
  1. Accept phase 2a as a software result. Next: run the hardware validation sprint with `hwkit/` (§7).
  2. Product-name trademark question: may the UI and product say "Bluetooth"? (OPEN_QUESTIONS #19)
  3. HTTPS strategy for the WebUI on the appliance (#20; affects phase 2b and the flash tool).
  4. Keep MQTT commands (pairing, bridge audio, test tone) enabled by default, or make MQTT read-only by default? (#22)
  5. Is a Docker test against a real Home Assistant instance wanted (#23), or is a real broker plus the documented schema enough until hardware tests?

## 2. Guest experience outcome
- **Real guest path (real phone pairs, connects, plays): NOT TESTED** (no hardware, by owner decision). Plan: `docs/HARDWARE_VALIDATION_PLAN.md` test G1.
- **Software path:** the owner completes the wizard in a real Chromium browser:
  - setup code → password;
  - Bluetooth name;
  - Lyrion server found by real UDP discovery;
  - player chosen from the real LMS;
  - test tone measured at the real Squeezelite output.

  The simulated phone then connects and plays, and the dashboard switches to "Playing" with title and artist **live, without a reload**. The 5-minute
  pairing window with its countdown is one tap. Guests never touch Lyrion, Home Assistant or the WebUI.

## 3. Implementation / architecture
**Three evidence classes are kept apart throughout:**
- **(A) software verified:** real LMS, Squeezelite, Mosquitto, PipeWire tools, FFmpeg codecs, Chromium.
- **(B) simulated integration:** simulated phone, hand-written fake BlueZ, simulated PipeWire node.
- **(C) pending hardware validation.**

| Area | What exists | Evidence class |
|---|---|---|
| Persistent configuration `config/` | Typed schema (validation, all-or-nothing updates, unknown keys rejected); atomic fsync+rename writes; corrupt file moved aside; secrets separate in a 0600 file. | A (unit) |
| Service `service.py` | Composes runtime, config, auth, monitor and MQTT. Settings apply **in place**: LMS server, target, format and token change without dropping the phone, and only our own URL is released. An `UnconfiguredLms` guard means no LMS command is possible before a server **and** a target are set. Status headline; redacted diagnostics bundle. | A (unit with simulated phone + fake LMS) + A/B (integration with real LMS) |
| Auth `webui/auth.py` | Per-device setup code (hash only, deleted after use); PBKDF2-SHA256; in-memory sessions; CSRF token; per-client back-off; password change ends all sessions; console-only recovery. | A |
| WebUI `webui/` | Stdlib server, server-rendered pages (work without JavaScript) plus small JS for live status, countdown and confirmations. Pages: wizard (5 steps), login, status, pairing (window, paired phones, names, removal), settings, diagnostics (test tone, LMS check, states, faults, logs, download), system. DE/EN (264 keys, completeness tests); light/dark/auto; own logo (not the Bluetooth mark). Strict CSP, `SameSite=Strict`, origin checks. | A: HTTP tests + **real Chromium** + axe-core |
| Pairing management | List, remove (`Adapter1.RemoveDevice`), owner labels, Bluetooth name (`Alias`), trust on pairing inside the window. | B (scripted + fake BlueZ over D-Bus); C for real BlueZ |
| System monitoring `system/` | CPU, load, memory, temperature with ok/warning/critical, storage, uptime, network and Wi-Fi signal from procfs/sysfs (paths injectable). Thermal faults persisted. | A with fake procfs trees; C for Pi values and throttling |
| MQTT `mqtt/` | Stdlib MQTT 3.1.1 client (last will, retain, keepalive, reconnect back-off, TLS, auth). Home Assistant discovery for 14 entities. Commands: pairing, bridge audio, test tone. Telemetry every 5 min, immediately on thermal changes. | A: **real Eclipse Mosquitto 2.0.20**; no real HA import |
| PipeWire capture | Default `parec --raw` with `node.dont-fallback`/`dont-reconnect`, a no-data watchdog and an optional presence check. | A for the tools (real PipeWire 0.3.65 and 1.4.2); B for the node (pipe-source named like a BlueZ node); C for real BlueZ nodes |
| Hardware validation kit `hwkit/` | Runbook. Test track that survives SBC/AAC (index blocks, tone pairs, checksum). Analyser (drops, repeats, gaps, latency). D-Bus trace recorder with MAC pseudonyms. Replay through the bridge's real translator with segment expectations. PCM capture. Structured results (32 test IDs mirrored from the plan; only REAL-HARDWARE accepted). CLI and bundle script. | A for codec robustness (**real FFmpeg SBC/AAC**); B for traces; the kit itself is ready, but its purpose is C |
| Entry point | `python -m btbridge.appliance --data DIR --source bluez|sim|none`. `bluez` exits cleanly when D-Bus is unavailable. | A for the negative path; C for real use |

**Decisions recorded:** `docs/DECISIONS.md` #22–#25 from your review, and #26–#33 as reversible implementation decisions. Git identity: all 24 commits are
`Oliver Meyer <olm@cativa.net>` (checked; no rewrite needed). **Git:** branch `develop`, 14 commits in this package (`c74e734`..`HEAD`, incl. this handoff), working
tree clean, no remote, tag, push or release. **New docs:** `docs/SECURITY.md`, `docs/THIRD_PARTY.md`, `docs/PIPEWIRE_CAPTURE.md`, `hwkit/README.md`.
**Updated:** ARCHITECTURE, ACCEPTANCE (phase 2a status table), OPEN_QUESTIONS (#19–#26), FINDINGS, HARDWARE_VALIDATION_PLAN (IDs C1–C3, D1–D3, G1), README.
**Size:** about 2.5k new product lines, 0.2k CSS/JS, 0.7k kit, 1.8k new tests (118 files changed, +7.8k lines).
**Dependencies:** the product runtime stays stdlib only, plus optional `jeepney`. Test-only additions (Mosquitto, FFmpeg, Playwright/Chromium, axe-core)
are listed in `docs/THIRD_PARTY.md`.
**Not started (out of scope):** Buildroot, A/B OTA, flash tool, Wi-Fi/hotspot/mDNS/TLS provisioning, CI service wiring.

## 4. Tests and environment
Docker 29.1.3 / Compose 2.40.3 on an x86-64 host; images: Debian 12 lab (Python 3.11.2), LMS 9.1.2, Squeezelite 1.9.9-1414, Mosquitto 2.0.20,
PipeWire 0.3.65/WirePlumber 0.4.13 (bookworm) and PipeWire 1.4.2/WirePlumber 0.5.8 (trixie), FFmpeg 5.1, Playwright 1.49.1 (Chromium),
axe-playwright-python 0.1.4. All lab networks are `internal`.

Clean-slate full run (`scripts/lab_down.sh; LOG=reports/evidence/full_run_phase2a.log scripts/run_all.sh`, started 2026-11-15T19:47Z), real output:
```
UNIT          171 passed in 29.96s                     (phase 1.5: 88)
INTEGRATION   54 passed in 943.07s (0:15:43)           (phase 1.5: 47; +9 D-Bus/device mgmt, +4 Mosquitto, +2 WebUI x real LMS)
OUTAGE lms    scenario exit code: 0
OUTAGE player scenario exit code: 0
PIPEWIRE      bookworm 9 passed, 1 skipped (pw-cat has no --raw in 0.3.65) | trixie 10 passed
HWKIT         26 passed in 20.21s
BROWSER       1 passed in 13.26s (complete owner journey, see below)
exit codes: unit=0 integration=0 outage_lms=0 outage_player=0 pipewire=0 hwkit=0 browser=0
```

Test quality, beyond green counts:
- **Mutation checks**, all killed:
  - 5/5 WebUI security mutants (CSRF, Origin, `HttpOnly`, escaping, open redirect);
  - 2/2 MQTT mutants (command ignored, last will missing);
  - 5/5 BlueZ translator mutants (phase 1.5);
  - 8/8 ownership mutants (phase 1).
- **Real-browser test:** checks at 390 px and 1280 px, light and dark:
  - no horizontal overflow;
  - live update without reload (verified with a page marker);
  - the countdown ticks;
  - the German switch works;
  - no console or CSP errors;
  - **axe-core: 0 violations of any severity on 11 pages.**
- **WebUI × real LMS:** the test tone arrives audibly at the real Squeezelite output, and the wizard never changes sync groups. Pause, audio toggle,
  test tone, target change and settings changes never touch a foreign "radio" stream.
- **Codec robustness:** the delivered phone tracks (AAC .m4a) decode cleanly, and the 15 s silence of the quiet-passage track is reported exactly.

Reproduce:
```bash
scripts/run_all.sh
scripts/run_unit.sh
scripts/run_integration.sh
scripts/run_pipewire.sh
scripts/run_hwkit.sh
scripts/run_browser.sh
scripts/hwkit_bundle.sh
```

## 5. Timing and quality measurements
- **Re-measured in the same run** (virtual DAC, x86 Docker; **not acoustic, not Pi**):

  | Metric | Result |
  |---|---|
  | Play → sound, WAV | median 1.54 s |
  | Play → sound, FLAC | median 1.90 s |
  | Pause → silence, `drop_restart` | 0.03–0.05 s |
  | Pause → silence, `keepalive_silence` | 1.61 s |
  | Takeover | 2.03 s |
  | Grace release | 30.06 s |

  These are consistent with phases 1 and 1.5.
- **PipeWire capture:** bit-exact over 5 s at 44.1 kHz on both versions, provided the graph allows 44.1 kHz.
- **Test-track robustness:** decodes through SBC (328k, 229k), AAC (256k, 128k) and a 48 kHz path with 0 drops and 0 repeats.
- **Not measured, hardware unavailable:** acoustic latency, real-phone timing, Pi CPU/RAM/temperature, Wi-Fi/Bluetooth coexistence, PBKDF2 cost on a Pi Zero 2 W.

## 6. Risks, defects, unverified claims
- **High (unverified, hardware):** the whole real Bluetooth side:
  - BlueZ/agent behaviour with iOS/Android;
  - AVRCP versus notifications;
  - absolute volume;
  - the real PipeWire node name and behaviour of a phone acting as A2DP source;
  - suspend/resume gaps;
  - real Squeezeboxes;
  - Pi resources.

  The kit and plan for these exist; none of it has been executed.
- **Found and fixed in this package:**
  - **(privacy)** a missing PipeWire target silently records the default source (for example a microphone) with both `parec` and `pw-cat`;
  - **(blocking UI bug)** every form POST failed in real browsers (`Origin: null` under `Referrer-Policy: no-referrer`);
  - the wizard's test-tone button was blocked;
  - MAC addresses inside BlueZ object paths were not anonymised in the kit;
  - smaller analyser and replay bugs;
  - a misleading phase-1 harness exit code (it recorded `grep`'s status; the scenario's own exit lines remain valid).

  While fixing the browser bug I briefly made the CSRF check unreachable through an indentation slip. The unit tests caught it before any commit.
  This is listed so the review can judge test value.
- **Medium:**
  - WirePlumber 0.4 quits without systemd-logind (configuration needed in a Buildroot image);
  - `parec` needs pipewire-pulse;
  - `pipewire-pulse` 1.4.2 disappeared once in about 10 lab runs (not reproduced);
  - plain-HTTP WebUI until HTTPS provisioning (session-cookie sniffing on the LAN);
  - MQTT commands are trusted per broker ACL;
  - real Home Assistant import is not tested;
  - jeepney in Buildroot is unchecked;
  - "Bluetooth" in the product name is a trademark question.
- **Low:**
  - Sessions are in memory, so a restart logs the owner out.
  - No owner-action audit log.
  - No `Host` allow-list.
  - PBKDF2 at 200k iterations may make login slow on a Zero 2 W.
  - The integration suite takes about 16 minutes.
- **Honesty notes:**
  - The fake BlueZ, the simulated phone and the PipeWire pipe-source were written to documentation, not observed behaviour.
  - Accessibility was checked by automation plus structural checks; there was no manual screen-reader test and no test on real phone browsers.
  - The "Hi-Fi & elegant" look was judged by me from screenshots (six curated ones in `reports/screenshots/`); it needs the owner's eye.

## 7. Recommendation for the next large work package
1. **Hardware validation sprint, run by the owner, small and decisive.**
   - Set up one Pi 3 B+ on Ethernet with Raspberry Pi OS Lite, one Android phone, one iPhone and a Squeezelite or Squeezebox player.
   - With `scripts/hwkit_bundle.sh` and `hwkit/README.md`, run A1–A7, B1–B4, C1–C3 and E1–E2 first.
   - Bring back `results.json`, `env.json`, traces and captures. I then analyse them in Docker, add the real traces as regression fixtures and adjust
     `fusion`, the capture node name and volume.
2. **In parallel, Phase 2b-preparation that needs no hardware decisions:**
   - process supervision and service units;
   - an mDNS announcement;
   - a proposal for TLS provisioning;
   - CI wiring of `run_unit` plus selected suites;
   - optionally the Home Assistant Docker test.
3. **Buildroot, A/B OTA and the flash tool only after the sprint** has fixed the supported boards and the audio stack (BlueZ/PipeWire versions, pipewire-pulse
   yes or no, logind yes or no).

Starting Buildroot before the sprint would mean building images around an audio stack that is still unverified.

## 8. Operator handoff (copy/paste for ChatGPT)
> Phase 2a of Bluetooth Audio Bridge is complete as a hardware-free software package (Docker only; no Bluetooth, phone, Squeezebox or Pi has been
> tested). New: persistent configuration (validated, atomic, 0600 secrets); owner authentication with a per-device setup code; a setup wizard;
> pairing management; a German/English, light/dark, "Hi-Fi & elegant" WebUI; diagnostics; system monitoring; and MQTT with Home Assistant discovery.
> Settings apply in place without dropping the phone, and no LMS command is possible before setup. The MQTT client is stdlib, tested against a real
> Mosquitto. A hardware validation kit (hwkit/) is ready: an SBC/AAC-robust test track, trace recorder, replay through the real bridge logic, PCM
> capture, and structured results that only accept REAL-HARDWARE. The PipeWire capture test was redone against real PipeWire 0.3.65 and 1.4.2:
> parec --raw is bit-exact, and a privacy defect was found and fixed (a missing node silently captured the default source, such as a microphone).
> Real-browser tests (Chromium) found and fixed a blocking bug (all form POSTs rejected because of Origin: null), and axe-core reports 0 violations.
> Clean run: 171 unit + 54 integration + 2 outage scenarios + 19 PipeWire + 26 kit + 1 browser test, all passing. Git: develop branch,
> Conventional Commits, author identity verified, nothing published. Decisions needed: accept phase 2a; the "Bluetooth" trademark in the name;
> the HTTPS strategy; MQTT commands on or read-only by default; whether a real Home Assistant test is wanted. Recommendation: owner-run hardware
> sprint with hwkit (Pi 3 B+, Android, iPhone, one player) next, hardware-independent phase-2b preparation in parallel, and Buildroot/OTA only after
> the sprint. Please read reports/HANDOFF_FOR_CHATGPT.md, docs/PIPEWIRE_CAPTURE.md, docs/SECURITY.md and hwkit/README.md.
