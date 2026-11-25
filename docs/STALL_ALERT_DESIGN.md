# Audio-stall alert — design and test plan (feature A; NOT implemented)

Status: proposal after G1-S round 2 (reports/G1S_ROUND2_ANALYSIS.md). Needs owner approval before implementation; no API, MQTT or UI
change exists yet. All signals below already exist in the opt-in trace (docs/STREAM_TRACE.md) — the work is to evaluate them in the
product (always on, cheap) instead of only printing them.

## 1. What can and cannot be claimed

The bridge sees PCM in, bytes written to Lyrion, Lyrion's `waitingToPlay`/`mode`/`time` for its target, and member connection state.
It does **not** see any player's buffers or whether a speaker makes sound. UI text therefore says *"playback may be interrupted"* and
names what was observed; it never says *"silence detected"*, *"player X is broken"* or *"audio OK"*. A standing footnote on the card:
*"Output at the players is not measured."* A member listed as connected is not evidence that it is audible.

## 2. Inputs (all sampled at 1 Hz from existing providers)

| Signal | Source today | Meaning |
|---|---|---|
| `pcm_live` | `in:` real frames/s and `nz` (feeder `real`, `silence`) | phone audio arriving; `nz≈0` for ≥N s = source silent |
| `http_flow` | `out:` bytes/s, `blk`, `STUCK` | bytes reach Lyrion; `STUCK` = Lyrion stopped reading |
| `lms_waiting` | `Observation.waiting` (`waitingToPlay`) | Lyrion paused the group (BUFFERING / WAITING_TO_SYNC) |
| `lms_time_frozen_s` | `Observation.time` not advancing while `mode=play` and not waiting | Lyrion's clock stuck (e.g. clamped at the duration, test 2/3) |
| `poll` | `controller.last_poll` | `lms-unreachable` / `target-disconnected` |
| `members` | `group_status` (2 s, own thread) | name, connected, Wi-Fi %, master |
| `phase` | controller | `session_start`, `target_change`, restart `GET 200→206` |

## 3. States (one cause list per episode, strongest first; "suspected" until a second signal confirms)

| Id | Condition (all for ≥ `stall_warn_after_s`, default **5 s**, range 3–60) | Confidence | Message (EN) | Message (DE) |
|---|---|---|---|---|
| `lms_waiting` | `pcm_live` ∧ ¬`STUCK` ∧ `lms_waiting` | confirmed (Lyrion says so) | Playback may be interrupted. Lyrion is waiting for synchronized playback (a player ran out of buffered audio). The Bridge's input is still healthy. Check each player's audio output and connection. | Die Wiedergabe ist evtl. unterbrochen. Lyrion wartet auf synchronen Start (ein Player hat keine Daten mehr gepuffert). Der Bridge-Eingang ist weiterhin in Ordnung. Audioausgang und Verbindung jedes Players prüfen. |
| `http_stalled` | `pcm_live` ∧ `STUCK` (write blocked > 1 s) | confirmed | Lyrion has stopped reading the Bridge's stream while the phone is still sending. Usually a player in the synchronized group is not taking audio. | Lyrion liest den Stream der Bridge nicht mehr, obwohl das Handy sendet. Meist nimmt ein Player der Sync-Gruppe kein Audio mehr an. |
| `member_disconnected` | any member `DISC` or `poll=target-disconnected` | confirmed | A player of the group is not connected to Lyrion. | Ein Player der Gruppe ist nicht mit Lyrion verbunden. |
| `lms_unreachable` | `poll=lms-unreachable` | confirmed | Lyrion does not answer the Bridge. | Lyrion antwortet der Bridge nicht. |
| `lms_time_frozen` | `mode=play` ∧ ¬waiting ∧ `time` frozen ∧ `pcm_live` | suspected | Lyrion's playback clock is not advancing. | Die Wiedergabezeit in Lyrion läuft nicht weiter. |
| `source_silent` | phone connected & playing ∧ `nz≈0` (≥ `source_silent_after_s`, default 10) | suspected (a paused/quiet track looks the same) | The phone is connected but delivers no audio signal. | Das Handy ist verbunden, liefert aber kein Audiosignal. |

Never raised: any state for a member by name as "the culprit". Member names appear only as *"members: …"* facts. Wi-Fi % is shown as a
fact, not a diagnosis. Which member underran is only in Lyrion's own log; the report offers the exact log command (§6) instead.

## 4. Debouncing and false positives

