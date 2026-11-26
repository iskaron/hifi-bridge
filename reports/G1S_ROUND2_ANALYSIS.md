# G1-S round 2 — four Pi traces after the Arbeitszimmer reboot (2026-11-25)

**Status: G1-S NOT passed.** Test 4 (three rooms, after the Arbeitszimmer power-cycle) played ~605 s with **one** audible ~6 s group
dropout. A pass needs a fresh three-room run of ≥10 min with no audible interruption, no `STUCK`/`WAITING` after start-up, no unplanned
`rN start GET`, and logs that support it.

Evidence labels: **REAL-HARDWARE** (operator Pi traces, `hifi-bridge-g1s2-evidence-2026-11-25.zip`, bridge rev `c56b32c` =
`dist/HWKIT_REVISION`, which is the commit *before* this package), **STATIC** (Lyrion 9.1.2 source read in the lab container; the
operator runs 9.1.1 — same code paths assumed, not verified), **INTEGRATION** (lab, real LMS + Squeezelite), **UNIT**.
Trace line numbers below refer to the files in the zip. Lyrion logs of tests 1–3 were not supplied; their events are quoted from the
operator's handoff text and are marked *(operator text)*.

## 1. Evidence table

| | Test 1 | Test 2 | Test 3 | Test 4 |
|---|---|---|---|---|
| Group | Küche + esparagus-01 + Arbeitszimmer | esparagus-01 + Arbeitszimmer | Küche + Arbeitszimmer | all three (target changed to the group at t=341) |
| Office Pi | before reboot (silent, per operator) | before | before | **after** hard power-cycle |
| Bridge target (first in `members:`) | Küche | **Arbeitszimmer** | **Arbeitszimmer** | Arbeitszimmer, from t=341 esparagus-01 |
| Stream start | t=99.17 (14:52:19) | t=22.64 (14:58:50) | t=27.95 (15:05:59) | t=10.48 (15:18:03) |
| LMS `time` | 0 → 24.4, **normal** | **24347.9 from the first line** (l.27) | 0.0, then 24347.9 one second later (l.26–27) | normal; 327.7 at t=340; new song → 0.0; 120.4 at stall |
| First write block (`blk` ≥100 ms) | t=124.56 (l.54): **25.3 s after start** | t=48.44 (l.52): **25.6 s** | t=52.43 (l.50): **24.4 s** | **none** (`blk` 1–3 ms throughout, no `STUCK` in 651 lines) |
| First Lyrion underrun (*operator text*) | Küche 14:52:45.4417, esparagus-01 +12 ms | esparagus-01 (each episode) | Küche (each episode) | **Küche** 15:25:35.1284 (`excerpt`, l.1) |
| Bridge input during the event | `in: nz=1.00`, 50 chunks/s | same | same | same (`nz=1.00`, ~176400 B/s, l.487+) |
| Recovery | none in 50 s, operator stop 14:53:09 | `peer-reset` + `GET 206` at 14:59:48, 15:00:51, 15:01:54 (l.90/156/222) | the same at 15:06:55, 15:07:55 (l.82/145) | `peer-reset` t=466.62 (l.491) + `r5 GET 206 range=bytes=21235252-`; `WAITING` cleared 15:25:41 |
| Restart range | – | `bytes=4294967336-` | `bytes=4294967336-` | `bytes=21235252-` (= 120.4 s × 176400 + 44 − align) |
| Class | FAIL | FAIL (3 episodes / 3 min) | FAIL (3 episodes / 3 min) | **not a strict pass**: 1 dropout in 605 s |

Controls (operator text): esparagus-01 solo 10 min OK; esparagus-01 + Küche 11 min OK.

## 2. Causal assessment

### 2.1 What is confirmed (Observed)

1. **The bridge was never the source of silence.** In all four runs `in:` and `feeder:` carry real audio (`silence +0fr`, `nz≈1`) while
   the group is silent. Tests 1–3: `out:` drops to `0B … STUCK` *after* Lyrion stopped reading. Test 4: `out:` never drops at all.
2. **Tests 1–3 fail 24–26 s after every stream start, and again ~32–38 s after every restart.** Three independent runs, three groups,
   one common member (Arbeitszimmer), one common timing (table row "First write block"; test 2 second/third cycles: l.131 and l.195).
3. **Test 4 (same bridge build, same Lyrion, same three players) differs in exactly one known respect: Arbeitszimmer was power-cycled.**
   The 25-s stalls vanished and the dropout became a single 6 s event. (Confounders: the target change at t=341; random timing.)
