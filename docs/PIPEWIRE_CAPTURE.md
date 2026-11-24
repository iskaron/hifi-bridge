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

## Harness flakes found and fixed (2026-11-20, package 7b-3) - OPEN_QUESTIONS #26
The last full run (`reports/evidence/full_run_pkg6_os.log`, "PipeWire on debian:trixie") showed `pipewire-pulse 1.4.2` failing at start-up with
`mkdir() /run/pw/pulse failed: File exists` / `failed to create context: File exists`; the harness restarted it once, 8 tests passed, then
pipewire-pulse was **gone without a log line** and 4 tests failed. These were two different defects, both in the **test harness**
(the PipeWire *stand-in* for the phone), neither in the bridge's capture code:

| | Start-up race | Crash on module unload |
|---|---|---|
| Cause | `get_runtime_dir()` of `module-protocol-pulse` does `stat()` and, on ENOENT, `mkdir()` - and treats EEXIST from that `mkdir()` as fatal (PipeWire 1.4.2 source; 0.3.65 behaves the same). `start_pipewire.sh` polls readiness with `pactl info` right after launching the daemon; libpulse creates `$XDG_RUNTIME_DIR/pulse` as well. When both run in the same microsecond the daemon loses and exits | `pactl unload-module` of a `module-pipe-source` while streams are attached or keep reconnecting (the supervised capture of these tests retries every 0.2 s) kills `pipewire-pulse` 1.4.2 with **SIGSEGV (exit status 139)**; it writes nothing to its log. Not tested on 0.3.65 |
| Reproduction (`scripts/run_pipewire_repro.sh`, disposable container) | tight start loop, directory missing: **trixie 1.4.2: 9 of 150 and 1 of 40 start-ups failed, bookworm 0.3.65: 5 of 150**; directory created first: **0 of 300 (trixie), 0 of 150 (bookworm)** | trixie, load/unload cycles with a consumer: alternating kill/no-kill 4 of 5 containers crashed (after 19, 21, 55, 63 of 150 cycles), supervised consumer 2 of 3 (after 81 and 102 of 150); no crash with the consumer attached throughout (120), killed 0.2 s earlier (120) or killed immediately before (2 x 100). **Making the node vanish with `pw-cli destroy` instead of unloading its module: 0 of 3 x 150 with the supervised consumer** |
| Fix (harness only) | `start_pipewire.sh` creates `$XDG_RUNTIME_DIR/pulse` (0700) before the daemon starts - what a service manager's `RuntimeDirectory=` / tmpfiles entry does on a device | on PipeWire 1.x the tests make the stand-in node vanish with `conftest.vanish_node()` (`pw-dump` + `pw-cli destroy`, then wait until the source and its module are gone) instead of `pactl unload-module` (on 0.3.x they keep the unload: there the destroyed node leaves a reader on the FIFO that steals the next node's frames, the next node delivers only silence); `start_pipewire.sh` wraps pipewire-pulse so that its **exit status and signal are written to its log** (`### harness: pipewire-pulse exited with status 139 (killed by SIGSEGV)`) |

Also found while testing the fix: a wrapper *subshell* around the daemon inherits the caller's saved stdout (`. script >/dev/null` keeps the original in
fd 10) and held the capture pipe of `subprocess.run(capture_output=True)` / `$(...)` open for as long as the daemon lives (60 s time-out of every test);
the wrapper is therefore a fresh `bash -c`.

Evidence of the fix (all `btbridge-pw` containers, 2026-11-20; commands in `scripts/run_pipewire_repro.sh`, `testkit/pw/repro_*.sh`):
* start-up with the real `start_pipewire.sh` in a fresh container: 0 failures in 60 before, 0 in 60 after - the race needs a warm, tight loop (above);
  this is why the whole suite almost never showed it;
