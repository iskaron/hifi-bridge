# G1-S — hardware runs 6–11 (two-room validation), CI status, next step

Date 2026-11-26. Gate **G1-S is OPEN** (no fresh three-room run has passed). Nothing in this document changes product behaviour.
Scope per the operator: no MQTT, ICY/title metadata or pairing work yet; **no new hardware test is requested right now**.

## 0. What this is based on (read first)

| Source | Status in this session |
|---|---|
| Handoff of 2026-11-26 (Runs 6–11, WLAN change, HA state) | taken as **operator-reported / operator-inspected**. |
| Raw bridge logs `g1s3-run10.log`, `g1s3-run11.log` | **not on the development host** — I did *not* re-derive any number below from the raw files. |
| Lyrion `server.log` for Run 11 (14:20–14:52), ESP32 ping log | **not available** (optional per the operator). Run 10's Lyrion extract was inspected by the operator. |
| Lyrion 9.1.2 source in the lab container (`/lms`) | read by me (**STATIC**). |
| CI run `45640242166`, both attempts | read by me through `scripts/gh_api.sh` (**CI**). |

Labels: REAL-HARDWARE = from the Pi/players as reported; STATIC = source reading; UNIT/INTEGRATION = this repository's Docker tests.

## 1. Runs 6–11 — strict verdicts

Strict technical rule = `HARDWARE_TEST_PROCEDURE` §8 (after the first 10 s: no `STUCK`, no `WAITING`, no unplanned new GET, every member `ok`).
"Listening" is the operator's report and is **kept separate**. Runs 6–11 are **not** the three-room G1-S case; a two-room pass would be
*two-room validation* only. Solo runs are controls, not G1-S evidence.

| Run | Rooms (master) | What the trace showed (REAL-HARDWARE, as reported) | Listening (reported) | Strict technical | Counts for G1-S |
|---|---|---|---|---|---|
| 6 | two rooms, **no** office | startup `WAITING`, HTTP backpressure, `STUCK`; long stable period afterwards (timing vs the 10-s grace not given) | not stated | **FAIL** (as reported) | no (2 rooms) |
| 7 | esparagus-01 solo | stable after initial buffering (~17:00); no `STUCK`, no post-start `WAITING`, no extra GET | n/a | **PASS (control)** | no |
| 8 | Küche solo | stable (~18:02); no `STUCK`/`WAITING`/extra GET, no dropped PCM frames | n/a | **PASS (control)** | no |
| 9 | Küche + Wohnzimmer; **actual master = Wohnzimmer** (the intended switch had not happened) | audible dropout ≈ t=1000 s, later `WAITING` and `STUCK` | dropout heard | **FAIL** | no |
| 10 | Küche (master) + Wohnzimmer; 25:45 on one GET | 09:04:46.6 `playerOutputUnderrun` Wohnzimmer (`decoder 7200 / output 0`), group `BUFFERING` 3.44 s (→ `PLAYING` 09:04:50.09), PCM 44.1 k/s, HTTP lag 0 ms; **separately** ≈09:08:47–54 five `STUCK` samples (lag ≤4420 ms, write block ≤2395 ms), Lyrion stayed `PLAYING`, no related server.log event; ≈1628 PCM frames (~37 ms) dropped near the end; no new GET, no BT reconnect | no interruption | **FAIL** (`WAITING`+`STUCK`) | no (2 rooms) |
| 11 | Küche (master, confirmed by the `(master)` marker) + esparagus-01; main GET 1879.94 s | **start:** stream t=79.28, `WAITING` ≈t=86 at play time 4.0 s, six `STUCK` t=87.07–92.08 (lag ≤5740 ms, block ≤6321 ms, `skip=14112fr` ≈320 ms), esparagus-01 `DISC` t=93–96 (signal still 77), peer-reset t=95.18 + **new `GET 206 bytes=844532-`**, `WAITING` again t=108–112. **t=433.45–437.46:** five `WAITING`, play time pinned at 333.5 s, same GET, `lag` ≤720 ms, `blk` 1023 ms, no `STUCK`/GET/`skip`. **t=438.46–1975.13 (≈25 m 37 s): 0 `WAITING`, 0 `STUCK`, 0 GET.** ≈1213 frames (~27.5 ms) dropped at t≈1929–1961 | no dropout during t=230–1900 (27 m 50 s) | **FAIL** (STUCK, unplanned GET, member `DISC`, 2 `WAITING` episodes) | no (2 rooms) |

