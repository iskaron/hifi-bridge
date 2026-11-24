# G1 real-hardware Bluetooth PCM ingress repair (2026-11-24)

**Status: fixed in software and in the images; HARDWARE-PENDING.** Nothing in this report is a new real-hardware result. The fix
must be confirmed by the operator on the Pi (retest: `docs/HARDWARE_TEST_PROCEDURE.md` section 5). Labels: REAL-HARDWARE (the
operator's observations of the first G1 day), SOURCE-READ, UNIT, INTEGRATION (real PipeWire / real Lyrion in Docker, simulated phone),
STATIC, BUILD (built, never booted).

## 1. Root cause - observations vs. hypotheses
| | Statement | Basis |
|---|---|---|
| Observed | The phone's audio was a PipeWire **playback stream** `bluez_input.<MAC>.2` linked to the Pi's headphone sink; no Bluetooth source existed; no `pw-cat` child of the bridge | REAL-HARDWARE (`wpctl status`, `pactl list short sources/source-outputs`, `ps`) |
| Confirmed | That is WirePlumber's **default** `bluez5.media-source-role = playback`: media class `Stream/Output/Audio`, `node.autoconnect = true`; `input` makes it an `Audio/Source` | SOURCE-READ: PipeWire 1.4.2 `spa/plugins/bluez5/media-source.c` lines 991-996 and 1880 (1.2.8: line 1749); WirePlumber 0.5.8 `monitors/bluez.lua` `createNode`, `docs/rst/daemon/configuration/bluetooth.rst` |
| Confirmed | The bridge targeted a guessed name without the `.2` suffix | `bridge/btbridge/adapters/bluez/pcm.py` line 31 (`node_name_for`) and `factory.py` line 17 at `b648936` |
| Reproduced | The old command line against that topology exits after ~10 ms with status 1, stderr `defined target not found` (3/3) | INTEGRATION, real PipeWire 1.4.2, simulated stream with the BlueZ node properties |
| Most likely (not observed on the Pi) | On the Pi the capture child also died at once and was restarted with back-off, which is why `ps` never showed it | the bridge discarded the child's stderr (`pcm.py` line 156, `stderr=subprocess.DEVNULL`) - fixed, now logged |
| Reproduced | Correcting only the name records the stream, but WirePlumber keeps it linked to the sink: local playback continues | INTEGRATION (`pw-link -l`) |
| Not shown to be a cause | `JeepneyBus` ignored the agent capability (hard-coded `NoInputNoOutput`, the same value the adapter passes) | fixed anyway (no behaviour change) |

Setup findings of the same day (REAL-HARDWARE), fixed in the test kit: `libspa-0.2-bluetooth` missing under `--no-install-recommends`;
seat monitoring active on a headless Pi with logind (old rule keyed on `loginctl` missing); a stale Pi-side bond blocked re-pairing
(documented fix: remove the bond; `JustWorksRepairing = always` is now explicitly not recommended).

## 2. Changes (branch `wip/g1-pcm-ingress`, merged into `develop`)
| Area | Files | What |
|---|---|---|
| Graph | `hwkit/pi-setup/50-hifi-bridge-a2dp-input.conf`, `os/br2-external/board/hifi-bridge/overlay/etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf` | WirePlumber rule `bluez5.media-source-role = "input"` for `bluez_input.*` (DECISIONS #142) |
| Discovery | `bridge/btbridge/adapters/bluez/pcm.py` (`pw_dump`, `find_phone_node`), `factory.py` | target = node with the phone's `api.bluez5.address`, `api.bluez5.profile = a2dp-source`, `media.class = Audio/Source`; resolved before every child start; a playback stream is never a target (#143) |
| Supervision | `pcm.py` (`PipeWireCapture`) | stderr drained + bounded (2 KiB), exit/stall logged with its last line, de-duplicated; `status()`; target re-resolved per child; stall watchdog also while stderr chatters |
| Lifecycle | `adapter.py` | one worker per streaming phone; repeated/replayed `pending`/`active` no restart; stop on idle/removal/disconnect/stop; transitions logged once; `capture_status()` (#144) |
| Diagnostics | `service.py` | `audio_capture` in the diagnostics bundle (MACs pseudonymised) |
| Agent | `jeepney_bus.py`, `testkit/btkit/fake_bluez.py` | the capability passed to `register_agent` reaches BlueZ |
| Test Pi | `hwkit/pi-setup/setup_test_pi.sh`, `main.conf.snippet`, `hwkit/hwkit/preflight.py`, `capture.py`, `__main__.py`, `catalog.json`, `hwkit/README.md` | `libspa-0.2-bluetooth`; role rule; headless detection by boot target (`--headless`/`--desktop`); dry-run mode for tests; preflight FAILs for each G1 finding; `hwkit capture` discovers the node, refuses a playback stream with the reason, reports peak level and non-zero fraction (#145) |
| Image | overlay `etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf` (+ the rule), `os/tools/image_check.py` | the image had no PipeWire configuration at all; checks `audio.bluez5-spa-plugin`, `audio.capture-tools`, `audio.a2dp-input-role`, `audio.allowed-rates`, `audio.seat-monitoring` (INFO) |
| Lab harness | `testkit/btkit/analyze.py` | `load()` reads only the requested window (OPEN_QUESTIONS #103: after days of lab uptime the full parse starved the in-process radio and failed 4 integration tests, also on unchanged code) |
| Docs | `docs/PIPEWIRE_CAPTURE.md` "G1", `docs/HARDWARE_TEST_PROCEDURE.md` §5, `docs/HARDWARE_GATE_CHECKLIST.md` G1-R, `docs/DECISIONS.md` #142-#145, `docs/OPEN_QUESTIONS.md` #100-#103, `docs/FINDINGS.md`, `docs/IMAGE_BUILD.md` | |

Architecture choice (DECISIONS #142): the upstream-documented role switch. Rejected: name-only fix (local playback continues), an
application-owned null sink + monitor (second routing policy against WirePlumber's autoconnect, monitor would carry other audio too),
hand-linking ports with `pw-link` (IDs change per connection, races with WirePlumber). Works the same on Pi OS Lite (WirePlumber 0.5.8
user session) and in the image (PipeWire 1.2.8 / WirePlumber 0.5.10 system-wide); WirePlumber 0.4 is not supported for this (preflight FAIL).

## 3. Tests (all in Docker; results of this package)
| Suite | Result | Label |
|---|---|---|
| `tests/unit/test_pcm_ingress.py` (new: G1 graph fixtures, discovery, supervisor, adapter lifecycle, agent capability, diagnostics bundle) | 21 passed | UNIT/SIMULATED |
| `tests/unit/test_capture_load.py` (new: windowed capture load equals the full parse; cost independent of lab history) | 3 passed (red before: 3-hour history load too slow) | UNIT |
| `scripts/run_unit.sh` (whole unit suite) | **1130 passed** (1 warning: pre-existing harness thread in `test_stream_faults.py`) | UNIT |
| `scripts/run_pipewire.sh` incl. new `tests/pipewire/test_g1_ingress.py` | bookworm 0.3.65: 13 passed + 1 skipped; trixie 1.4.2: **14 passed** - playback stream never captured (no child, reason names the rule), input-role source found and captured **bit-exactly**, nothing captured after it vanishes | INTEGRATION (real PipeWire), SIMULATED phone node |
| `scripts/run_integration.sh` (real Lyrion 9.1.2, 4 Squeezelite, fake BlueZ over D-Bus incl. the agent capability) | **56 passed** (after the harness fix; 4 radio/guest tests had failed before it - also on unchanged `b648936` code - OPEN_QUESTIONS #103) | INTEGRATION |
| `scripts/run_hwkit.sh` (setup-script dry run, preflight with the G1 state, capture refusal + levels) | 36 passed | UNIT/SIMULATED |
| `scripts/run_os.sh` (incl. new `tests/os/test_audio_ingress_image.py`) | defconfigs/licences/tests exit 0; **225 passed** | STATIC |
| `scripts/run_static.sh`, `scripts/run_entry_tests.sh` | exit 0; 100 passed | STATIC |
| Images rpi3_64 / rpi4_64 / rpizero2w_64 (incremental rebuild, overlay + bridge) | image checks **0 FAIL / 132 PASS / 22 INFO** each, new `audio.*` PASS; secret scans 0; OTA checks pass (bundles 76 092 873 / 76 301 769 / 75 941 327 bytes); size plan ok | BUILD - never booted |
| Old command line replayed against the G1 topology | exit 1 after ~10 ms, `defined target not found` (3/3) | INTEGRATION |

## 4. What still needs the operator (G1-R, `docs/HARDWARE_TEST_PROCEDURE.md` §5)
* the phone is listed under *Sources* (not a stream to the headphones) after the rule and a reconnect;
* `hwkit capture` without Lyrion shows real amplitude (peak above -40 dBFS, non-zero fraction above 0.5) - first proof of PCM ingress;
* the node acquires the transport when the bridge's capture starts on `pending` (OPEN_QUESTIONS #100), with Android and iPhone;
* audible music in each room of the group, in sync; pause/resume, reconnect, manual-source priority, nothing on the Pi's own output;
* the image (G2): whether the bridge user reaches the system-wide PipeWire (OPEN_QUESTIONS #101, #24/#25).
