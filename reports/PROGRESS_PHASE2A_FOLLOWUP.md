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