Run 11's `(master)` marker is also the first REAL-HARDWARE evidence that the round-3 diagnostic works (Run 9 showed why it is needed).

## 2. Run 10 vs Run 11, time-aligned by objective rules

| Rule | Run 10 | Run 11 |
|---|---|---|
| Group | Küche master, Wohnzimmer follower | same |
| Wohnzimmer WLAN association | RepeaterKüche, 67 Mbit/s PHY (before the single reboot of esparagus-01) | RepeaterWohnzimmer, 130 Mbit/s PHY |
| Main stream | 1545 s, one GET | 1879.94 s, one GET **after** the startup restart |
| `WAITING` after the first 10 s | ≥1 episode (3.44 s) | 2 episodes (≈4 s at t≈108; 5 s at t≈433) |
| `STUCK` | 5 samples at ≈09:08:47–54 (≤2.4 s block) | 6 samples at startup (≤6.3 s block) |
| Lyrion-confirmed output underrun | **yes**, Wohnzimmer, 09:04:46.6 | **unknown** (no Lyrion log) — do not infer from `WAITING` |
| Unplanned new GET | 0 | 1 (startup, t=95.19) |
| Member `DISC` | none | esparagus-01, t=93–96 (Lyrion's view, **not** verified as Wi-Fi disassociation) |
| Verified master | Küche | Küche (`(master)` marker) |
| PCM dropped | ~37 ms | ~320 ms (`skip`, startup) + ~27.5 ms (tail) |
| BT capture | uninterrupted | uninterrupted |
| Listening | no interruption | no interruption (t=230–1900) |

What the comparison does **not** show: an improvement from the WLAN change (events per run: 2 vs 3; n=1 each, different start conditions —
Run 11 followed a reboot of esparagus-01). PHY link rate is not throughput.

## 3. Causal assessment

### Confirmed
* **The bridge delivered audio throughout.** In Run 10 at the underrun: 44 100 PCM frames/s, HTTP lag 0 ms. In Run 11 the long tail ran 25 m 37 s clean on one GET; PCM was captured continuously through every stall.
* **`WAITING` is a group state.** STATIC (Lyrion 9.1.2, `Queries.pm:4082`, `StreamingController.pm:1676–1679`): `waitingToPlay=1` ⇔ the controller is playing but not *really* playing, i.e. `playingState` is `BUFFERING` or `WAITING_TO_SYNC`. After an underrun `_Rebuffer` pauses the whole group (`StreamingController.pm:1659`, "pause(noFadeOut) -> Buffering(rebuffering)"). A pinned play time (Run 11: 333.5 s for 5 s) is that pause.
* **The office player is not a necessary condition.** Runs 6, 9, 10 and 11 had no Arbeitszimmer and still show `WAITING`/`STUCK`.

### Plausible (Inferred — each has a falsifier)
* **P1 — a live source leaves almost no cushion.** The bridge can only deliver at capture rate, so members hold roughly the lead-in (~1 s; Run 10: `decoder 7200 / output 0` ≈ 40 ms). Any ≥~1 s delivery hiccup to *one* member underruns it, and Lyrion then pauses *everyone* until the 882 000-byte refill threshold (5 s of 1411 kbit/s PCM) is met, which a real-time source needs ≥4 s for. *Falsifier:* the Lyrion log shows the underrun while data to that member was arriving normally (queue not drained by the player for another reason), or the event rate does not change after a larger cushion (only ever as a single-variable test, see §5).
* **P2 — a member's data channel stalls and Lyrion stops reading for the whole group.** STATIC: `Source.pm` "Bug 14117" (`QUEUED_CHUNKS_HWM=10`) stops reading when one member's queue is full, which the bridge sees as `STUCK`. Fits Run 11's startup (six `STUCK`, then Lyrion's own restart at the same instant esparagus-01 shows `DISC`) and Run 10's 09:08 `STUCK`. *Falsifier:* Lyrion log at 14:20–14:22 shows Küche, not esparagus-01, as the member that stopped; or a continuous ping to `.167` shows no loss at t=87–96.
  * Ordering matters: `STUCK` starts t=87.07, `DISC` t=93 — `DISC` is *after* the stall, so it is a consequence or a coincident symptom, not the established trigger.
