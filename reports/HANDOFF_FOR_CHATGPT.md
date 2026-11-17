# HANDOFF_FOR_CHATGPT - Interactive Docker demo and public-repository preparation (2026-11-17)

## 1. Status and decisions requested
**Done (software, Docker only):** `make demo` starts a complete, usable demo on an ordinary Linux notebook - the **unchanged** bridge software
with its real BlueZ adapter, a real Lyrion server with three Squeezelite rooms, Mosquitto, optional Home Assistant, **virtual phones** and a
**browser audio monitor** that plays what the rooms output behind Lyrion and Squeezelite. The repository is prepared for public visibility
(licence, copyright, AGPL source offer, security policy, hardened CI, secret and privacy check).

**Not changed by rule:** repository visibility, GitHub settings, releases, tags, images or OTA packages; no hardware test; the hardware gate
(`docs/HARDWARE_GATE_CHECKLIST.md`) stays exactly as it was. **Nothing here proves real Bluetooth, real phones, a Raspberry Pi or A/B updates.**

**Owner decisions requested**
1. **OPEN_QUESTIONS #46 - private information in the immutable history** before going public: owner name and e-mail (commit identity, by
   design), the private GitLab domain `git.cativa.net`, one local user path in an old report, 46 commit messages with Claude session links.
   No credential (Gitleaks clean). Accept, or rewrite history yourself before the visibility change (agents must not).
2. **OPEN_QUESTIONS #47 - GitHub settings when going public:** private vulnerability reporting (SECURITY.md uses it), approval for workflows
   from outside contributors, branch protection, secret scanning with push protection.
