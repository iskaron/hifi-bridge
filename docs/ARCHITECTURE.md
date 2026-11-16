# Working architecture and hypotheses

Proposed flow: `Phone BT A2DP -> Linux/BlueZ + selected audio framework -> audio receive / session tracking -> bounded live buffer / optional encoding -> authenticated local HTTP media endpoint -> LMS external URL playback -> Squeezebox/Squeezelite (existing group)`.

This is a **hypothesis to prove**, not a declaration of LMS/legacy hardware support. Start by testing an endless synthetic audio stream into LMS. Then test a real Bluetooth source. If LMS can only accept a different streaming mode, document evidence and propose the smallest compatible design. Do not introduce a custom LMS plugin unless standard URL-based playback has been shown insufficient and the tradeoff is recorded.

Plan explicit interfaces and modules (names provisional):

- A2DP media receiver + AVRCP/transport metadata adapter
- Playback state machine + source ownership + pause/notification rules
- Live stream transport/transcoding adapter (bounded latency/buffering)
- LMS discovery/control/status adapter; target selection, group aware
- WebUI/admin service (pairing management, setup, status, diagnostics)
- MQTT/HA adapter, optional and entirely nonblocking
- OS/hardware adapter (Buildroot services, Wi-Fi config, watchdog, A/B boot)
- Update and recovery manager

**Risk map:**

- Running LMS live HTTP stream: player buffering/timeout, format support, sync groups, fallback transcoding.
- BT sound detection: A2DP transport state vs AVRCP vs simple waveform energy; pauses, silent intros, notifications.
- Pause latency: LMS/player output buffering can continue after phone stops sending; measure actual speaker output.
- Phone volume: AVRCP Absolute Volume availability and group volume semantics vary.
- Zero 2 W: shared 2.4GHz WLAN/BT, CPU/RAM (512MB) and buffering constraints. Pi 3 B+ with Ethernet is good reference.
- HTTP local token: exposure through LMS player logs and URLs, stream URL rotation impact on current clients.
- Immutable filesystem/OTA: real board support, rollback semantics, signature validation and power failures.

The owner expects a *shipping-quality* appliance eventually, but audio feasibility is a review gate. Spend phase 1 time on real playback behavior, measurement instrumentation, emulator/dummy stream tools, and reproducible evidence, not on full OTA/UI implementation.

## Phase-1 / review status of the architecture (2026-11-15)

Implemented and tested (software only): `Source adapter interface -> MediaFusion -> LiveFeeder/PcmRing -> HTTP live endpoint -> LMS (JSON-RPC)`,
with the ownership `Controller`. Layering is deliberate so a future Docker runtime or another host service can reuse the core:

```
[phone]  --A2DP/AVRCP-->  bluetoothd  <--D-Bus--  BlueZBus (JeepneyBus | fake)  -->  BluezModel (pure) -+
   |  decoded audio                                                                                      +--> BluezAdapter --> AudioSourceAdapter callbacks
   +--> PipeWire/WirePlumber node --> PcmCapture (PipeWireCapture | test double) ----------------------+          |
                                                                                                                  v
                                   BridgeRuntime: MediaFusion, Controller (state/ownership), LiveFeeder+PcmRing, StreamServer  --> LMS --> players
```
Boundaries: core (`btbridge/core`, `stream`, `lms`) has no hardware or D-Bus dependency; `adapters/base.py` is the only contract between "source" and
"bridge"; the simulated source (`testkit`) and the BlueZ adapter implement the same contract. The BlueZ side is prepared but **not verified on hardware**
(docs/BLUEZ_ADAPTER_DESIGN.md, docs/HARDWARE_VALIDATION_PLAN.md). Still design-only: WebUI, MQTT, OS/Buildroot services, OTA (phase 2).

## Phase-2a module map (2026-11-15)

```
btbridge/
  core/ stream/ lms/          phase-1 bridge: ownership controller, live feeder/ring, HTTP stream, LMS JSON-RPC (+ read-only targets)
  adapters/bluez/             BlueZ/PipeWire adapter (hardware-free prepared): model, jeepney bus, PCM capture (parec, no fallback), device management
  config/                     settings schema + atomic store + 0600 secrets
  system/                     procfs/sysfs monitor (injectable paths)
  service.py                  ApplianceService: composes runtime + config + auth + monitor + MQTT; in-place reconfiguration; status headline
  webui/                      stdlib HTTP server, auth (setup code, PBKDF2, sessions, CSRF), views, i18n (de/en), static (CSS/JS/logo)
  mqtt/                       stdlib MQTT 3.1.1 client + Home Assistant discovery bridge
  appliance.py                entry point (service + WebUI)
hwkit/                        hardware validation kit (runs on the Pi; analysis in Docker)
testkit/                      lab-only: simulated phone, fake BlueZ, PipeWire harness, appliance_lab (simulated phone + control API)
```
Data flow for owner actions: WebUI/MQTT -> ApplianceService -> ConfigStore (validated, persisted) -> listener applies to Controller/runtime.
The WebUI and MQTT never talk to the Controller or LMS directly. Still open for phase 2b: OS integration (service supervision, Wi-Fi/hotspot,
mDNS, TLS provisioning), Buildroot image, OTA, flash tool.

## Follow-up package additions (2026-11-15)
```
btbridge/net/        hostcheck (DNS rebinding), certs (local CA, renewal, custom certs), mdns (responder), access (LocalAccess: names,
                     certificates, proxies, renewal loop), addresses
btbridge/supervision.py   sd_notify + watchdog, crash-recovery journal
packaging/           systemd unit (verified), respawn wrapper - preparation for phase 2b
```
The WebUI runs an HTTP and an HTTPS listener; `LocalAccess` decides allowed hosts and provides a reloadable TLS context. MQTT is read-only
unless controls are enabled. Concepts: docs/LOCAL_ACCESS_TLS.md, docs/SERVICE_SUPERVISION.md.

## Stabilisation package additions (2026-11-16)
`net/avahi.py` (Avahi backend, production default), `recovery.py` (local recovery), `system/syslog.py` (optional TLS syslog), automatic PipeWire
capture tool choice (`adapters/bluez/pcm.choose_capture`), trend history in the service, web-app manifest. Phase 2b proposals: docs/PHASE2B_PLAN.md.

## Phase-2b preparation additions (2026-11-16)
* Device: `provisioning.py` (format `hifi-bridge-provision/1`, shared with the flash tool), `trustproof.py` (P7 HMAC proof, shared),
  `bootfiles.py` (recovery → provisioning → CA → status, run by `hifi-bridge-boot.service`), journaled `recovery.py`, `/trust/proof`.
* `os/br2-external`: Buildroot skeleton (docs/PHASE2B_PLAN.md, os/README.md).
* `flashtool/hififlash`: flash tool core (docs/FLASH_TOOL.md). It depends on `btbridge.provisioning` and `btbridge.trustproof` only.
