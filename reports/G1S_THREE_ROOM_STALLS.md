# G1-S round 2 - three-room stalls: Lyrion's sync-group rebuffering (2026-11-25)

Branch `develop`, starting point `3a74653` (verified HEAD = origin/develop before this work). Hardware gate **G1-S: NOT passed**.
Evidence labels: REAL-HARDWARE (owner's Pi trace), STATIC (source read: bridge and Lyrion 9.1.2 in the lab image; the owner runs 9.1.1),
INTEGRATION (Docker lab: real Lyrion 9.1.2 + Squeezelite, simulated Bluetooth; not Wi-Fi, not acoustic).

## Result in one paragraph

The three-room silences are **Lyrion's sync-group rebuffering**, not a bridge or HTTP failure. One member's output underrun makes Lyrion
pause **every** room. Lyrion then stops reading from the bridge because a member takes no data. If the group cannot refill within
**30 s**, Lyrion gives up and requests the stream again at the frozen elapsed offset (the new `GET … 206 range=bytes=<large>-`).

The lab reproduces the Pi signature exactly by stalling the data connection of **one** member while it stays "connected". The bridge
kept capturing and offering live audio throughout. **Which** member underruns on the Pi, and why, is not yet known: the bridge
cannot see it. The next run names it (Lyrion's `player.source` log plus the new trace fields). No product fix is justified yet.
Only opt-in diagnostics were added.

## Pipeline (STATIC; lab-confirmed where noted)

Pixel → A2DP → BlueZ/PipeWire → the bridge's capture child → `LiveFeeder` (real frames, silence fill only while a session needs it)
→ `PcmRing` (6 s, one position per reader) → `StreamServer` (WAV over HTTP; `206` + `Content-Range: bytes N-4294967294/4294967295`
for `Range: bytes=N-`, header skipped for N ≥ 44, fresh live PCM from the live edge, ≤ 1 s lead) → **Lyrion**. Lyrion proxies the
stream itself (lab log: `Opening stream (no direct streaming)`, `wav->pcm`) and feeds every member from **one** source read
(`Slim::Player::Source::nextChunk`).

`peer1` is the single address that made every request in the Pi trace (r16-r22), with one connection open at a time. That is
Lyrion's server, not the three players (REAL-HARDWARE + STATIC). The players never connect to the bridge.

The `GET 200` with WAV header, followed 40 ms later by `GET 206 range=bytes=44-` for the same session (t=5663.95 / 5663.99), is
expected. Lyrion scans the header (closed after 14668 B), then streams from the audio offset 44. The lab and the 150 s baseline
behave identically.

## What the trace fields mean (STATIC, `bridge/btbridge/stream/trace.py`, `server.py`)

| Field | Meaning | It does NOT show |
|---|---|---|
| `out: rN:<B>` | bytes `sendall()` handed to the kernel in that second for request rN (176400 B/s at 1x) | that Lyrion or any player consumed them |
| `blk` | longest single socket write in the second; with `STUCK`, a write started > 1 s ago has not returned (kernel send buffer full = the peer is not reading) | why the peer is not reading |
| `lag` | how far this reader is behind the ring's live edge, computed live | audio age at the speaker |
| `skip=…fr` | the reader fell a whole ring (6 s) behind and was moved forward (bounded latency, by design) | lost "live" audio: what is skipped is older than 6 s |
| `rN end reason=peer-reset` | the peer closed with RST (Lyrion dropping the source socket) | who decided it (Lyrion's log does) |
| `lms: play time=` | Lyrion `status` `time` = `song startOffset + the master player's reported elapsed`. It is extrapolated **only while the group controller is PLAYING** (`Squeezebox2::songElapsedSeconds`, `StreamingController::playingSongElapsed`). Frozen time with mode `play` therefore means BUFFERING / WAITING_TO_SYNC. Right after a restart it briefly adds the old elapsed to the new offset (64.7/66.0 at t=5613-5614, 50.2-53.2 at t=5722-5725) | bytes consumed |
| new: ` WAITING` | Lyrion's `waitingToPlay` = mode play, controller buffering. Direct confirmation of a rebuffer | which member caused it |
| new: `poll=…` | the last poll of the target failed (`lms-unreachable`) or found the target `target-disconnected` | |
| new: `members:` | every group member by name: `ok`/`DISC`, Wi-Fi `sig=` | per-member buffer levels (only Lyrion's log has them) |

## Observed / Inferred / Unknown

| | Statement | Evidence |
|---|---|---|
| **Observed** | The bridge's own capture delivered ~50 chunks/s of non-zero audio throughout every stall. The feeder wrote ~44100 real frames/s with 0 silence frames. | every `in:`/`feeder:` line t=5583-5612, 5689-5719, 5729-5734, 5740-5749, 5787-5792 |
| **Observed** | Each stall shows a frozen `lms: play time` and a blocked write to `peer1`, within ~3 s of each other. Both orders occur (table below). | trace |
| **Observed** | Two stalls ended after ~31 s of frozen time with `peer-reset` and, in the same millisecond, a `206` whose offset ≈ the frozen time × 176400 + 44. | r16→r17 t=5613.44, r19→r20 t=5720.56 |
| **Observed** | Two stalls ended after ~9-10 s, each with a burst on the old connection, a `reachability unreachable` event and a gap in successful target polls just before the new request. | r20→r21 t=5735.16 (`seen` 1.6-3.6 s at t=5733.65-5735.66), r21→r22 t=5750.34 (`seen` 1.3 s, `skip=119952fr`) |
| **Observed** | During the 30 s stalls the bridge's LMS poll stayed fresh (`seen` ≤ 1.0 s), so the Pi↔Lyrion path worked while Lyrion did not read. | t=5586-5612, 5690-5719 |
| **Observed (lab)** | Stalling only player4's data connection for 40 s reproduces the full chain. The lab Lyrion log shows `playerOutputUnderrun 02:…:04: decoder: 0 / output: 0`, then BUFFERING, healthy members stuck at `decoder 0 / output ≈360 KB` of an 882000-byte threshold, and `jumpToTime` after 31 s. The bridge shows `peer-reset … write-in-progress=30.37s` and `206 range=bytes=4608668-` (= 26.13 s, the frozen time). The trace shows `STUCK … WAITING` with all members `ok`. | `measurements/exp_group_member_datastall_40s.json` |
| **Observed (lab)** | A 5 s stall gives ~4 s of group silence and recovery on the same connection, without a new request, also with an extra 400:600 KB-buffer member. | `…_5s.json`, `…_5s_smallbuf.json` |
| **Observed (lab)** | Three (and four) healthy Squeezelite members ran 150 s (and 80 s) with no underrun, no `WAITING`, and one held connection. | `test_14`, `measurements/stream_continuity_sync_group_150s.json` |
| **Inferred** | The 30 s restarts are Lyrion's rebuffer give-up. | "Bug 6712" in `Slim::Player::Player::_buffering`: `buffering == 2 && time() > bufferStarted + 30` → `jumpToTime(playingSongElapsed(), 1)`; offset from `Protocols::HTTP::getSeekData` |
| **Inferred** | The rebuffer starts with one member's output underrun, and the whole group pauses. | `Squeezebox2.pm` `STMo` → `playerOutputUnderrun` → `_Rebuffer` (no master check: any member) |
| **Inferred** | Lyrion stops reading because one member's queue is full (10 chunks). | `Source.pm` `nextChunk` "Bug 14117" |
| **Inferred** | For 30 s at least one member took **no** data at all, so the group could not reach the 882000-byte threshold. | lab: only a ≥ 30 s member stall reproduced it; 5 s did not |
| **Inferred** | The 9-10 s restarts (r21, r22) were triggered by a member (re)connecting, not by the 30 s timer. | the lab 20 s SIGSTOP run (`exp_group_member_stall_20s.json`) restarted on reconnect; the target-poll gaps line up. Not proven: the old trace cannot tell "LMS unreachable" from "target disconnected" (now `poll=`) |
| **Unknown** | **Which member** underran each time, and why: its Wi-Fi, its data TCP connection, its output or decoder, or its buffer settings. | Lyrion's `player.source` INFO log names it |
| **Unknown** | Whether the cause is Arbeitszimmer itself or "any third member" (airtime, Lyrion load). | A/B runs 2 and 3, below |
| **Unknown** | t=5646.6-5656.6: `time` frozen at 63.7 while `out` ran at a full 176400 B/s, then the owner paused. Possibly a rebuffer during which every member still accepted data; whether it was audible is not recorded. | the new ` WAITING` flag will show it |
| **Unknown** | Why the 30 s restart offsets are ~0.6 s past the displayed frozen time on the Pi (exact in the lab). Minor; no effect on a live source. | |

### The five stalls

| # | Conn | LMS time froze | Write blocked from | Order | End | New request | Path |
|---|---|---|---|---|---|---|---|
| 1 | r16 | ≈ t=5582.3 at 31.7 | t=5585.41 | freeze 3.1 s first | t=5613.44 peer-reset, write in progress 28.03 s | r17 `bytes=5710112-` (32.37 s) | 30 s give-up (31.1 s) |
| 2 | r19 | ≈ t=5689.4 at 23.7 | t=5688.65 | write 0.8 s first | t=5720.56 peer-reset, 31.91 s | r20 `bytes=4283740-` (24.28 s) | 30 s give-up (31.2 s) |
| 3 | r20 | ≈ t=5726.0 at 49.8 (time unreliable just after a restart) | t=5729.51 | freeze ~3.5 s first | 656 KB burst at t=5733.65, peer-reset at t=5735.16 | r21 `bytes=4922344-` (27.90 s) | ~9 s, target poll gap (inferred reconnect) |
| 4 | r21 | ≈ t=5740.6 at 31.7 | t=5740.52 | same second | 797 KB burst + `skip` at t=5749.67, peer-reset at t=5750.34 | r22 `bytes=5585960-` (31.67 s) | ~10 s, target poll gap |
| 5 | r22 | ≈ t=5784.1 at 62.4 | t=5787.03 | freeze 2.9 s first | the owner paused at t=5793 | - | - |

The order varies because it only reflects the stalled member's own buffer against Lyrion's queue headroom. When Lyrion simply stops
on its own (CPU, its network), the freeze would follow the block by the players' buffer (~2 s) every time, and the polls would also
fail. That is not what the 30 s stalls show.

All Pi restart offsets are frame-aligned (`(N − 44) % 4 == 0`): Lyrion aligns them to `block_alignment`, and the bridge always starts at
a frame boundary. So there is no misalignment.

## Three players vs. Arbeitszimmer

- **For "Arbeitszimmer-specific":**
  - esparagus-01 alone ran ≥ 10 min, and esparagus-01 + Küche ran 11 min with no underrun.
  - Every three-room start failed after 24-36 s.
  - In the lab, member count alone (3-4 healthy Squeezelite) causes no underrun.
- **For "a third member":** real Wi-Fi airtime and Lyrion host load grow with each member, and the lab has neither.
- **Not decidable from the bridge.** In a sync group Lyrion silences all rooms together, so "which rooms fail together" is always
  "all".
- **The discriminating evidence:**
  - Lyrion's own `playerOutputUnderrun <MAC>` line per stall;
  - the A/B runs **esparagus-01 + Arbeitszimmer** and **Küche + Arbeitszimmer**.
- **Reading the A/B result:**
  - Both fail → Arbeitszimmer.
  - Both pass while three rooms fail → count or load.
  - One fails → that pair (e.g. shared Wi-Fi).

## Bridge HTTP audit (STATIC + UNIT/INTEGRATION; the tests are listed in `reports/G1_STREAM_SILENCE.md`)

- **Blocking and partial writes:** `sendall` under a 60 s socket timeout. A write that cannot finish shows as `STUCK` and ends as
  `write-timeout-60s` or with Lyrion's RST. The thread and the connection slot are released (`test_stream_continuity`).
- **Concurrent 200/206:** each request gets its own reader at the live edge. The scan request's early close does not affect the stream.
- **Range and the fake length:** `bytes=N-` gives `206` + `Content-Range … /4294967295`. Lyrion turns this into a 6.8 h duration
  (OPEN_QUESTIONS #104). It is not involved here: Lyrion restarted with correct offsets and the bridge served them.
- **Replay offsets:** a live source cannot replay, so any offset is served from the live edge with ≤ 1 s lead. Lyrion's players
  restart cleanly (audible ~1 s after each restart on the Pi).
- **Old-client cleanup:** an RST ends the old request at once (`peer-reset`), and the new one starts in the same millisecond.
- **Back-pressure:** the ring bounds the backlog to 6 s (`skip`). On recovery Lyrion gets at most 6 s of stale audio, then live audio.

There is no defect, so nothing was changed. A blind periodic reconnect would only cause more restarts, and a "healthy" flag would
hide a real member fault. A larger start lead (fuller player buffers, more latency) is not justified until the trigger is measured:
it cannot cover a member that takes nothing for 30 s.

## Code changes (diagnostics only, opt-in, off by default)

| File | Change |
|---|---|
| `bridge/btbridge/lms/client.py` | `Observation.waiting` (Lyrion `waitingToPlay`); `group_status(player)`: read-only, every sync member's name, connected flag and signal, with no ids or addresses |
| `bridge/btbridge/core/controller.py` | `last_poll = (monotonic, ok \| lms-unreachable \| target-disconnected)`, set where reachability is already decided; the trace reads it |
| `bridge/btbridge/stream/trace.py` | ` WAITING`, `poll=…`, `members: …` (own 2 s thread; a stale result shows its age; names sanitised) |
| `bridge/btbridge/app.py` | wiring, only when the trace is on |
| `tests/unit/test_trace_lms_fields.py` | 8 tests: parsing, read-only member poll, no ids, poll states in the controller, formatting, thread failure handling, off by default |
| `tests/integration/test_14_stream_continuity.py` | the 150 s three-member baseline also asserts no ` WAITING` after start-up and all 3 members `ok` |
| `testkit/btkit/stall_relay.py`, `scripts/exp_group_member_datastall.sh`, `testkit/btkit/exp_group_member_stall.py` | LAB-only experiment: stall one member's data connection (userspace relay, no NET_ADMIN), optional small-buffer member, Lyrion `time`/`waitingToPlay` per second, lab Lyrion `player.source` log |
| `docs/STREAM_TRACE.md`, `docs/HARDWARE_TEST_PROCEDURE.md` §7, `docs/OPEN_QUESTIONS.md` #105, `docs/HARDWARE_GATE_CHECKLIST.md` | mechanism, new fields, lab evidence 4-5, round-2 procedure and pass/fail gate |

Untouched: BlueZ, pairing, codecs, PipeWire, source priority, volume, ownership logic, the stream bytes. With the trace off, no new code
runs and Lyrion gets no extra requests. With it on, Lyrion gets one `status` request per member every 2 s.

Tests, commit and CI: see the final message of this session; this report states only what was verified there.
Rollback: `git revert <commit>` on develop; on the Pi, `rm -rf ~/hifi-bridge && mv ~/hifi-bridge.3a74653 ~/hifi-bridge`.

## Next step for the owner (docs/HARDWARE_TEST_PROCEDURE.md section 7)

Deploy the new bundle and set Lyrion's `player.source` log to INFO (temporary). Then run 10 min each:
1. the unchanged three rooms;
2. esparagus-01 + Arbeitszimmer;
3. Küche + Arbeitszimmer.

Bring back the trace logs, the redacted Lyrion log lines, the silence times, and Arbeitszimmer's hardware and Squeezelite options.

**Pass per run:** 10 min audible in every room; after start-up no `STUCK`, no `WAITING`, no re-request, all members `ok`.

**G1-S passes only on a fresh three-room run** of at least 10 minutes with no audible interruption, and logs that support it.