4. **In tests 2 and 3 Lyrion's elapsed time sat at 24347.9 s from the first seconds of the stream**; in tests 1 and 4 it advanced
   normally. 24347.9 s is the virtual WAV duration (§2.3).
5. **Test 4's event is a Küche underrun with the bridge writing continuously.** The Lyrion excerpt shows (all times 15:25): 35.1284
   Küche `decoder: 0 / output: 0` → BUFFERING; 39.1667 esparagus-01 buffer ready → WAITING_TO_SYNC; 39.1694 Arbeitszimmer ready;
   **39.4700 `jumpToTime` (master esparagus-01)** → STOPPED → BUFFERING (the bridge sees `peer-reset` t=466.62 and `GET 206`);
   Arbeitszimmer ready 40.32, esparagus-01 40.66, Küche 41.2858 → PLAYING. Küche was the *last* to refill.
6. The Küche Wi-Fi signal (LMS `signalstrength`) stayed in 71–84 % during test 4, 80→78→76 around the event (l.487–491) and 71 only
   afterwards (4 samples). Küche had 71–84 % in the other runs too. It does not discriminate.

### 2.2 The mechanism, refined by this round (STATIC + earlier INTEGRATION)

* **Why the whole group goes silent.** (reports/G1S_THREE_ROOM_STALLS.md, docs/STREAM_TRACE.md) Any member's output underrun →
  `StreamingController::_Rebuffer`: every player is paused and must refill **5 s** (882 000 B for 1411 kbit/s PCM) from a source that
  can only deliver at real time; `time` freezes; `WAITING` appears in our trace.
* **Why a restart follows (new in this round).** `Slim::Player::Player::_buffering` (Player.pm l.1127–1181):
  a player that has refilled calls `playerBufferReady` and sets `bufferStarted(0)` (l.1178–1181). If the group is still waiting for a
  slower member, `_buffering` is re-armed 0.3 s later (l.1237–1243) and its first test is the "Bug 6712 give up after 30 s":
  `$buffering == 2 && time() > bufferStarted() + 30` — with `bufferStarted() == 0` this is **true at once**, so the first-ready member
  triggers `jumpToTime(playingSongElapsed(), 1)` (l.1154) = stop + re-request the stream, ~0.3 s later. Test 4 shows exactly this:
  esparagus-01 ready 39.1667 → `jumpToTime` 39.4700 (**+0.303 s**) while Küche was still empty. So the restart does not need the 30 s:
  *with heterogeneous buffer fills a rebuffer ends in a stream restart within a fraction of a second after the first member refills.*
  (Lab: the equal-fill Squeezelite members refilled together, hence the earlier lab runs recovered on the same connection.)
  Not lab-reproduced yet; see falsifier F2.
* **Cost of any underrun on this source.** Refilling 5 s of audio at real time takes ≥4 s (test 4: 4.04 s from underrun to the first
  ready) plus a ~2 s second fill after the restart: 6.16 s of silence from a single member's underrun. A file would refill in a
  fraction of a second; a live source has no head start.

### 2.3 Tests 1–3: Arbeitszimmer is the working hypothesis — and why (Inferred)

Hypothesis **H-A**: *in tests 1–3 Arbeitszimmer's Squeezelite accepted audio but did not play it at real time (stuck USB audio output
of the RØDECaster Duo or a sick Pi). Its buffers filled; Lyrion's source-queue limit stopped reading for the whole group; the healthy
rooms then drained their small cushion.*

For:

* **Timing signature.** A player that never drains accepts, at 176 400 B/s: Squeezelite's default stream buffer (2048 KiB = 11.9 s) +
  output buffer (3446 KiB at 8 B/frame = 10.0 s) = 21.9 s, plus Lyrion's queue and TCP buffers (a few s) = **~25 s** — observed
  24.4 / 25.3 / 25.6 s. Assumes default `-b` and that Max2Play did not change it (to be verified from its command line).
* **Cycle.** After `jumpToTime` flushes the players the same member refills from empty: next block ~32–38 s after the restart,
  ~63 s per cycle (31 s fill + 30 s Bug-6712 wait).
* **First underrun ≈1 s after the first block** (test 1: block ≈124.45, underrun ≈125.44): the healthy members hold only ~1 s of cushion
  (a live source cannot pre-fill), they run dry as soon as Lyrion stops reading.