3. **Incident to note (resolved, details in section 4):** during development the demo's setup wizard briefly **read** the server status and
   player list of a **real Lyrion server on the development host** (via the host's address on an internal Docker network). Nothing was
   played or changed. Both the demo and the test lab networks now give the host no address; a test step verifies it before any packet.
4. Minor: #48 pin the Lyrion image tag; #49 preset the demo rooms' volume (Lyrion starts players at 50).

## 2. The demo (docs/DEMO.md, German: docs/DEMO.de.md)
```bash
make demo           # build + start; prints the URLs and the one-time setup code
make demo-status    # URLs, setup code, CA fingerprint, MQTT credentials, CPU/RAM
make demo-trust     # P7 trust proof (before setup) or fingerprint comparison (after); saves .demo/hifi-bridge-ca.pem
make demo-recover   # SD-card recovery: new one-time setup code, settings kept
make demo-ha        # + Home Assistant, onboarded, with the MQTT integration
make demo-logs | demo-stop | demo-reset   # reset deletes ONLY the demo's own data, after typing RESET
```
URLs (defaults, all bound to 127.0.0.1, configurable): bridge http://127.0.0.1:18080 / https://127.0.0.1:18443, console
http://127.0.0.1:18100 (`/phones`, `/monitor`), Lyrion http://127.0.0.1:19000, Home Assistant http://127.0.0.1:18123.
Host requirements: Docker + Compose v2 + make. No host installation, no hardware access, no privileged container.

**Architecture (DECISIONS #71)**
```
virtual phones (console) --"air" API--> SIMULATED bluetoothd (fake BlueZ, private D-Bus): pairing agent, Device1, MediaTransport1, MediaPlayer1
        | A2DP stand-in (PCM, real time)          | D-Bus
        v                                         v
  PipeWire node bluez_input.<MAC> --pw-cat--> REAL bridge (--source bluez, unchanged) --HTTP stream--> REAL Lyrion --> 3x REAL Squeezelite
                                                                                                                    | virtual DAC + tap
                                                              browser audio monitor <-- console relay <-------------+
```
* The phones never talk to the bridge: they can only do what a phone can do over the air. Refusal of a second phone, the pairing window,
  owner removal, takeover rules - all decided by the product's own code.
* The flash-tool core provisions the setup code and trust key on the simulated boot partition, as for a real card.
* Isolation: compose project `hifi-demo`, one image `hifi-demo:dev` (394 MB), internal network **without host address**, only a gateway
  publishes ports (127.0.0.1). The demo pre-sets the owner setting `extra_hostnames=127.0.0.1` so HTTPS works at 127.0.0.1 with the device CA.
* Audio: generated in code (4 tracks, radio programme, notification chime; CC0 output). Users can add their own WAV/FLAC/MP3/Ogg files;
  decoded locally, never sent anywhere.

## 3. Requirements of the assignment - status
| # | Requirement | Status / evidence |
|---|---|---|
| 1 | `make demo`, `-stop`, `-status`, `-logs`, `-reset` (with confirmation); only Docker/Compose/make; no privileged containers; isolation; configurable ports on localhost | done; plus `-trust`, `-recover`, `-ha`, `-test`. Port pre-check; isolation verified by the stack test (step 0) |
| 2 | Real product software with all functions (wizard, login/HTTPS, pairing window, devices, playback, Lyrion, player choice, diagnostics/test tone, MQTT, monitoring, DE/EN, light/dark); security not weakened; setup code + TLS trust easy; persistent | done: unchanged product; setup in Chromium, pairing window, device removal, MQTT, ICY titles tested; P7 trust proof + fingerprint fallback; volumes persist across restarts |
| 3 | Virtual phones UI in the CI: search, pair, connect/disconnect, play/pause/resume, volume, track change, metadata, notifications, link loss, second phone; existing fake BlueZ; no product bypass | done (`/phones`); built on `btkit.fake_bluez`; unit-tested against the product's real `BluezAdapter` |
| 4 | Real Lyrion, Squeezelite Wohnzimmer/Schlafzimmer/Arbeitszimmer, single and synchronised playback, simulated radio, Lyrion UI on the host | done; multiroom proven by correlation in the Schlafzimmer; demo radio as Lyrion favourite and console helper |
| 5 | Audible audio: browser monitor of the players' PCM (behind Lyrion and Squeezelite), room choice, documented added latency, own/free content, no external transfer | done; AudioWorklet (secure context) with ScriptProcessor fallback; latency measured live: ~260-295 ms |
| 6 | Real Mosquitto; Home Assistant as optional profile; discovery and enable-able controls | done; random per-install broker passwords; `make demo-ha` onboards HA and adds MQTT (manual run; the HA suite of the lab tests discovery + all controls) |
| 7 | URLs in the terminal; short guide with the 8 examples; simulated devices/values marked | done: docs/DEMO.md (EN, full), docs/DEMO.de.md (DE); every console page shows SIMULATED; measurement files carry a label |
| 8 | Public-visibility preparation | done, section 6; visibility not changed |
| 9 | TDD, unit/integration/browser tests, coverage kept, resources watched, Conventional Commits, auto-push + CI monitoring, rules, hardware gate | done; section 5 |

## 4. Defects and findings in this package (all fixed)
1. **Host services reachable from "internal" Docker networks.** The host keeps an address on an internal bridge; a Lyrion server running on
   the host answered the wizard's UDP discovery, and the bridge in the **test** stack listed its players (read-only; the test stopped
   before choosing one). Fix: `com.docker.network.bridge.inhibit_ipv4` on the demo **and the lab** network; stack test step 0 aborts if
   the host has an address there. Full run after the fix: all suites green (the lab does not need host access).
2. **Product: misleading first-start hint** - after provisioning, the log still pointed to the deleted `setup-code.txt`. Now points to the
   flash tool's owner sheet (unit test).
3. **Product: no AGPL source offer** in the WebUI. Every page footer now links the repository (unit test; `btbridge.SOURCE_URL`).
4. **Test race (CI): Home Assistant switch** pressed before HA had processed the entity's availability. The bridge subscribes before it
   announces (checked); the test now waits for availability, like a user would.
5. Demo-internal (found by its own tests): concurrent saves of pairings; correlation finding a repeated bar (search window bounded); trust
   key consumed by setup (P7 before setup, fingerprint fallback after); AudioWorklet missing in insecure contexts (fallback); CSP blocking
   inline styles; a11y: unnamed select (axe "critical"); ambiguous labels; stale phone messages; `scripts/demo.sh` addressed the owner's
   project from the stack test (now `DEMO_PROJECT`, the test never touches the owner's demo).
6. Development host specifics: automatically assigned Docker ranges 172.17/16 and 172.18/16 were not reachable from this host - the demo now
   uses fixed, overridable /24 subnets. The old `scripts/demo.sh` (phase-1 lab walkthrough) was overwritten by mistake and restored as
   `scripts/lab_walkthrough.sh`.
7. **Harness: PipeWire start on a cold CI runner** - `testkit/pw/start_pipewire.sh` waited only 10 s for pipewire-pulse (0.3.65) and its
   diagnostic `tail -5` with several files was invalid, so logs never appeared. Now up to 30 s and `tail -n 8`.

## 5. Tests
**Local full run** (`scripts/run_all.sh`, log `reports/evidence/full_run_demo_package.log`, started 2026-11-17T12:53Z on `e93d9fe`; the HA
and demo suites ran with the test fix committed as `dc92515`; code-identical to `0e307d1`): **all 17 suites exit 0** - static (incl. licence-copy check), unit 296,
integration 55, outages 2, PipeWire 11+1 skip / 12, hwkit 32, mDNS 13/13, syslog 2, Python 3.14 296, bandit, Gitleaks (74 commits), OS
(3 defconfigs, licences, 46 tests), Home Assistant 7/7, **demo unit 52**, **demo stack** (below), browser.

**Demo stack test** (`scripts/test_demo.sh`, separate project `hifi-demo-test`, fresh volumes):
0 isolation (no host address, no default route) - P7 trust proof before setup (`VERIFIED`, HTTPS 200 with only the device CA) - 1 owner
setup in Chromium - 2 audio chain **11/11** - 3 console UI **8/8** - 4 fingerprint method after setup - 5 MQTT discovery (14 config topics)
- 6 SD-card recovery (new code, `/` redirects to `/setup`).
Chain tests: pairing inside the window; Play reaches the Wohnzimmer behind Lyrion and Squeezelite (**cross-correlation with the phone's
track: score 1.0**); track change; phone volume changes the room volume (50 -> 40); second phone refused, first untouched; pause/resume;
0.7 s notification does **not** take the radio over, music does; synchronised group plays the same track in the Schlafzimmer (score 1.0);
link loss stops the room; manual source change in Lyrion is respected (the bridge does not take the room back).
Console UI: overview/phones/monitor in DE light mobile and EN dark desktop with axe-core (no serious/critical), no console/CSP errors, no
overflow; phone controls; both monitor engines with measured latency; owner removes a phone in the WebUI and the phone learns it; MQTT set
in the WebUI; ICY titles appear in Lyrion after the owner enables them.

**GitHub Actions:** run 44087288559 (`e93d9fe`): 13/14 green incl. the new **demo job (4.2 min)**, homeassistant failed (finding 4);
run 44093151791 (`0e307d1`): 13/14, pipewire start failed (finding 7); **run 44097021835 (`87074ad`): 14/14 green** (section 9). Full CI: 58 job-minutes, 21.6 min wall time.

**Coverage** (product code, unit + integration): see section 9 (`reports/evidence/coverage.txt`). The demo code is a tool and is covered by
its own tests, not counted in the product figure (docs/TEST_COVERAGE.md).

## 5a. Measurements (SIMULATED phones and rooms; software timing in Docker - not acoustic, not Bluetooth)
`measurements/demo_chain.json`, `measurements/demo_monitor_latency_*.json`:
| What | Value |
|---|---|
| Play on the phone -> Lyrion plays the bridge stream | 2.1 s |
| Phone -> room DAC while playing (Lyrion + Squeezelite buffering) | 2.2 s (Wohnzimmer; grouped Schlafzimmer 2.25 s) |
| Takeover of the radio after Play (notification filter) | 2.3 s |
| Resume after pause (within the grace period) | 0.27 s |
| Link loss -> room stops | 0.02 s |
| Audio monitor, **added** latency (player DAC -> browser output) | ~260-295 ms (jitter buffer ~225-240 ms + browser-reported output ~50 ms; headless Chromium) |
| Resources while playing | ~430 MB RAM for the whole demo (bridge ~65 MB), a few % CPU |

Screenshots (`reports/screenshots/`): `demo-03-bridge-pairing-open.png`, `demo-04-phones-playing.png`, `demo-05-monitor-listening.png`,
`demo-phones-mobile-light-de.png`, `demo-overview-desktop-dark-en.png`, `demo-monitor-desktop-dark-en.png` (all screenshots of every run are
also uploaded by the CI demo job).

## 6. Publication check (docs/REPOSITORY.md section 7, DECISIONS #72)
| Item | Result |
|---|---|
| LICENSE | official AGPL-3.0 text (SHA-256 `0d96a4ff…abcb0`); `bridge/LICENSE` identical copy for Buildroot (static check); `HIFI_BRIDGE_LICENSE_FILES` set (OPEN_QUESTIONS #41 resolved) |
| Copyright / licence notice | README: "Copyright (C) 2026 Oliver Meyer and contributors", AGPL-3.0-only notice, CC0 note for generated audio |
| AGPL section 13 | WebUI footer links the repository with the version on every page |
| Third-party licences | docs/THIRD_PARTY.md now also lists every demo component |
| Secrets | Gitleaks over all 76 commits: clean. Tracked files: only lab/test values. Committed screenshots: empty forms, a disposable lab CA fingerprint. CI logs of all runs (78 files) and latest artifacts: Gitleaks clean, no token patterns. The GitHub token file is git-ignored, 0600, never in images (`.dockerignore` allowlist) |
| Private information | see decision 1 (OPEN_QUESTIONS #46) |
| Pull requests from forks | `pull_request` only, read-only token, no secrets anywhere, checkout without persisted credentials, actions pinned to SHAs + Dependabot, GitHub-hosted runners only |
| README | starts with an explicit development-status warning: no hardware validation, no image built, no releases |
| SECURITY.md | private reporting via GitHub (needs the setting, #47) |

## 7. Remaining limitations (unverified, by design of the demo)
* Simulated: phones, Bluetooth radio and codecs (no SBC/AAC, no link quality), AVRCP behaviour of real phones, the rooms' speakers, the radio.
  The demo's PipeWire node is a `module-pipe-source`, not the PipeWire BlueZ plugin.
* PipeWire 1.4.2 in the demo vs. 1.2.8 in Buildroot; Squeezelite 2.0 (trixie) in the demo vs. 1.9.9 in the lab.
* The monitor latency is measured in headless Chromium; real browsers and sound cards add their own output latency (the page shows it live).
* `make demo-ha` was exercised manually by its building blocks (onboarding + MQTT flow reuse the lab's tested HA client); the stack test
  checks MQTT discovery on the broker, the lab HA suite checks discovery and all controls in a real HA.
* No temperature in containers (system page shows CPU/RAM only). No Wi-Fi/hotspot network service exists yet (phase 2b step 3).
* Everything in the hardware gate remains open.

## 8. Docker resources (read-only checks)
148 GB free on the Docker filesystem. New images: `hifi-demo:dev` 394 MB, `ghcr.io/gitleaks/gitleaks:v8.24.3` 39 MB. 60 superseded
intermediate `hifi-demo` builds of today (identified by the Dockerfile's own configuration, unused) were removed; the image count went from
191 back to 129; no foreign image, container or volume was touched. The image now carries a label for safe identification. The owner's demo
(`hifi-demo`) is running; the test stack `hifi-demo-test` removes itself after each run.

## 9. Final CI run and coverage
* **GitHub Actions run 44097021835 on `87074ad`: 14/14 jobs green** (static, unit, integration, pipewire, hwkit, mdns, os-and-flashtool,
  security incl. Gitleaks, python314, syslog, coverage, homeassistant, browser, **demo**). The two runs before were red once each for a
  test-harness reason: the Home Assistant switch race (finding 4) and a PipeWire 0.3.65 start that needed more than 10 s on a cold runner
  (finding 7) - both fixed in tests/harness, not in product code.
* **Coverage** (product code, unit + integration, `reports/evidence/coverage.txt`, commit `0e307d1`): 5615 statements, 2120 branches,
  **91 %** (unchanged; 90 % is an intermediate value, not a ceiling).

## 10. Commits (develop, pushed; no tag, no release, no history rewrite)
`e99b757` fix(appliance) first-start hint - `e4a94f4` feat(demo) - `f1e15ab` feat(webui) licence + source link - `1bfdb78` fix(lab) no host
address - `eecc7d2` ci hardening + demo job - `e93d9fe` feat(demo) recovery/trust/monitor/docs - `dc92515` test(homeassistant) race -
`0e307d1` chore(demo) label + coverage doc - `4dea08f` test(pipewire) start wait - `87074ad` docs: full run, coverage,
measurements - this handoff.

## 11. Recommendation
1. Decide #46 (history) and #47 (settings), then switch the repository to public; the CI then runs on free runners.
2. Use the demo to walk through the guest experience with the owner and family - it exercises the same product code paths as the device.
3. Then the hardware gate G1 (unchanged) with real phones; the demo's virtual-phone scenarios map one-to-one to G1's B/C tests.

## 12. Operator summary (copy/paste)
Interactive Docker demo delivered: `make demo` runs the unchanged Hi-Fi Bridge (real BlueZ adapter over a simulated bluetoothd, real
PipeWire), real Lyrion with three Squeezelite rooms, Mosquitto, optional Home Assistant, virtual phones and a browser audio monitor of the
rooms' output (added latency ~260-295 ms, measured). Tests: local full run 17/17 suites green; demo stack test proves by cross-correlation
that the rooms play the phone's track; takeover, notification filter, multiroom, link loss, manual source change, P7 trust, MQTT, recovery
covered; GitHub Actions 14/14 green (run 44097021835) after two test-harness fixes; product coverage 91 %. Public-readiness: AGPL LICENSE, copyright,
source link in the WebUI, SECURITY.md, CI hardened (SHA pins, no secrets, pull_request only), secrets check of history/docs/screenshots/CI
logs clean. Found and fixed: Docker "internal" networks still let containers reach host services (a real Lyrion on the host was listed
read-only once) - now blocked for demo and lab. Owner decisions: private data in Git history (#46), GitHub settings (#47), then visibility.
Nothing verified on real Bluetooth, phones or a Pi; hardware gate unchanged.