* the whole `tests/pipewire` suite, each run in a fresh container, three containers in parallel (CPU contention like a cold CI runner):
  old harness 34 of 34 green on trixie (10 sequential + 24 parallel) and 12 of 12 on bookworm; new harness 12 of 12 on trixie and 12 of 12 on
  bookworm (11 passed + 1 skipped each, as before), plus a first version of the change that ran 34 of 34 on trixie. A first version that used
  `pw-cli destroy` on 0.3.65 too failed `test_zz_restart` in 3 of 3 containers - hence the version split above. The two defects are rare in the
  suite (they showed once in CI): **the reproductions above are the proof, not the suite loop**; the suite loop shows that the fix breaks nothing.

Product relevance: none found. The bridge never unloads PipeWire modules and never starts `pipewire-pulse`; the Buildroot configuration (PipeWire
1.2.8) enables PipeWire without a pulse server (nothing in `os/` mentions one) and the bridge captures with `pw-cat --raw`. Whether removing a *real* BlueZ node
can upset any PipeWire daemon stays hardware item A3/A4.

## G1 real-hardware finding: the phone's audio was a playback stream, not a source (2026-11-24)
**Observed on hardware (operator, Pi 3 B+ Rev 1.3, Raspberry Pi OS Lite 64-bit trixie, kernel 6.18.50+rpt-rpi-v8, BlueZ 5.82,
PipeWire/WirePlumber 1.4.2, bundle of `develop` `b648936`; label REAL-HARDWARE):** pairing, connection, AVRCP metadata and the WebUI test
tone worked; Lyrion reported `play`; **the phone's music was inaudible** at the group. `wpctl status` while Spotify played: `Sources:
(empty)`; under `Streams:` `bluez_input.<MAC>.2` with `output_FL/FR > bcm2835 Headphones:playback_FL/FR [active]`. `pactl list short
sources`: only the onboard ALSA monitor, no `bluez_input.*`; `source-outputs` empty; no `pw-cat` child of the bridge.
Setup findings on the way there (fixed in `hwkit/pi-setup`): `libspa-0.2-bluetooth` missing under `--no-install-recommends` (no
"Audio Sink" UUID), WirePlumber's seat monitoring active on a headless Pi that HAS logind (the old setup installed the override only
when `loginctl` was missing), a stale bond on the Pi after the phone had forgotten it (resolved by removing the bond; **not** by
`JustWorksRepairing = always`).

**Cause - read from the sources (PipeWire 1.2.8 and 1.4.2, WirePlumber 0.5.8, fetched in Docker 2026-11-24):**
* `spa/plugins/bluez5/media-source.c` (1.4.2 lines 991-996, `impl_init` line 1880): the node of a phone streaming *to* us is
  `media.class = "Stream/Output/Audio"` unless the node property `bluez5.media-source-role = "input"` is set, then `"Audio/Source"`
  (and `node.driver = true`). 1.2.8 has the same switch (line 1749). WirePlumber's documentation (`daemon/configuration/bluetooth.rst`):
  "Defaults to `playback`, playing the incoming stream out to speakers. Set to `input` to use the smartphone as an input for apps".
* WirePlumber `scripts/monitors/bluez.lua` `createNode`: name `bluez_input.<address>.<id>` (`id` = 2 for the A2DP source,
  `bluez5-device.c` `emit_dynamic_node(this, t, 2, ...)`), `node.autoconnect = true` for `a2dp.source` - which is why the stream was
  linked to the headphone sink - and the `monitor.bluez.rules` are applied after that.
* `bridge/btbridge/adapters/bluez/pcm.py` at `b648936`: `node_name_for()` (line 31) built `bluez_input.<MAC_>` without the `.2`;
  `factory.py` (line 17) used it as the `pw-cat --target`; `PipeWireCapture._run` (line 156) sent the child's stderr to `/dev/null`.

**Reproduced hardware-free (INTEGRATION, real PipeWire 1.4.2 in `btbridge-pw:trixie`, SIMULATED phone stream carrying the BlueZ node
properties):** the old command line (`pw-cat --record --target bluez_input.<MAC_> -P { node.dont-fallback ... } --raw -`) against the G1
topology exits after **10 ms with status 1**, stderr `stream node 54 error: defined target not found` - 3 of 3 runs, i.e. a child that
never persists, consistent with the observation (**on the Pi itself this remains the most likely explanation, not an observation**: the
bridge did not log the child's stderr). Correcting only the name (`--target bluez_input.<MAC_>.2`) records the playback stream in the lab,
but WirePlumber keeps its links to the sink: the music would go on playing on the device's own output (`pw-link -l`), which the product
must not do. Hence the fix is the role, not the name.

**Fix (DECISIONS #142-#145):**
| Part | What |
|---|---|
| Graph | WirePlumber rule `50-hifi-bridge-a2dp-input.conf` (`monitor.bluez.rules`: `node.name = "~bluez_input.*"` → `bluez5.media-source-role = "input"`): the phone becomes an Audio/Source, never linked to a speaker. Same file in the image overlay (`/etc/wireplumber/wireplumber.conf.d/`) and in `hwkit/pi-setup/` (a test keeps them identical) |
| Target | `pcm.find_phone_node(address)`: `pw-dump`, a node with `api.bluez5.address` = the active phone, `api.bluez5.profile = a2dp-source`, `media.class = Audio/Source`, not internal; several → the highest `object.serial`. Resolved again before every child start. A playback-role node is **never** a target; the reason names the missing rule |
| Lifecycle | the adapter starts exactly one capture worker per streaming phone (repeated/replayed `pending`/`active` do not restart it), stops it on `idle`, transport removal, disconnect and adapter stop; each transport transition is logged once |
| Diagnostics | the child's stderr is drained while streaming (a full pipe cannot block it), the last 2 KiB are kept, an exit/stall is logged with the child's last stderr line; identical messages are logged once and then every 50th time; `PipeWireCapture.status()` and `audio_capture` in the diagnostics bundle (MACs pseudonymised) |
| Test Pi | `setup_test_pi.sh`: `libspa-0.2-bluetooth`, the role rule, seat override when headless (`systemctl get-default` ≠ graphical, or `--headless`), restarts the user's PipeWire; `hwkit preflight`: FAILs for each of the three G1 setup findings and for a missing role rule; `hwkit capture` reports peak level and non-zero fraction |
| Image | overlay: the role rule and `10-hifi-bridge-rates.conf` (the 44.1 kHz clock was never in the image); image checks `audio.bluez5-spa-plugin`, `audio.capture-tools`, `audio.a2dp-input-role`, `audio.allowed-rates`, `audio.seat-monitoring` (INFO) |

**Evidence (hardware-free):** UNIT `tests/unit/test_pcm_ingress.py` (G1 graph fixtures `tests/fixtures/pipewire/`), INTEGRATION
`tests/pipewire/test_g1_ingress.py` on PipeWire 0.3.65 **and** 1.4.2: the phone-like playback stream is never captured (no child
started, reason names the rule, microphone untouched); the same audio as an input-role Audio/Source is found by its properties and
captured **bit-exactly**, and after it vanishes the bridge waits without capturing anything. STATIC `tests/os/test_audio_ingress_image.py`,
`tests/hwkit/test_kit_completion.py` (setup script dry run, preflight).

**HARDWARE-PENDING (G1 retest, docs/HARDWARE_TEST_PROCEDURE.md section 5):** that WirePlumber 1.4.2 on the Pi applies the rule to the
phone's node (`wpctl status` lists it under *Sources*, no stream to the headphones); that the node acquires the transport when the
bridge's capture starts on `pending` (in the input role nothing else consumes the node - PipeWire acquires on node start,
`media-source.c` `do_start`); real amplitude and sample rate (SBC 44.1 vs 48 kHz); pause/resume (transport idle → capture stopped →
restarted on the next `pending`); reconnect; that nothing plays on the Pi's own output.
