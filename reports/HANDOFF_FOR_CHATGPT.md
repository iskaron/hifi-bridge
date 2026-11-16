# HANDOFF_FOR_CHATGPT — Software stabilisation and phase-2b preparation (2026-11-16)

Previous handoff (phase-2a follow-up) is in git history at commit `82f7860`. Running log: `reports/PROGRESS_STABILISATION.md`.
Decisions: `docs/DECISIONS.md` #48–#50 are new. Plan: `docs/PHASE2B_PLAN.md`.

## 1. Status and decisions requested
**Status: COMPLETE for every hardware-independent task of this package. Stopped before the hardware gate. Phase 2b has NOT been started:**
- no Buildroot image;
- no OTA installer;
- no flash program.

Nothing has run on real Bluetooth, phones, Squeezeboxes or a Pi. All evidence is UNIT, INTEGRATION (real Docker services), SIMULATED
(phone/BlueZ) or STATIC. There is no REAL-HARDWARE evidence.

Decisions requested from the owner:
1. Approve the **hardware test day** (`docs/HARDWARE_TEST_PROCEDURE.md`, §5 below).
2. Confirm or change the phase-2b proposals **P1–P9** (`docs/PHASE2B_PLAN.md` §2), especially:
   - P3: RAUC with U-Boot boot-count on all three boards;
   - P7: the CA is created on the device, and only its fingerprint is written to the boot partition;
   - P9: the flash tool is built with PySide6 (alternative: Tauri).
3. Optional: run `scripts/cleanup_own_images.sh --apply`. It removes 16 superseded images of this project (about 6.0 GB); a dry run lists them. My
   own attempts to delete images were denied by the permission system, and I did not work around that.

## 2. Changes and commits
16 commits on `develop` (`82f7860..a8fe076`), all with the identity `Oliver Meyer <olm@cativa.net>`. 119 files changed (+3284/−289). There was
no push, tag, release or history rewrite.

| Commit | What |
|---|---|
| 255cfab | Reproducible image build (`scripts/build_images.sh`, `--pull --no-cache`, base digests); static-check image; owner-run cleanup script |
| 943b4f1 | PipeWire 0.3.65 lab harness made deterministic (root cause: the simulated node, not the product) |
| 9e599a2 | DECISIONS #48 HSTS off, #49 Avahi preferred, #50 OpenSSL allowed; Buildroot research; phase-2b plan; recovery + Avahi modules |
| 1cc0226 | Automatic capture-tool choice (`pw-cat --raw` > `parec` > `pw-cat` 0.3); PipeWire restart regression; HSTS regression |
| c588081 | Avahi mDNS backend (D-Bus) as production default with automatic fallback to the built-in responder |
| b30e149 | Web-app manifest and icons (home-screen shortcut); 2-hour CPU/temperature/memory trend graphs; boot-partition recovery wiring |
| 317656f | Optional secure remote syslog (RFC 5425 over verified TLS) with a settings page |
| 55ded26 | End-to-end guest flow with the pairing window against the real LMS |
| 59e9122 | Python 3.14 compatibility run (the Buildroot version) |
| ab7da90, e677617 | CI: py314, syslog, coverage and bandit security jobs; bandit medium findings fixed or justified |
| 62be78b | Hardware plan set H (name resolution, trusted HTTPS, home-screen shortcut, screen reader) |
| 49b3689, d3d7481, a8fe076 | Coverage tests (appliance entry point, interface conformance, access reconfiguration, Wi-Fi parsing); browser checks; evidence |
| 8bd5efb | Fix: the icon renderer was not valid Python 3.11 (found by the final full run) |

**Product changes in brief** (spec items from PRODUCT_SPEC):