* **Operator**: the office was inaudible before the first stall in test 1, ping answered but ssh refused; a power-cycle fixed audio.
* **Clamped time (tests 2, 3).** Lyrion's `time` comes from the *sync master's* player status (`playingSongElapsed` uses `master()`;
  Squeezebox2.pm `songElapsedSeconds`: master STAT `elapsed_milliseconds` + extrapolation). In tests 2/3 the first-listed member
  (= bridge target) is Arbeitszimmer; if it was master and reported nonsense elapsed values (e.g. an unsigned underflow from a
  misbehaving ALSA delay), Lyrion clamps to `duration`. **Unknown: who was master in tests 1–3.** The trace now marks the master.

Against / not explained:

* In test 1 Arbeitszimmer was also a member, yet `time` was normal (so master ≠ Arbeitszimmer there, or its elapsed was fine).
* Küche is the first underrunner in 3 of 3 runs that contain it (tests 1, 3, 4). That is consistent with "Küche has the smallest
  cushion" (original Squeezebox decoder buffer) and with a Küche-specific problem — it cannot be separated with the data at hand.
* Nothing here measures what Arbeitszimmer's ALSA output did. **H-A is an inference from timing and operator observations, not a measurement.**

**Falsifiers.** F1 — three rooms with a rebooted Arbeitszimmer fail again at ~25 s: H-A is wrong (or the fault returned).
F3 — Arbeitszimmer's Squeezelite log/`aplay`/`dmesg` after a failing run shows a healthy USB device: H-A is wrong. A *pass* of runs 5–6
(§5) supports "Arbeitszimmer's state" but cannot prove a USB/Squeezelite/network root cause.

### 2.4 The 2^32 anomaly (`bytes=4294967336-`, test 2/3) — resolved by code, not a bridge defect (STATIC + UNIT)

* The bridge computes nothing: it sends the WAV header with data size `0xFFFFFFFF` (`stream/encoders.py:11`) and echoes the requested
  start in `Content-Range: bytes <start>-4294967294/4294967295` (`stream/server.py:166–173`).