* **Grace** of 10 s after `session_start`, `target_change` and every restart (`GET 200→206`): a normal start shows `WAITING` for ~2 s
  (test 1, l.30–31). Conditions are evaluated only after the grace.
* A condition must hold **continuously** for `stall_warn_after_s` (default 5 s, configurable, unit-tested at 3/5/60); a 1-s `blk` is not a stall.
* **Episode** = a set of causes from first abnormal second to 10 s of all-clear. Test 4's event (WAITING 15:25:35 → clear 15:25:41, 6 s)
  yields: banner at +5 s, "recovered after 6 s" at +16 s (timeline only, banner dismisses after 60 s).
* **Repeating** = ≥2 episodes in 10 min → banner text gains "repeated N times in M min" and severity rises (tests 2/3: three in 3 min).
* No alert while the bridge is idle, paused, or the phone is not connected.
* An episode that ends by Lyrion's own restart (peer-reset + new 206) stays one episode; the restart is a timeline event, not a second alert.

## 5. UI

* Dashboard card (existing look/i18n pattern): a persistent banner while an episode is active (warning colour), recovery line afterwards,
  "Details" expands the evidence (pcm/out/lms/members as in the trace, no URLs/tokens/MACs).
* Timeline (last 50 events, ring in memory + `bridge-data/diag-events.json`, rotated): state transitions with time since first abnormal.
* **Diagnostic report**: "Copy report" / download `.txt` — episode list + the last 120 trace lines (same redaction as the trace) +
  the Lyrion command from HARDWARE_TEST_PROCEDURE §8 step 5. No auto-upload.
* Actions shown (text only): check each player's audio output/USB device; check Wi-Fi of players; look at Lyrion `server.log` with the shown command; "if
  one player has been silent since the start of playback, restart *that* player yourself".
* **Never automatic**: no player reboot, unsync, queue change, source override, reconnect of the stream.

## 6. Telemetry contract

`GET /api/status` gains `audio_health` (no new endpoint; same auth as status):

```json
{"state": "ok|warning|recovered", "since": 1764084335.1, "first_abnormal": 1764084335.1,
 "causes": [{"id": "lms_waiting", "confidence": "confirmed", "seconds": 6.2}],
 "episodes_10min": 1, "pcm_live": true, "http_flow": true,
 "lms": {"waiting": false, "time_frozen_s": 0},
 "members": [{"name": "esparagus-01", "connected": true, "wifi": 80, "master": true}],
 "output_measured": false}
```

MQTT (optional, **off by default**, HA discovery as for the other entities): binary sensor `audio_health_problem` (+ attribute `causes`),
state changes only, rate-limited to one notification per 5 min per cause set, payload without names/IPs unless the owner enables names.
No Home Assistant is needed for the WebUI card. `output_measured` is always `false` until a sensor exists.

## 7. Test plan (UNIT with fake clock and scripted inputs; INTEGRATION in the lab)

| Scenario | Inputs | Expected |
|---|---|---|
| normal start | `WAITING` 2 s after `session_start` | no alert (grace) |
| temporary startup buffering | `WAITING` 4 s inside grace / 4 s outside | none / none (<5 s) |
| Lyrion WAITING, PCM flowing (test 4) | `lms_waiting` 6 s, `blk` ok | banner at +5 s `lms_waiting`; recovered at clear+10 s |
| stalled HTTP (tests 1–3) | `STUCK` 30 s then `peer-reset` + 206 | `http_stalled` (+`lms_waiting`); one episode across the restart |
| repeated | 3 episodes in 3 min | "repeated 3×" |
| silent phone | `nz=0` 12 s, phone playing | `source_silent` (suspected), nothing about Lyrion |
| target change | `target_change` then WAITING 3 s | grace, none |
| member offline | `DISC` 6 s | `member_disconnected`; message names the fact, not a culprit |
| Lyrion down | `lms-unreachable` 6 s | `lms_unreachable` |
| recovery | clear 10 s | `recovered`, banner dismissed after 60 s, timeline keeps it |
| privacy | any state | no URL/token/MAC/IP in status, report, MQTT |
| MQTT opt-in | disabled / enabled | nothing published / one message per 5 min |
| integration (lab) | 40 s member data stall (`scripts/exp_group_member_datastall.sh`); 5 s stall (`… 30 5`) | 40 s: `http_stalled` + `lms_waiting` within 5 s, a restart event, one episode; 5 s: no persistent alert |
