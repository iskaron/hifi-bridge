# Progress log — Phase 2a follow-up (HTTPS/mDNS, MQTT read-only, real Home Assistant, WebUI, supervision, CI, hwkit)

Running log, newest at the bottom. Labels: UNIT, INTEGRATION, SIMULATED, REAL-HARDWARE (none of this package is REAL-HARDWARE).

## 2026-11-15
- Decisions recorded: docs/DECISIONS.md #34-#40.
- Product name "Hi-Fi Bridge" (footer, ICY name, MQTT/HA device model); device name stays owner-chosen.
- MQTT read-only by default: separate switches mqtt_allow_pairing/_audio/_test (all off); disabled controls appear as read-only
  binary sensors, the inactive variant is removed by an empty retained discovery config; command topics subscribed only when enabled and
  re-checked per message. New `<base>/<id>/thermal` topic + "Overheating" binary sensor (device_class heat), published at once on every
  thermal state change by a 10 s temperature check that is independent of the 300 s telemetry (configurable 60-3600 s).
  Broker TLS always verifies (system CAs or owner CA PEM), no insecure option. UNIT 10 + INTEGRATION (real Mosquitto) 4.
- Local CA + server certificates (openssl 3 CLI): name-constrained CA (.local/.home.arpa/.lan/.internal + private IPs), 397-day server
  certificates, renewal (missing/expiring/names changed/CA changed), custom certificates (key match, expiry, name coverage), Host policy
  against DNS rebinding. 21 tests incl. a real TLS handshake with hostname verification and proof that a forged certificate for a public
  name signed with the CA key FAILS verification ("permitted subtree violation").
- Stdlib mDNS/DNS-SD responder: probing + conflict resolution, A/PTR/SRV/TXT, legacy unicast, goodbye; joins the group per interface
  (joining via 0.0.0.0 fails with ENODEV on networks without a default route - found in the lab). UNIT/loopback 5 tests +
  INTEGRATION against a REAL Avahi 0.8 peer (scripts/test_mdns.sh): 7/7 PASS (resolve, browse, conflict -> hifi-bridge-2.local,
  goodbye). Bug found: SIGTERM was not handled, so no goodbye on service stop -> fixed.
- Environment finding: Docker on this host cannot signal containers ("unable to signal init: permission denied"); docker stop ends in
  SIGKILL. Tests request clean stops inside the container.
- LocalAccess (names, Host policy, certificates, renewal hourly + on address change, mDNS restart on rename) wired into the service and
  the appliance entry point (--https-port, --no-mdns). WebUI: separate HTTP/HTTPS listeners, 421 for foreign Host names before any
  processing, HTTPS-only (308) with lock-out protection (can only be enabled from an HTTPS session), bootstrap paths (/trust, /ca.crt,
  /ca.der) stay on HTTP, X-Forwarded-* only from trusted proxies, per-request Secure cookies, /healthz (details only for localhost).
  Settings split into an overview + 7 section pages; Secure-access page (status, names, TLS mode, proxies, own certificate, renew);
  public trust page with fingerprint, CA download (PEM/DER) and per-platform steps (DE/EN). Header shows product "Hi-Fi Bridge" and the
  device name separately. Error flashes are role=alert and receive focus. Balanced desktop grids.
  Tests: 10 new (real TLS with verification). Bugs found: TLS-mode switch did not reload the certificate (fixed); a silent no-op text
  replacement in my own tooling had dropped health() - caught by the tests; edits are now asserted.
- Supervision: sd_notify (READY/STATUS/WATCHDOG/STOPPING, stdlib), watchdog pings only while health() != failed (heartbeats of control
  loop and service watch, feeder/stream threads), crash-recovery journal (released URL-addressed on the next start; retried while LMS is
  unreachable), systemd unit (Type=notify, WatchdogSec=30, Restart=always, hardening) verified with REAL systemd-analyze verify (rc 0,
  exposure 2.1 OK), respawn wrapper with back-off for non-systemd inits (shellcheck clean). 9 tests incl. real unix notify socket and
  real processes.
- REAL Home Assistant 2026.11.1 (pinned; digest sha256:6785d1ec...) + REAL Mosquitto (authenticated listener): onboarding and the MQTT
  config flow fully via REST; 7/7 tests PASS (scripts/run_homeassistant.sh): read-only default (14 entities, exact types, no switch/button),
  one device with model "Hi-Fi Bridge", units/device classes, availability on clean stop AND via last will (HA shows "unavailable"),
  automatic reconnect, HA restart restores entities and retained states, enabling one control swaps the read-only sensor for a switch
  and removes the stale entity (and back), HA switch/button really drive pairing/playback/test tone, a non-enabled command published by
  another client is ignored, periodic telemetry reaches HA, over-temperature visible in HA 0.5 s after the file change with telemetry
  set to 3600 s (bound: 10 s check interval). No MQTT warnings in the HA log. Test-isolation lessons: retained discovery of earlier lab
  instances leaks into HA (fresh broker per run; entities matched by device identifier).
- CI: .github/workflows/ci.yml (9 jobs calling the same Docker scripts; never run on GitHub - repo unpublished), validated with actionlint
  1.7.7 (rc 0). scripts/run_static.sh: shellcheck (findings fixed), Python compile check, systemd-analyze verify, actionlint.
- ENVIRONMENT BLOCKER found: Docker's data filesystem (/dockerfs/daemon-data, 196 GB) is 100 % full. apt inside new containers fails
  (first visible as "invalid signature" because downloads are truncated). Existing images keep working. Deleting my own dangling
  images was denied by the permission system; owner notified. Not worked around.
- hwkit completed as far as possible without hardware: `preflight` (read-only checks incl. the PipeWire allowed-rates and WirePlumber
  logind findings, each with a fix), `guide` (step-by-step session through all 32 catalogue tests with procedures, automatic
  env/trace/nodes/capture, verdict recording), `latency` (E2: AVRCP edge -> sound at the mic on one clock; states what is NOT included),
  pi-setup/ (PipeWire/WirePlumber/BlueZ snippets + setup script that refuses unless confirmed AND on a Raspberry Pi), 
  docs/HARDWARE_TEST_PROCEDURE.md (day plan, order, stop rules, bring-back list). +6 tests (32 total in tests/hwkit).