* Lyrion derives `duration = 4294967295·8 / 1 411 200 = 24347.887 s` from that length (OPEN_QUESTIONS #104).
  `getSeekData` (Protocols/HTTP.pm l.1190–1208): `int(176400 · t)`, minus `% block_alignment`, plus `audio_offset` 44. For
  `t = duration`: `int(176400·24347.887)` = 4294967295 → −3 (4-byte alignment) → 4294967292 → **+44 = 4294967336. Exactly the observed
  value (verified numerically).** So the range is the *consequence* of a Lyrion time clamped at the duration
  (`playingSongElapsed`, StreamingController.pm l.1736–1739: `songtime > duration → duration`), nothing the bridge picked.
* **Feedback loop.** Lyrion takes `startOffset` from our Content-Range start (HTTP.pm l.757–795): `startOffset = duration · start / length`
  ≈ duration → time stays clamped for every later restart (tests 2/3: all of l.27–230). Test 4's restart at 21235252 gives 120.4 s —
  the same code with a sane time.
* **It is not the stall's cause**: test 1 stalled at the same 25 s with a normal time (24.4 s). It is a *marker* that the master's elapsed
  time was already wrong in tests 2/3 (H-A).
* **Decision: no code change to the range semantics.** A `416` for start > end would end Lyrion's recovery; changing the echoed start
  would change what Lyrion does with every Range (FINDINGS 2, OPEN_QUESTIONS #104). Pinned by new UNIT cases
  (`tests/unit/test_stream_continuity.py`: `bytes=4294967294-`, `4294967295-`, `4294967336-`, `4294967336-4294967294`: 206, live fresh PCM,
  echoed Content-Range, never a Content-Length). The reversed header for start > end is formally odd and is documented as such.

### 2.5 The group target change at 15:23:34 (t=341) (Observed / Unknown)

* Observed (l.356–364): `target_change` → `session_release` → new session `h9kaPS2J`: `GET 200` (header scan, 0.02 s, `peer-reset`) and
  `GET 206 bytes=44-` with `lead=1000ms`. Lyrion's `time` went 328.7 → **0.0** (a new song; the old one was `rDQz-KNv`, 330 s).
  The `members:` order changed (target Arbeitszimmer → esparagus-01), i.e. the target is now the group's **master** esparagus-01
  (the master in the Lyrion log at 15:25:39). During the next 121.5 s no `blk`, `WAITING` or `DISC`.
* Not shown: any causal link to the underrun 121 s later. Candidates (all Inferred, none tested): (a) coincidence — Lyrion rebuffers
  happen at random once any member's cushion is exhausted; (b) the new stream has `lead=1000 ms` and a fresh sync start → different
  cushion per member than the first, 330-s stream, which survived without an underrun; (c) master change affects which
  player's STAT drives sync adjustments. Distinguishing needs two runs: same group, target = master from the start, vs. the
  old target. Not worth a code change.

### 2.6 What the bridge can and cannot see

Can: PCM arrival and level, bytes passed to `sendall` and write blocking, Lyrion's view of the **target** (`mode`, `time`, `waitingToPlay`),
the members' Slimproto connection and Wi-Fi signal. Cannot: any member's decoder/output buffer fill, whether a member's DAC makes
sound, the Lyrion → player path (Wi-Fi/TCP), which member underran (only Lyrion's `player.source` log says so).
Consequently **a trace saying `ok`/`play`/`playerBufferReady` for a member proves nothing about audible output.**

### 2.7 Is the remaining underrun outside the bridge code?

Test 4: yes, to the extent the evidence allows (bridge wrote at full rate, no write block, input healthy; Küche's buffers were empty).
Not provable: a live source gives members a ~1 s cushion; whether Küche's underrun was a delivery hiccup (Wi-Fi/TCP stall > cushion),
Lyrion scheduling or clock drift (121 s after a fresh stream is too short for ppm-level drift to empty a 1 s cushion unless the cushion
was much smaller) is open. **A larger cushion is a bridge-side lever** (`lead_in_ms`, default 1000, up to the 6 s ring) that would shorten every
rebuffer and tolerate longer hiccups, at the price of added latency and a measurable trade-off in guest UX; it is *not* a hide-the-
telemetry fix, but it is not justified by a measured trigger yet and is left as a decision for the owner after run 5.

## 3. Code changes in this package (diagnostics only; opt-in; no product behaviour)

| File | Change |
|---|---|
| `bridge/btbridge/lms/client.py` | `group_status` adds `master` (the sync master) per member |
| `bridge/btbridge/stream/trace.py` | `members:` prints `Name:ok(master)` |
| `tests/unit/test_trace_lms_fields.py` | master marking incl. target = slave |
| `tests/unit/test_stream_continuity.py` | Range cases at/over 2^32 (G1-S tests 2/3) |
| `tests/integration/test_14_stream_continuity.py` | members regex accepts `(master)` |
| docs | `docs/STREAM_TRACE.md`, `docs/HARDWARE_TEST_PROCEDURE.md` §8, `docs/OPEN_QUESTIONS.md` #105/#106, `docs/STALL_ALERT_DESIGN.md`, `docs/PAIRING_CODE_FEASIBILITY.md` |

Deliberately **not** changed: HTTP handler, ranges, ring/lead-in, reconnect behaviour, health flags, BlueZ, pairing.

## 4. Other deliverables

* Diagnostic alert design (feature A): `docs/STALL_ALERT_DESIGN.md` — design + test plan only, nothing implemented.
* Pairing-code feasibility (feature B): `docs/PAIRING_CODE_FEASIBILITY.md` — no prototype run (needs the Pi and the Pixel);
  the operator-run prototype plan is in it. No claim that a phone-side static code works.

## 5. Next experiment and matrix (HARDWARE_TEST_PROCEDURE §8)

One variable per run, Arbeitszimmer stays in the rebooted state, no mid-run changes, `player.source` INFO, `python3 -u`:

| Run | Group / target | Question |
|---|---|---|
| 5 (**the single next experiment**) | all three; target = the group master from the start; 15 min | does the Küche underrun recur without the target change; who underruns; who is master |
| 6 (if 5 fails with Küche first) | Küche + esparagus-01 (the 11-min control) with this build | does Küche underrun without the third member |
| 7 (if 5 passes) | the same group, target = Arbeitszimmer (as in tests 1–3's target) | does the old target matter |
| 8 (only for H-A, owner's choice) | Arbeitszimmer left unrebooted *only if it is reproducibly faulty again* | F1 |

Pass per run: 10 (15) min audible in every room, no `STUCK`, no `WAITING` after the first 10 s, no unplanned `rN start GET`, all
`members:` `ok`. G1-S passes only with a fresh three-room run meeting this.

## 6. Cleanup status to confirm

* `player.source` was reset to `ERROR` by the operator (JSON-RPC `result: {}`; acknowledged, not read back). Lyrion keeps no persistent
  log level: a Lyrion restart also clears it.
* ~~The Home Assistant automation `automation.wohnzimmer_yamaha_aus_bei_abwesenheit_und_lyrion_quelle` is still disabled for the test.~~
  **Superseded 2026-11-26:** the operator read the live state after Run 11 - it is **ON** again (as is `automation.kuche_verstarker_aus`). It was never touched from here.
* Bridge was stopped after test 4; no test is presumed running.