*Local access (`docs/LOCAL_ACCESS_TLS.md`)*
- **HSTS** is off in every mode (DECISIONS #48).
- **mDNS** goes through the system's Avahi daemon. The fallback is the built-in responder (`mdns_backend` = auto/avahi/builtin, shown in the WebUI).
- **TLS** has no silent exceptions anywhere.

*Recovery (`python3 -m btbridge.recovery`)*
- Available only from the console or via a `hifi-recovery` file on the boot partition (physical access); never through the WebUI or MQTT.
- Covers owner-password reset and factory reset. Factory reset deletes only the bridge's own files.
- `hifi-status.txt` on the boot partition shows the CA fingerprint as an out-of-band trust reference. It contains no secrets.

*WebUI*
- Home-screen manifest and icons.
- Trend graphs: inline SVG, CSP-clean, with text summaries for screen readers.
- Logging settings page. DE/EN with 370 keys each.

*Monitoring*
- 2-hour sample history.
- Optional remote syslog: TLS only, always verified, redacted lines, bounded non-blocking queue.

*Capture*
- The capture tool is chosen automatically.
- Buildroot 2026.02 has **no `parec`** without the full PulseAudio package, so the appliance will use `pw-cat --raw` (PipeWire 1.2.8).

**Defects found and fixed in this package:**
- Avahi "local name collision" when our name equals the daemon's host name.
- `test_mdns.sh` gave a false PASS on an empty publisher IP.
- The syslog client sent no TLS `close_notify` on stop.
- The Python 3.14 run exposed a test that assumed pytest is not PID 1.
- bandit found an LMS URL with no scheme check, and `assert` used for secret-name checks, which disappears under `-O`.
- The icon script was not valid on Python 3.11.

## 3. Full test results with evidence
**Final full run** (`reports/evidence/full_run_stabilisation_final.log`, 2026-11-16T12:21Z, commit `d3d7481`, after `lab_down`, fresh images):
```
static=1 unit=0 integration=0 outage_lms=0 outage_player=0 pipewire=0 hwkit=0 mdns=0 syslog=0 py314=0 security=0 homeassistant=0 browser=0
unit 257 passed | integration 55 passed (16:08) | PipeWire bookworm 11 passed + 1 skipped, trixie 12 passed | hwkit 32 passed
mDNS vs real Avahi: built-in 7/7, Avahi backend 6/6 | syslog vs real rsyslog 2/2 | Python 3.14: 257 passed | bandit gate rc 0
Home Assistant 2026.11.1: 7/7 (no MQTT warnings in the HA log) | browser 5/5 (Chromium + WebKit, axe-core)
```
- **static=1** came from `scripts/render_icons.py`: it used f-string syntax that only Python 3.12+ accepts. Fixed in `8bd5efb`.
- Re-run at `a8fe076` (`reports/evidence/rerun_after_final_fixes.log`):
  - static rc 0 (`systemd-analyze` exposure 1.9 OK);
  - unit **258 passed**;
  - Python 3.14 **258 passed**;
  - bandit rc 0.
- The baseline before this package's work is in `reports/evidence/full_run_stabilisation_baseline.log`.

**Coverage** (`docs/TEST_COVERAGE.md`, `reports/evidence/coverage.txt`): line + branch coverage is 82 % from unit tests alone and **90 %** for unit +
integration. The gaps are listed per module with a reason; hardware-dependent paths are marked as such.

**Security scan** (`reports/evidence/security_bandit.txt`): bandit 1.8.6 reports 0 HIGH, 0 MEDIUM (after fixes or justified `nosec`), and 15 LOW
(subprocess use, try/except/pass).

**Timings** (real LMS + Squeezelite with a virtual DAC + SIMULATED phone; **not acoustic**):

| Measurement | Result |
|---|---|
| Play → sound | WAV 1.58 s / FLAC 1.92 s (median) |
| Pause → silence | 0.035 s (drop_restart) |
| Resume | 1.58 s |
| Takeover → LMS command | 2.04 s (2 s filter) |

PipeWire capture: the automatic choice gives 0 inserted silence frames on 1.4.2 (`pw-cat --raw`) and 0.3.65 (`parec`). The raw-less `pw-cat` on
0.3.65 inserts silence (23 runs), which is why it is the last resort.

**Guest flow** (`tests/integration/test_13_guest_flow.py`): the owner configures the bridge via the WebUI.
- The phone is refused while the pairing window is closed.
- With the window open, it pairs; connecting alone plays nothing.
- Play → an audible capture at the real player; pause → stop; disconnect → released.
- Only the bridge's own commands reach LMS.

[INTEGRATION + SIMULATED phone]

## 4. Missing or non-executable tests
| Not executed | Why | Where it is planned |
|---|---|---|
| Real BlueZ, phones, AVRCP, absolute volume, PipeWire BlueZ nodes | no hardware on the dev machine (by owner decision) | plan A–D, G |
| Acoustic latency, real Squeezeboxes, Pi Zero 2 W resources | hardware | plan E, F |
| `hifi-bridge.local`, CA trust and home-screen install on real phones; VoiceOver/TalkBack pass | real devices | **new** plan H1–H4 |
| systemd watchdog kill/restart on an image; Buildroot boot; RAUC A/B; power-loss rollback | phase 2b, needs images and boards | PHASE2B_PLAN steps 1–8 |
| CI on GitHub | repository not published; jobs only run locally via the same scripts (actionlint-clean) | owner decision |
| PipeWire 0.3.65 resume after a pipewire-pulse restart | the lab's stand-in node does not recover on 0.3.65, so the result is recorded, not asserted (target is 1.2.8; asserted on 1.4.2) | OPEN_QUESTIONS #34 |

## 5. Current Docker usage (read-only, `reports/evidence/docker_usage_final_stabilisation.txt`, 13:10Z)
| | At the start of the package | Now |
|---|---|---|
| Docker filesystem (`/dockerfs`, 196 G) | 85 G used, 102 G free | 71 G used, **115 G free** |
| Images | 53 GB (82) | 47.4 GB (103); project images ≈ 4.8 GB |
| Build cache | 8.9 GB | 14.2 GB |

The project images (`btbridge-*`) are:

| Image | Size |
|---|---|
| browser | 2.58 GB |
| hwkit | 630 MB |
| lab | 386 MB |
| pw:trixie | 236 MB |
| py314 | 214 MB |
| pw:bookworm | 203 MB |
| static | 164 MB |
| security | 152 MB |
| avahi | 123 MB |
| syslog | 89 MB |

- The lab was stopped with `scripts/lab_down.sh` (only our own compose project) and has 0 containers running.
- Superseded own images: 16 images, about 6.0 GB, removable with the owner-run script (§1).
- No foreign resource was touched, and no prune command was used.

## 6. Risks and open product decisions
- **Main software risk (unchanged):** whether real phones' AVRCP/MPRIS signals separate music from notifications or navigation (plan C1/C2).
- **Capture on the appliance:** depends on `pw-cat --raw` with PipeWire 1.2.8, which is verified in the lab with 1.4.2 and the simulated node only.
  - Still open: system-wide vs. per-user PipeWire, and logind (OPEN_QUESTIONS #24/#25).
  - Stuck digital silence: OPEN_QUESTIONS #34.
- **Before the CA is installed**, plain HTTP allows LAN session sniffing. The trust step depends on the owner comparing the fingerprint.
- **Phase 2b size and resources** are estimates only (about 100–150 MB per slot, ≥ 1 GB card minimum) and must be measured.
- **Licence obligations** of the image (GPL/LGPL components) need `make legal-info` in 2b. The project licence file (AGPL-3.0-only) has not been
  added yet.
- **Open owner decisions:**
  - P1–P9 (§1);
  - the final product name (DECISIONS #34 is provisional);
  - whether HSTS should become an option later;
  - publishing the repository (needed for CI on GitHub).

## 7. Hardware test day: concrete preparation
Follow `docs/HARDWARE_TEST_PROCEDURE.md`. Additions from this package:

1. **Bring:**
   - a Pi 3 B+ (reference), with power supply, a 16 GB+ microSD card and Ethernet;
   - an Android phone and an iPhone;
   - one target player (Squeezelite or a real Squeezebox);
   - a **test** LMS;
   - for E2/F3: a USB microphone;
   - for H1: a Windows or macOS laptop.
2. **Bundle:** on the dev machine, run `scripts/hwkit_bundle.sh`. Flash stock Raspberry Pi OS Lite, then run `setup_test_pi.sh --i-am-on-the-test-pi`.
3. **Preflight** (`python3 -m hwkit preflight`): it now also shows the capture tool the bridge would use there.
4. **Start the bridge:** `python3 -m btbridge.appliance --data ~/bridge-data --source bluez --boot-dir /boot/firmware`.
   - Raspberry Pi OS already runs Avahi, so the name is published through it.
   - `hifi-status.txt` shows the CA fingerprint for H2.
5. **Order:** A1–A5 → B → C1/C2 → D1/D2 → E1/E2 → F → G1 → **H1–H3** (H4 if time). Stop rules: A3/A4 fail, or B4 evicts the first phone.
6. **Bring back:** `results.json`, `env.json`, `REPORT.md` and `sessions/**`. Do not include secrets.

## 8. Recommendation for the package after hardware validation
1. Analyse the results with `hwkit analyze/replay/latency`, and turn the real traces into regression fixtures (REAL-HARDWARE).
2. Tune media fusion (C1/C2), the capture choice and the no-data watchdog (A3/A4), and the volume mapping (D1/D2). Record the verified-player list (F).
3. Decide P1–P9, then start **Phase 2b** in the order of PHASE2B_PLAN §5:
   1. Buildroot br2-external with three defconfigs;
   2. RAUC/U-Boot A/B with boot-count and power-loss tests;
   3. provisioning and hotspot with a per-device password;
   4. signed OTA with offline keys;
   5. flash tool;
   6. legal-info and size and resource measurements per board.

## 9. Operator summary (copy/paste)
> Software stabilisation and phase-2b preparation are complete without hardware. Everything ran in Docker; no real Bluetooth, phone,
> Squeezebox or Pi was tested. Phase 2b has not been started.
>
> Decisions recorded and regression-tested: HSTS off by default, Avahi preferred for mDNS (now implemented as the production backend with a
> fallback; 13/13 against a real Avahi), OpenSSL allowed (Buildroot 2026.02: libopenssl 3.6.2, Apache-2.0).
>
> New:
> - console/SD-card recovery with the CA fingerprint on the boot partition;
> - web-app manifest and icons;
> - 2-hour trend graphs;
> - optional TLS-only remote syslog (tested against a real rsyslog; a wrong CA delivers nothing);
> - automatic PipeWire capture-tool choice (Buildroot has no `parec`, so `pw-cat --raw`);
> - an end-to-end guest-flow test with the pairing window;
> - a Python 3.14 run, 90 % line+branch coverage, a bandit gate (0 high/medium) and CI jobs for all of them;
> - all lab images rebuilt reproducibly.
>
> Final full run: static (after one fix), 258 unit, 55 integration, outages, PipeWire 0.3.65/1.4.2, kit, mDNS, syslog, Home Assistant
> 2026.11.1 7/7 and browser 5/5 — all green.
>
> Docker: 115 GB free; 16 superseded own images (about 6 GB) can be removed with `scripts/cleanup_own_images.sh --apply`.
>
> Buildroot/A-B/RAUC/OTA-key/provisioning/flash-tool proposals P1–P9 are in docs/PHASE2B_PLAN.md and await approval. Next: hardware test day
> (now including set H: name resolution, trusted HTTPS, home-screen install, screen reader), then Phase 2b.