* **P3 — the two-room group is intermittently fragile on this Wi-Fi; the office player is not special.** The rate is not stable (Run 10: two events in 25 min; Run 11: two in the first 6 min after stream start, none for the next 25 min). No periodicity is visible; n is too small to say more.

### Unresolved
* Why Wohnzimmer's output underran at 09:04:46 (Run 10); the cause of the 09:08 `STUCK`.
* Whether Run 11's `WAITING` at t≈433 was an output underrun and on which player (needs Lyrion `playerOutputUnderrun` lines for 14:26:12).
* The startup stall in Run 11 (see P2).
* Whether the WLAN association change helped (n=1 each).
* **Listening vs trace.** Run 10's 3.44 s and Run 11's 5 s group `BUFFERING` are, by the source (STATIC above), periods in which Lyrion had paused the group, yet the operator heard no dropout. Both reports are recorded as given; the subjective result **cannot** overrule a logged pause and the pause cannot be shown to be inaudible. The Lyrion log for those minutes resolves it (does it show `_Rebuffer`/`playerOutputUnderrun`/`jumpToTime` at 14:26:12?). *Falsifier of the technical reading:* the log shows no group state change at that time — then `waitingToPlay` was a status artefact and the trace rule needs refinement.

### Retraction of an earlier statement
The Round-2 report named an unhealthy Arbeitszimmer player as the *working hypothesis for tests 1–3*. That remains **unproven** and is **not** a general explanation (above). It is not the established root cause of anything; `docs/OPEN_QUESTIONS.md` #105 now says so.

## 4. Acceptance criteria (`HARDWARE_TEST_PROCEDURE` §8) — what is still missing

| Criterion | Best evidence so far | Status |
|---|---|---|
| all three physical rooms audible ≥10 min | Round-2 test 4: 605 s, one ~6 s group dropout (three rooms) | **not met / not repeated** |
| no `STUCK`/`WAITING` after the first 10 s | Runs 10 and 11 (two rooms) both break it | **not met** |
| no unplanned new GET | Run 10 ok; Run 11 one at startup | **not met** (Run 11) |
| every member `ok` | Run 11: esparagus-01 `DISC` 3–4 samples | **not met** (Run 11) |
| fresh **three-room** run | none since the Arbeitszimmer power-cycle with the `(master)` marker | **missing** |

Missing for closure: (1) an uninterrupted three-room run after the office player is shown to play audio on its own (the handoff lists no
Arbeitszimmer-solo run — if one exists, it should be cited); (2) the Lyrion log for the minutes in §3 to separate P1/P2; (3) an
explanation for the startup stall that does not rely on one sample.

## 5. Next experiment (only if the operator chooses; nothing is requested now)

One variable at a time; bridge code, BlueZ, WLAN, bonds and ESP32 buffer sizes unchanged.

