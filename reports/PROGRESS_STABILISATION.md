# Progress log — software stabilisation and phase-2b preparation

Labels: UNIT, INTEGRATION, SIMULATED, STATIC, REAL-HARDWARE (none here). Newest entries at the bottom.

## 2026-11-16
- Docker (read-only check): data filesystem 196 G, 85 G used, 102 G free; images 53 GB (82), build cache 8.9 GB; 4 CPUs, 22.9 GiB RAM.
  Baseline: reports/evidence/docker_baseline_stabilisation.txt.
- All 7 project images rebuilt from scratch (scripts/build_images.sh: --pull --no-cache; sizes + base digests in reports/evidence/images.txt);
  new btbridge-static image (systemd-analyze, shellcheck) so static checks need no apt at check time.
- Removing my own superseded images (by ID) was denied by the permission system - not worked around. Owner-run helper:
  scripts/cleanup_own_images.sh (dry run by default; finds only dangling images built from this project's Dockerfiles + the duplicate
  HA "stable" tag). Space is not a blocker (102 GB free).
- Baseline full run with fresh images (reports/evidence/full_run_stabilisation_baseline.log): static 0 (systemd-analyze verify OK,
  exposure 1.9), unit 221, integration 54, outages 0/0, PipeWire trixie 10, hwkit 32, mDNS 7/7, HA 7/7, browser 5; PipeWire bookworm
  1 FAILED (first capture = only silence).
- Root cause analysis: NOT the rebuild (old image fails 1 in 3 too) - the SIMULATED node (module-pipe-source on PipeWire 0.3.65)
  sometimes delivers only silence for a whole session. Two harness fixes: a writer stays attached to the FIFO from before the module
  loads; readiness check through the bridge's own capture supervisor with up to 3 module reloads (counted in the results).
  6/6 bookworm runs then green (1 needed a reload), trixie 2/2 green. Product capture path unchanged; target PipeWire is 1.2.8.
- Buildroot research (sparse clone of 2026.02.x LTS, commit 679b9ea): versions/licences of all needed packages; key finding - no
  `parec` without the full pulseaudio package, so the appliance needs `pw-cat --raw`; Python 3.14 on the image. docs/PHASE2B_PLAN.md.
- DECISIONS #48-#50 recorded (HSTS off by default, Avahi preferred, OpenSSL allowed); spec updated. HSTS regression test: no
  Strict-Transport-Security header over HTTP, HTTPS/local CA or own certificate, plus a static guard.
- Capture tool chosen automatically (pw-cat --raw > parec > pw-cat 0.3); factory uses it with a presence check for parec.
  Lab: parec on 0.3.65, pw-cat --raw on 1.4.2 - both bit-exact, no fallback (INTEGRATION).
- New fault test: pipewire-pulse restart while capturing, microphone returns first as default -> never microphone frames (both versions);
  resume asserted on 1.4.2, recorded (not asserted) on 0.3.65 because of the stand-in. New open question #34 (stuck digital silence).
- mDNS: Avahi backend (`net/avahi.py`, D-Bus via jeepney) as production default (`mdns_backend=auto`), built-in responder as fallback;
  real-Avahi interop 13/13 (built-in 7 + Avahi backend 6 incl. collision -> `hifi-bridge-2.local` and goodbye). Two own bugs found and fixed
  on the way (local name collision with the daemon's own host name; a false PASS of the test script with an empty publisher IP).
- Local recovery (`btbridge.recovery`): owner-password reset, factory reset (only the bridge's own files), boot-partition request file,
  non-secret `hifi-status.txt` with the CA fingerprint (out-of-band trust reference until the flash tool exists).
- WebUI: web-app manifest + icons (home-screen shortcut), 2-hour CPU/temperature/memory trend graphs (inline SVG, CSP-clean, aria-label
  summaries), logging settings page.
- Optional secure remote syslog: RFC 5425 over TLS with mandatory verification; INTEGRATION with a real rsyslog 8.2504 (gnutls): delivery,
  RFC 5424 fields, redaction, wrong CA -> nothing delivered. Found and fixed: missing TLS close_notify on stop.
- Guest flow end to end (INTEGRATION, SIMULATED phone, real LMS): refused outside the pairing window, pairs inside, Play -> audible
  capture at the player, Pause -> stop, leave -> released; only the bridge's own commands at LMS.
- Python 3.14 (Buildroot's version): unit suite 250/250 after one test fix (the test assumed pytest is not PID 1).
- Coverage (coverage.py, line+branch) measured; appliance entry point, adapter interface conformance and access reconfiguration now tested.
- bandit 1.8.6 gate: 0 HIGH; the medium findings fixed (LMS URL scheme check; explicit checks instead of asserts) or justified inline
  (bind on all interfaces is required for a LAN appliance).
- Hardware plan: new test set H (name resolution, trusted HTTPS, home-screen shortcut, screen reader) incl. catalogue and guided procedures.
