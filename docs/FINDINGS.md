# Phase 1 findings (software-integration evidence)

Labels (docs/ACCEPTANCE.md): `REAL-LMS` real Lyrion 9.1.2 in Docker; `INTEGRATION` real LMS + real Squeezelite
with the **simulated** phone; `SIMULATED`; `UNIT`; `STATIC-ONLY`; `UNTESTED`.
Nothing here is `REAL-HARDWARE`. All timings are *software virtual-DAC* timings (see "Measurement method").
Raw numbers: `measurements/*.json`; full logs: `reports/evidence/full_run.log`.

## Measurement method (and its limits)
* Squeezelite runs with `-o -` (PCM to stdout). Measured: that output is **not clocked** (floods the pipe at memory speed),
  so `btkit.pcm_tap` consumes it at exactly 44.1 kHz through a 4 KB pipe (a "virtual DAC", +-~25 ms granularity).
  "Player output time" = when the virtual DAC consumed the frame. It excludes any real DAC/speaker/amplifier latency.
* All containers share the host kernel clock, so wall-clock timestamps from simulator, bridge and taps are comparable.
  This validates nothing about clocks on real devices.
* The test signal is counter-based (frame i depends only on i), so captured audio is aligned to the generator exactly and
  checked **bit-for-bit** in 100 ms blocks (`btkit.analyze.integrity`). The last block at stop always differs (tail).

## LMS facts established (REAL-LMS 9.1.2 + Squeezelite 1.9.9-1414)
1. **Endless external HTTP URL playback works** with plain JSON-RPC `playlist play <url>`; no LMS plugin needed. WAV (PCM) and
   FLAC (via `flac` CLI) both arrive **bit-exact** at the player (WAV 85/86, FLAC 80/81 blocks in the final run; the one miss is the stop tail).
2. LMS first scans the URL itself (GET, drops after ~7 KB), then the player connects **directly** with `Range: bytes=44-`
   (LMS parsed the WAV header). The bridge honours Range by skipping its header; ignoring it injects 44 stray bytes.
   In a **sync group** (or for FLAC to PCM-only players) LMS fetches once and proxies.
3. **`playlist deleteitem <url>` is a URL-addressed release**: stops our stream, **no-op when another source is current**
   (tested vs a different stream). This is the only destructive command the bridge uses - no generic stop/pause/clear.
4. **Stalls are catastrophic, silence-fill is mandatory.** If the live stream delivers no bytes the player underruns, LMS sends
   `strm p` and only un-pauses ~5.5 s after data returns (5 s and 15 s stalls: always ~5.5 s); a 40 s stall makes LMS restart the track.
   Baseline vs fix (INTEGRATION, 4 s source stall): no fill -> 1 underrun, audio back after 5.1-5.3 s, ~48 % audible in next 10 s;
   silence keepalive -> 0 underruns, back after 1.3-1.4 s, ~86 % audible (two runs).
5. **Startup floor ~1.5-1.9 s**: LMS makes Squeezelite wait for 255 KB of stream data (`bufferThreshold` = min(bitrate/8 x bufferSecs, 255 KB);
   bitrate known from the WAV header), which fills at real-time rate. WAV median 1.56 s (n=5), FLAC 1.90 s (n=5).
   **Negative result:** an `icy-br` hint and silence pre-roll gave no gain (`measurements/NEGATIVE_startup_policy_icybr_preroll.json`),
   so both were removed from the code. Lowering the threshold would need an LMS plugin protocol handler or server settings
   (`bufferSecs` min 3 is already capped) - not pursued; no plugin without the owner's decision.
6. Default buffering absorbs delivery hiccups up to 1.5 s with no underrun; a 2.5 s stall underruns (1 underrun).
7. **Legacy-capability emulation** (Squeezelite `-c pcm,mp3`): WAV direct and FLAC transcoded by LMS to PCM, both bit-exact (84/84, 81/81).
   This is capability negotiation only - **not a real Squeezebox**; real hardware is UNTESTED.
8. **Sync group** (3 players, target = master *or* a slave member): inter-player offset 4-20 ms (stable within a run, varies between runs: 4.2, 8.7, 20.3 ms) at the virtual DACs;
   Squeezelite's own sync correction skips 441-837 frames during the first seconds (so grouped output is aligned, not bit-contiguous).
9. In-band **ICY metadata** on a WAV stream updates LMS `current_title` ("Artist - Title") while PCM stays bit-exact
   (real-phone AVRCP metadata UNTESTED).
10. Relative `mixer volume +/-N` on one sync member leaves the other member unchanged (60->50 / 30->30): relative deltas preserve room balance.
11. UDP discovery (broadcast 3483, TLV) finds the LMS (name, JSON port, version, UUID). Needs directed broadcasts when no default route.
12. During a pure **LMS outage** the player keeps playing the bridge's live stream by itself (99.99 % audible for 20 s) and LMS re-attached
    without restart; in a **player outage** audio resumed 0.47-0.57 s after the player returned, first frame only ~1.5 s old (nothing stale).
    (Squeezelite behaviour; hardware players may differ.)

