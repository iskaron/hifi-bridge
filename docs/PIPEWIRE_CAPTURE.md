# PipeWire capture — hardware-free verification (2026-11-15)

**Label: INTEGRATION — real PipeWire, WirePlumber and pipewire-pulse tools in Docker; SIMULATED source node.** The "phone" node is a
`module-pipe-source` named `bluez_input.AA_BB_CC_DD_EE_01`, fed through a FIFO with an index-addressable PCM pattern
(`testkit/pw/pcm_probe.py`). It is **not** a BlueZ node, **not** a phone and **not** a Raspberry Pi. Reproduce with `scripts/run_pipewire.sh`;
raw results are in `measurements/pipewire_capture_<version>.json`; the tests are in `tests/pipewire/`.

| Environment | PipeWire | WirePlumber | pactl |
|---|---|---|---|
| `debian:bookworm-slim` | 0.3.65 | 0.4.13 | 16.1 |
| `debian:trixie-slim` | 1.4.2 | 0.5.8 | 17.0 |

## Results
| # | Question | 0.3.65 | 1.4.2 | Consequence for the bridge |
|---|---|---|---|---|
| 1 | `parec --raw --format=s16le --rate=44100 --channels=2` from the node: headerless and **bit-exact**? | **yes** (5 s, 0 drops, repeats or foreign frames) | **yes** | **Default capture command** (`pcm.parec_argv`) |
| 2 | `pw-cat --record ... -` *without* `--raw` | raw PCM, bit-exact | **not raw PCM**: a little-endian Sun-AU stream (`dns.` magic) | never rely on `-` alone |
| 3 | Does `pw-cat` have `--raw`? | **no** ("unrecognized option") | yes; headerless and bit-exact | `record_argv(raw=True)` needs PipeWire ≥ 1.x |
| 4 | Graph clock: at PipeWire's default (48 kHz only), is a 44.1 kHz capture bit-exact? | **no**: resampled 44.1→48→44.1 kHz (foreign values, apparent drops/repeats) | **no** | set `default.clock.allowed-rates = [ 44100 48000 ]` in the appliance |
| 5 | **Target node missing:** what do the tools record? | **the default source** (e.g. a microphone): ~48k (`parec`) / ~131k (`pw-cat`) microphone frames in 3 s | same | **privacy risk** — target is only a hint |
| 6 | With `node.dont-fallback=true` + `node.dont-reconnect=true` | `parec` exits at once ("No such entity"); `pw-cat` keeps running **without data** | both exit at once | always set both; the supervisor's no-data watchdog (`stall_s`) restarts a silent `pw-cat` |
| 7 | The node vanishes mid-capture (phone disconnects) | no switch to the microphone; supervisor retries | same | OK |
| 8 | Presence check `pactl list short sources` | works | works | optional `present=` gate in `PipeWireCapture` (defence in depth) |
| 9 | WirePlumber without systemd-logind | **0.4.13 quits** ("failed to start systemd logind monitor" → "disconnected from pipewire") | 0.5.8 keeps running | an image without logind needs `bluez_monitor.properties["with-logind"] = false` (0.4) or `monitor.bluez.seat-monitoring = disabled` (0.5) |

The bridge's own `PipeWireCapture` supervisor ran every capture in these tests, so this exercises the real code path
(argv → subprocess → chunking → `on_pcm`).

## Test-harness notes (not product findings)
* A `module-pipe-source` suspends without consumers and shows a ~2 s gap when it resumes. The tests keep it running with a background
  consumer, so each capture joins a live stream. A real BlueZ node will behave differently on suspend/resume (hardware item A3/A4).
* The "microphone" is a second pipe source with a constant sample value, set as the default source, so any fallback becomes visible.
* Starting the stack inside `$(...)` hangs, because the daemons keep the pipe open. `testkit/pw/start_pipewire.sh` must be **sourced**.

## Still unverified (hardware: docs/HARDWARE_VALIDATION_PLAN.md A2–A5)
* The real node name, properties and `media.class` of a phone acting as an A2DP **source** in WirePlumber's bluez5 monitor. The template
  `bluez_input.<MAC_>` is a documented expectation, not an observation.
* Whether a BlueZ node delivers data while the phone is paused, and how it behaves on transport suspend/resume (gaps, timestamps).
* The SBC/AAC decode sample rate (44.1 vs 48 kHz) of real phones, and resampling CPU cost on a Pi Zero 2 W, Pi 3 B+ and Pi 4.
* `pipewire-pulse` availability and its resource use in the Buildroot image. Without it, `pw-cat --raw` on PipeWire ≥ 1.x is the alternative.

## Stabilisation package additions (2026-11-16)
* **Automatic tool choice** (`pcm.choose_capture`): `pw-cat --raw` when available (PipeWire >= 1.x - the Buildroot 2026.02 image has PipeWire
  1.2.8 and **no `parec`**), else `parec`, else `pw-cat` on 0.3.x. In the lab the choice is `parec` on 0.3.65 and `pw-cat --raw` on 1.4.2;
  both bit-exact, and neither captures the microphone when the node is missing.
* **pipewire-pulse restart regression** (`tests/pipewire/test_zz_restart.py`): during and after the restart, with the microphone returning
  first as the default source, the capture **never** contains microphone frames (asserted on both versions). Resume with the phone's pattern
  is asserted on 1.4.2. On 0.3.65 the re-created stand-in sometimes hands a new stream only digital silence, so resume is recorded there but
  not asserted.
* **Stand-in determinism:** on 0.3.65 a fresh `module-pipe-source` delivers only silence in about 1 of 3 sessions. The harness keeps a FIFO
  writer attached from before the module loads, and reloads the module until real frames arrive (counted as `_stand_in_reloads`).
* **Open (hardware A3/A4):** a capture stream can deliver *digital silence* indefinitely, and the no-data watchdog does not catch that. Whether
  a real BlueZ node can get stuck like this, and whether the bridge should restart capture after N seconds of silence while AVRCP says
  "playing", must be decided from real traces (OPEN_QUESTIONS #34).
