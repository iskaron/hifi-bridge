# Hardware validation kit (`hwkit`)

Tooling to execute `docs/HARDWARE_VALIDATION_PLAN.md` on a **real Raspberry Pi with real phones and players**, and to analyse the
results reproducibly. Nothing in the kit has been run on hardware yet. Its own tests use a fake BlueZ, simulated PipeWire nodes
and real SBC/AAC codecs (see "Kit self-tests").

**Rules:** run the recording commands **only on the Pi under test**, never on the development machine. Use a test Pi and a test player
first, and touch the production LMS only read-only, unless the owner explicitly agrees (plan §0). Results are always labelled
`REAL-HARDWARE`, and the results tool refuses anything else for executed tests.

Day plan, order, stop rules and what to bring back: `docs/HARDWARE_TEST_PROCEDURE.md`. Test Pi setup: `pi-setup/` (configuration from
the lab findings + `setup_test_pi.sh`, which refuses to run unless `--i-am-on-the-test-pi` is given AND the board is a Raspberry Pi).

## 1. Prepare (on the development machine, Docker only)
```bash
scripts/hwkit_bundle.sh          # -> dist/hwkit-bundle-<rev>.tar.gz and dist/hwkit-tracks/*.wav|*.m4a
```
* Copy `dist/hwkit-tracks/*.m4a`, or the WAV files, to each phone, for example with the phone's file manager or a music app library.
  * `bab_continuity_30min`: plan E1/E3 (continuity, drift).
  * `bab_startstop_2min`: plan E2 (play, pause and resume timing).
  * `bab_quiet_passage`: plan C1/C2 (15 s of digital silence inside media must not count as a pause).
* Copy the bundle to the Pi and unpack it: `tar xzf hwkit-bundle-*.tar.gz && cd <dir>`.

## 2. On the Pi
Requirements: Python ≥ 3.9, `python3-jeepney`, BlueZ, PipeWire with WirePlumber and pipewire-pulse (`pactl`, `parec`). Use a stock
Raspberry Pi OS Lite for validation; this is **not** the appliance image. Set `PYTHONPATH=$PWD/bridge:$PWD/hwkit`.

Configure PipeWire before testing (findings in `docs/PIPEWIRE_CAPTURE.md`; `pi-setup/setup_test_pi.sh` does all of it):
* `libspa-0.2-bluetooth` (PipeWire's BlueZ plugin; only a *Recommends*, so `--no-install-recommends` skips it - G1);
* `default.clock.allowed-rates = [ 44100 48000 ]`;
* `bluez5.media-source-role = input` for `bluez_input.*` (`pi-setup/50-hifi-bridge-a2dp-input.conf`): without it the phone's audio is a
  playback stream on the Pi's own output and the bridge records nothing (G1);
* a headless Pi (no active local session, even WITH systemd-logind): WirePlumber 0.5 needs `monitor.bluez.seat-monitoring = disabled`
  (0.4: `with-logind = false`). Do not set `JustWorksRepairing = always` (see `pi-setup/main.conf.snippet`).

| Step | Command | Plan IDs |
|---|---|---|
| Check the Pi before testing | `python3 -m hwkit preflight` (read-only; every FAIL prints its fix) | all |
| Guided session (recommended) | `python3 -m hwkit guide results.json --address <phone MAC> [--mic <source>] [--only B1,B4]` | all |
| E2 timing | `python3 -m hwkit latency traces/e2.jsonl cap/e2-run1` (AVRCP event -> sound at the mic, on the Pi's clock) | E2 |
| Start a results file | `python3 -m hwkit results new results.json --board "Pi 3 B+ rev 1.3" --operator NAME --phone "Pixel 8 / Android 15" --player "Squeezelite 2.0 on PC"` | all |
| Record the environment | `python3 -m hwkit env -o env.json` | A1 |
| Record a D-Bus trace with markers | `python3 -m hwkit trace -o traces/android-spotify.jsonl`, then type markers as you act on the phone (`spotify play`, `expect:media`, `messenger beep`, `expect:no-media`, ...); Ctrl-D ends | A6, B*, C1/C2, D1/D2 |
| Replay a trace through the bridge logic | `python3 -m hwkit replay traces/android-spotify.jsonl` (exit code 0 = every `expect:` held) | C3 |
| Capture the phone's audio (and a microphone at the speaker) | `python3 -m hwkit capture -o cap/e2-run1 --address <phone MAC> --seconds 120 [--mic <pipewire source name of a USB mic>]` (the phone's node is found like the bridge does; prints peak level and non-zero fraction) | A3, A4, E1, E2 |
| Enter a result | `python3 -m hwkit results set results.json --id C1 --status PASS --observations "..." --evidence traces/android-spotify.jsonl --device "Pixel 8 / Android 15 / Spotify 9.0"` | all |
| Check and report | `python3 -m hwkit results check results.json` · `python3 -m hwkit results report results.json > REPORT.md` | all |

Marker conventions: free text describes what you did. `expect:media` means "from here until the next marker, this is music the bridge should
play". `expect:no-media` means "this must **not** take over a playing radio", so no continuous media detection for ≥ 2 s. Put the expectation
marker directly after the action marker.

## 3. Analyse (development machine or Pi with numpy)
```bash
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit analyze cap/e2-run1
```
This reports continuity per recording: decoded blocks, drops, repeats and silence gaps. With `--mic` it also reports the latency from **bridge
input to speaker output**, measured on one clock (the Pi's), median/p95 per block. The Bluetooth delay from phone to bridge is **not**
included, because no phone-side timestamp exists. Report that part as "not measured".

Add real traces as regression fixtures with `cp traces/x.jsonl tests/fixtures/traces/real_<phone>_<scenario>.jsonl`. They are anonymised by
default (MAC addresses replaced by stable pseudonyms; `--no-anonymize` turns that off). `tests/unit/test_trace_fixtures.py` then replays them
in every test run.

## Kit self-tests (hardware-free; what they do and do not show)
| Test | Label | Shows |
|---|---|---|
| `tests/hwkit/test_signals_codecs.py` | UNIT + INTEGRATION (real FFmpeg SBC 328k/229k and AAC 256k/128k, 48 kHz path) | The test track survives the codecs, and drops, repeats, gaps and latency are found exactly. Not shown: a real phone encoder or radio losses. |
| `tests/hwkit/test_trace_results_cli.py` | SIMULATED-BLUEZ (fake `org.bluez` on a private D-Bus) | Trace recording, anonymisation, replay checks, results rules and the CLI. |
| `tests/pipewire/test_hwkit_capture.py` | INTEGRATION (real PipeWire 0.3.65 and 1.4.2, simulated node) | The capture path writes clean, timestamped WAV. |

Run them with `scripts/run_hwkit.sh` and `scripts/run_pipewire.sh`.
