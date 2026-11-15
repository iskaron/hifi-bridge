# HANDOFF_FOR_CHATGPT — Phase 2a follow-up package

Previous handoff (phase 2a) is in git history at commit `4cef23f`. Running log: `reports/PROGRESS_PHASE2A_FOLLOWUP.md`. Decisions: `docs/DECISIONS.md` #34–#47.

## 1. Status and decisions requested
**Status: COMPLETE for every task that can be done without hardware.** The hardware gate is still open: nothing has run on real Bluetooth,
phones, Squeezeboxes or a Pi. One **environment blocker** exists on the development host and affects the owner's other services (§6).

Decisions requested:
1. **Free space on Docker's data filesystem.** `/dockerfs/daemon-data` is 100 % full. My request to delete my own dangling images was denied.
2. **HSTS:** keep it off for now? (OPEN_QUESTIONS #28)
3. **mDNS:** use the built-in responder or avahi in the appliance image? (#30)
4. Confirm that `openssl` is part of the Buildroot package set (#29).
5. Approve the hardware test day per `docs/HARDWARE_TEST_PROCEDURE.md`.

## 2. What was delivered (evidence class in brackets)
* **Product name "Hi-Fi Bridge":** shown separately from the owner-chosen device name in the header, title, footer and HA device model. No Bluetooth logo. [UNIT/BROWSER]
* **Local access / HTTPS** (concept: `docs/LOCAL_ACCESS_TLS.md`):
  * **Name:** stable mDNS name `hifi-bridge.local` from my own stdlib responder, with conflict resolution and goodbye. [INTEGRATION vs. real Avahi 0.8, 7/7]
  * **Certificates:** a per-device local CA with critical name constraints, so a forged certificate for a public name fails verification. Server certificates are valid 397 days and renew automatically. The owner can install their own certificate, and a reverse-proxy mode with an explicit list of trusted proxies exists. [real openssl + TLS]
  * **HTTPS only:** has lock-out protection; HTTP remains only for the trust bootstrap.
  * **Trust page:** shows the fingerprint and per-platform steps (DE/EN).
  * **DNS rebinding:** Host validation answers 421 for unknown names.
  * **Browser check:** WebKit trusts the device after the documented CA install. Chromium without the CA refuses (`ERR_CERT_AUTHORITY_INVALID`). There are no TLS exceptions anywhere. [BROWSER]
* **MQTT read-only by default:**
  * Pairing, playback and test tone are each enabled separately.
  * Disabled controls appear as read-only sensors, and the inactive entity variant is removed from HA.
  * A thermal state change is published at once via a 10 s temperature check, independent of the 300 s telemetry.
  * Broker TLS always verifies.
* **Real Home Assistant 2026.11.1 + Mosquitto: 7/7 tests pass.** They cover:
  * entity types and device mapping, units and classes;
  * availability on a clean stop and via last will;
  * reconnect and HA restart;
  * stale-entity removal;
  * the read-only default and separately enabled controls;
  * telemetry;
  * over-temperature visible in HA 0.5 s after the change in this run (upper bound: the 10 s check interval).

  No MQTT warnings appeared in the HA log.
* **WebUI improvements:**
  * Settings are an overview with 7 section pages.
  * Desktop grids are balanced.
  * Error messages are `role=alert` and receive focus.
  * The **keyboard-only** setup wizard was verified in Chromium, including visible focus and the skip link.
  * The accessibility tree was checked.
  * axe-core reports 0 violations on 15 pages.
* **Supervision:**
  * A systemd notify watchdog driven by internal heartbeats, plus `/healthz`.
  * A crash-recovery journal: the next start releases exactly the bridge's own LMS item.
  * The unit file is hardened (`systemd-analyze` exposure 2.1).
  * A respawn wrapper for systems without systemd.

  [UNIT; STATIC]
* **CI:** `.github/workflows/ci.yml` has 9 jobs that call the same Docker scripts as local runs. It is actionlint-clean and has never run on GitHub (the repository is unpublished). `scripts/run_static.sh` exists as well.
* **Hardware kit:**
  * `preflight` checks the test Pi read-only, with a fix for every failure.
  * `guide` walks through all 32 tests with procedures.
  * `latency` measures E2 timing (AVRCP event to microphone).
  * `pi-setup/` contains the configuration and a guarded setup script.
  * `docs/HARDWARE_TEST_PROCEDURE.md` gives the day plan, stop rules and the bring-back list.

## 3. Tests — clean-slate full run 2026-11-15T21:34Z (`reports/evidence/full_run_phase2a_followup.log`)
```
static: shellcheck rc 0, python compile 0 errors, actionlint rc 0, systemd-analyze FAILED (apt: no space - environment; same unit rc 0 earlier)
unit 221 passed | integration 54 passed (15:37) | outage lms 0 / player 0 | PipeWire 9+1 skip / 10 | hwkit 32 | mDNS vs Avahi 7/7
Home Assistant 7/7 | browser 5/5 (Chromium + WebKit, axe 0 violations)
```
Phase-1 timings re-measured (virtual DAC, not acoustic): Play→sound WAV 1.55 s / FLAC 1.91 s, Pause→silence 0.04 s, takeover 2.03 s.

## 4. Bugs found and fixed in this package
* Changing the TLS mode did not reload the certificate.
* The mDNS responder did not handle SIGTERM, so no goodbye was sent on a service stop.
* Joining the multicast group via `0.0.0.0` fails with ENODEV on networks without a default route.
* My own tooling once silently dropped `health()`; the tests caught it, and edits are now asserted.

## 5. Not verified (hardware gate)
* Real BlueZ/phones/AVRCP/volume, PipeWire BlueZ nodes, real Squeezeboxes, a Pi, and acoustic latency.
* Real phone browsers' CA trust and a manual screen-reader pass.
* Real systemd on an image.

## 6. Risks
* **Docker data filesystem full (owner action).** Non-root containers fail to write: LMS failed with "database or disk is full", and the owner's other services are probably affected too.
  * **Mitigation for the project:** the lab LMS state now lives in `.lab/` on the host.
  * **Consequence:** fresh `apt` in images fails, so images cannot be rebuilt until space is freed (#27, #32).
* Orphaned HA entities from earlier instances stay retained at the broker (#31).
* Before the CA is installed, plain HTTP still allows LAN session sniffing.

## 7. Recommendation for the next large work package
1. The owner frees Docker space, then runs the **hardware test day** with `hwkit` (Pi 3 B+, Android, iPhone, one player).
2. Analyse the results, add the real traces as fixtures, and tune fusion, capture and volume.
3. Only then start Phase 2b: Buildroot, A/B OTA and the flash tool, which shows the CA fingerprint. Decide mDNS (own responder or avahi), `openssl` and the systemd/PipeWire layout first.

## 8. Operator summary (copy/paste)
> Follow-up to Phase 2a is complete without hardware (Docker only; no real Bluetooth, phone, Squeezebox or Pi tested). Product name "Hi-Fi
> Bridge". New: mDNS name hifi-bridge.local (tested against real Avahi), local HTTPS via a name-constrained per-device CA with renewal, own
> certificates and reverse-proxy mode, a trust page, DNS-rebinding protection and no TLS exceptions (WebKit trusts after CA install; Chromium
> without it refuses). MQTT is read-only by default, with separately enabled controls and an immediate over-temperature warning; real Home
> Assistant 2026.11.1 + Mosquitto pass 7/7. WebUI: settings sections, keyboard-only wizard verified, axe-core 0 violations. Also: supervision
> (watchdog, crash recovery, verified systemd unit), CI workflow (actionlint-clean, never run), and the hardware kit completed (preflight,
> guided session, latency, Pi setup, day plan). Full run: 221 unit, 54 integration, outages, PipeWire, kit, mDNS, Home Assistant and browser
> all green; only the systemd-analyze static step failed, because Docker's data filesystem on the dev host is 100 % full (owner action
> needed; it also broke LMS writes, which is why the lab LMS moved to the host filesystem). Next: free Docker space, run the hardware test
> day, then Phase 2b.