1. **E1 — zero-variable repeat of Run 11 with full evidence.** Same two rooms, Küche master, same association, `python3 -u`, `HIFI_STREAM_TRACE=1` (procedure §8 step 3). Add the two passive artefacts that are missing: Lyrion extract (procedure §8 step 5, its `sed` removes the stream token) and a timestamped `ping -D -i 0.5` to `192.168.0.167` and `192.168.0.180` from the bridge Pi (ICMP only; a normal ping is *not* proof of clean audio delivery). Purpose: does the **startup** stall repeat (n=2) and which member does Lyrion blame.
2. **E2 — only if E1 shows P2 with a named member:** swap that *one* member for another room (e.g. Küche + Arbeitszimmer), after a 10-min **solo** run of Arbeitszimmer with the trace on.
3. **E3 — three rooms** (Küche master from the start, no target change) only when E1/E2 explain the two-room events. Pass rule unchanged.
4. A bridge-side cushion (`lead_in_ms`, default 1000) is **not** proposed: no evidence yet that cushion size is the variable. It is listed as a candidate *after* E1 supports P1 (OPEN_QUESTIONS #106).

## 6. CI — demo job

| Attempt | Commit | Result | Notes |
|---|---|---|---|
| 1 (2026-11-25 15:00 UTC) | `48f0d0f` | 16/17, **`demo` failed** | `test_08`: `{'offset_s': 24.3, 'phone_to_dac_s': 1.05, 'score': 0.49}` |
| 2 (GitHub-UI rerun, 15:39 UTC, triggering actor type *User*) | `48f0d0f` | **17/17 success** | same SHA, no new commit |

* **Same code, different result ⇒ a nondeterministic test, not a regression from `48f0d0f`.** STATIC: `group_status` (the only changed runtime function) is called only when `HIFI_STREAM_TRACE` is set (`app.py:170`); neither `demo/` nor the workflow sets it.
* **Signature** (CI log + measurement history): in the 12 earlier recorded runs `multiroom_schlafzimmer` is always `phone_to_dac_s` 2.21–2.33 s, `score` 1.0. The red run had 1.05 s and 0.49 — Schlafzimmer played audio ≈1.2 s *fresher* than the aligned group and the capture matched only partly. That is what a capture taken while the joining player is still being aligned looks like.
* **Test weakness:** `test_08` waits a fixed `time.sleep(6)` after `/api/lms/sync` ("Lyrion re-buffers the group") and then captures 3 s. Whether 6 s is enough depends on runner load.
* Status of the rerun: done by the user in the GitHub UI; I did not and cannot trigger it (the token lacks `actions=write`, HTTP 403). No commit was made to trigger CI.

Local reproduction and the test change are in §7.

## 7. Demo test: reproduction and fix

* **Reproduction (INTEGRATION, `scripts/test_demo.sh`, own project `hifi-demo-test`, the owner's `hifi-demo` untouched):** 3 consecutive full runs on the unchanged test: **3/3 pass** (`multiroom_schlafzimmer` score 0.988–1.0). Together with 12 recorded passes and CI attempt 2, the failure was observed **1 time in ≈17**. **Not reproduced → root cause not proven**; the table in §6 is an inference.
* **Change (test only, no product code):** `tests/demo/stack/test_2_chain.py` — `time.sleep(6)` is replaced by `wait_group_settled("Schlafzimmer")`: capture only after Lyrion reports the player `play`, not `waitingToPlay`, with a `sync_master`, and with its play time advancing for 3 s (45 s timeout). The assertion (`score > 0.9`) is unchanged. This removes a known timing assumption; it cannot be *proven* to cure a ≈1-in-17 flake, so a recurrence must be investigated, not retried.
* **Verification after the change:** full `test_demo.sh` rc=0 (11 chain tests, score 1.0, `phone_to_dac_s` 2.3). CI for the new commit is reported separately.
* Gate wording: CI on `48f0d0f` is **green on rerun (17/17)**, with one unexplained red attempt on the same SHA. It is not "never failed".

## 8. Housekeeping

* Home Assistant, verified by the operator on 2026-11-26: `automation.kuche_verstarker_aus` ON; `automation.wohnzimmer_yamaha_aus_bei_abwesenheit_und_lyrion_quelle` **ON**. The earlier "disabled" statements in the procedure and the Round-2 report are marked superseded. Nothing here toggles either automation.
* Untouched user files: `docs.zip`, `hifi-bridge-g1-3room-evidence.zip`, `hifi-bridge-g1s2-evidence-2026-11-25.zip`; the already-modified `measurements/demo_*.json` and demo screenshots (rewritten by every demo run) are not committed.