## Pause strategies (INTEGRATION, 3 cycles each, virtual DAC)
| strategy | Pause -> last audible output | Resume -> first output | LMS commands |
|---|---|---|---|
| `drop_restart` (default) | **0.06-0.08 s** | 1.54-1.59 s | `deleteitem` on pause, fresh `play` on resume |
| `keepalive_silence` | 1.58-1.61 s | 1.54-1.57 s | none until grace expiry |
Grace: keepalive releases LMS item 30.1 s after Pause. Resume after grace = fresh play, first audio position is the resume position (no stale audio).
Recommendation: `drop_restart`; the owner may still pick keepalive (no LMS traffic during pauses) - a configuration value.

## Bridge behaviour verified
Unit (55) + integration: notification beeps (AVRCP-capable and AVRCP-less phone) never touch a playing radio; sustained media overrides after the 2 s filter
(measured 2.03 s) and never restores the old radio; override-off never takes over; manual switch to radio then late pause/grace/disconnect sends **zero** LMS
commands; two bridges on one player: second overrides, first goes passive; second phone refused; audio-toggle and target-change release only our stream;
diagnostic test refuses on a busy target and never cuts a user's source; tokens never appear in logs/status/timeline; stale session URL -> 410.
Mutation check: 8/8 deliberate invariant-breaking mutants were caught by the unit tests.

## Clock drift / resources (x86 dev host, Docker; NOT Raspberry Pi)
Simulated +-3000 ppm: slow phone -> 7 silence insertions of ~40 ms per 100 s; fast phone -> ~470 drops averaging ~15 frames (0.3 ms); 0 underruns.
At +-300 ppm / 120 s the skew (36 ms) stayed inside tolerance: 0 corrections. Real phone drift UNTESTED.
Bridge CPU ~2.4-2.7 % of one core (WAV or FLAC), `flac` encoder 0.4 %, RSS flat (106.12 -> 106.14 MB / 60 s, whole test process incl. numpy), ring buffer fixed 1.06 MB.

## Bugs found by the tests while building (all fixed, regression-tested)
feeder "drop ahead" never converged (dropped frames moved the timeline); volume policy replayed a capped jump so a small phone volume-DOWN raised LMS volume;
diagnostic test tone was cut off at half length (player lead not accounted for); unclocked Squeezelite stdout made first timings meaningless.

## Known limitations
* Slow-clock phones produce periodic ~40 ms silence gaps (no resampling); acceptable for the spike, improvable.
* Startup (Play -> sound) ~1.6 s and Resume ~1.6 s are bounded by LMS buffering; Pause is fast with `drop_restart`.
* Remaining ownership race: between observing the target and sending `play` during a takeover (intentional override anyway). Releases are URL-addressed.
* Only Squeezelite 1.9.9 and LMS 9.1.2 were exercised.

## Phase-1 review work package: BlueZ/PipeWire preparation (2026-11-15; labels as in docs/BLUEZ_ADAPTER_DESIGN.md)
* Added the hardware-free layers of the real adapter (translator, bus interface, jeepney backend, PCM capture supervision, pairing agent policy, bluetoothd restart
  recovery) and tests: UNIT (scripted events), INTEGRATION against a **fake** BlueZ on a private D-Bus daemon, and an end-to-end run fake BlueZ -> adapter -> bridge -> real LMS
  -> real Squeezelite (bit-exact, ownership/late-event/second-phone cases). These show the *code* behaves per the BlueZ documentation as I read it; they say nothing
  about real bluetoothd, phones, PipeWire nodes or the Pi.
* Negative/corrective results: `pw-cat` 0.3.65 has no `--raw` (my first command line was wrong); no automated PipeWire capture test could be built in the time box;
  jeepney local filters need sender-less rules.
* Owner decisions of the review are pinned by unit tests (`tests/unit/test_review_decisions.py`): default `drop_restart`, ICY off, no power-off path in the LMS client or controller.

## Phase 2a (2026-11-15): what the tests found
* **PipeWire** (INTEGRATION, real 0.3.65 + 1.4.2): a missing capture target silently records the default source (privacy defect, fixed); `pw-cat -`
  is a Sun-AU stream on 1.4; 48 kHz-only graph breaks bit-exactness; WirePlumber 0.4 quits without logind. Details: docs/PIPEWIRE_CAPTURE.md.
* **Hardware kit** self-tests: MAC addresses inside BlueZ object paths were not anonymised (regex word boundary after `dev_`), replay clock origin
  wrong, a partial first block counted as a repeat. The indexed test track survives real SBC (328k/229k), AAC (256k/128k) and a 48 kHz path.
* **WebUI** in a real browser: all form POSTs were rejected because `Referrer-Policy: no-referrer` makes Chromium send `Origin: null`;
  wizard test-sound button blocked by the wizard guard (found by the LMS e2e test); CSP violation on text error pages; COOP warning on HTTP.
  During the fix the CSRF check was briefly made unreachable by an indentation slip - caught by the unit tests before any commit.
* **Static power-off guard** (review decision #17) was a substring check that fired on *reading* the power state; replaced by a call-pattern
  guard that has its own test.
* **Phase-1 harness:** `run_all.sh` recorded the exit code of `grep`, not of the outage scenario (the scenario's own "exit code: 0" lines in the
  phase-1 log remain valid evidence); fixed with PIPESTATUS.
