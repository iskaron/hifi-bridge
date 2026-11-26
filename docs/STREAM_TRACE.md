# Audio-path trace (`HIFI_STREAM_TRACE=1`)

See also `reports/G1S_THREE_ROOM_STALLS.md` (three-room analysis of 2026-11-25).

Opt-in diagnostics for the real-hardware finding **G1-S** (2026-11-24, Pi 3 B+, Pixel → BlueZ/PipeWire → bridge → LMS 9.1.1 sync group
Küche + Arbeitszimmer + esparagus-01): the music is audible, goes **silent for ~10-15 s**, and comes back exactly when a new
`"GET /s/***/<sid>.wav HTTP/1.0" 206 -` line appears in the bridge log. **Off by default**; when off, no hook runs.

## Switch it on

```sh
HIFI_STREAM_TRACE=1 python3 -m btbridge.appliance --data "$HOME/bridge-data" --source bluez --boot-dir /boot/firmware 2>&1 | tee ~/g1-trace.log
```

(`btbridge` / the lab: `RuntimeConfig(stream_trace=True)`, `make_bridge(trace=True)`.)

## What a line means

Every line starts with `[trace] t=<monotonic s since start> <local HH:MM:SS>`.

| Line | Fields |
|---|---|
| `rN start GET 206 sid=… peerK range=bytes=44-4294967338 content-range=bytes 44-4294967294/4294967295 wav-header=no lead=980ms` | a new stream response began (the stock `"GET …" 206 -` line is written at the same moment: `BaseHTTPRequestHandler.send_response()` logs at the **start**, not at the end). `peerK` = a pseudonym per client address, `lead` = how far behind the live edge the response starts |
| `rN end reason=… after=…s bytes=… sid=… peerK [write-in-progress=…s]` | the response ended. `peer-closed` (broken pipe), `peer-reset` (RST), `write-timeout-60s` (the client took nothing for a minute), `session-ended` (the bridge ended the session), `os-error-<errno>`, `error-<Type>` |
| summary, once per second while anything is active | `in:` chunks/bytes the bridge's **own** capture child delivered in that second, `peak` (dBFS) and `nz` (share of non-zero samples), `gap-max` (longest pause between chunks), `idle=` (seconds without any chunk) · `feeder:` real frames written, silence frames inserted, frames dropped, `fill` on/off · `out:` per connection: bytes **written to the socket** in that second (WAV: 176400/s at 1x), level of that audio, `lag` (behind the live edge; grows from the ring position where the connection started even while its very first write is blocked), `blk` (longest write; `STUCK` = a write has been blocked for more than 1 s and has not returned yet), `skip` (frames jumped because the reader fell a whole ring behind) · `lms:` the controller's last LMS poll of the target (`mode`, elapsed `time`, `duration` as LMS believes it, `ours` = our URL is current, `seen` = age of that poll, `WAITING` = Lyrion's `waitingToPlay`: mode is `play` but the group controller is BUFFERING / WAITING_TO_SYNC - i.e. **Lyrion has paused the group to rebuffer**, `poll=lms-unreachable` / `poll=target-disconnected` = the last poll of the target got no answer / found the target player disconnected from Lyrion) · `members:` every member of the target's sync group by its Lyrion name, `ok` or `DISC` (Slimproto connection), `sig=` Lyrion's Wi-Fi signal % when the player reports one, `(master)` on the sync master (Lyrion takes the elapsed `time` and the restart position from the master; the target is listed first, which is not necessarily the master); polled every 2 s in a separate thread (`(Ns old)` when Lyrion did not answer) |

Privacy and size: no audio, no stream token, no URL, no client address, the `Range` header only as `bytes=<digits>-<digits>`; player
names as Lyrion shows them (the owner's room names), never ids or addresses. At most one summary per second (~250 bytes) plus two lines
per connection. Load on Lyrion while the trace is on: one extra read-only `status` request per group member every 2 s.

## Why Lyrion stops reading: sync-group rebuffering (STATIC, Lyrion 9.1.2 source; INTEGRATION-reproduced)

1. **Any** member's output underrun (Slimproto `STMo`, `Slim::Player::Squeezebox2` → `StreamingController::playerOutputUnderrun`)
   triggers `_Rebuffer`: **every** player of the group is paused and the controller goes to BUFFERING. `mode` stays `play`; `time`
   freezes because `Squeezebox2::songElapsedSeconds` only extrapolates while the controller is PLAYING; `waitingToPlay` becomes 1.
2. While paused, a member that takes no data fills its Lyrion queue; at 10 queued chunks Lyrion stops reading the **source** for the
   whole group (`Slim::Player::Source::nextChunk`, "Bug 14117"): the bridge's write blocks (`STUCK`). Healthy members cannot fill up
   either, because the group is fed from one source read.
3. Playback resumes when every member's buffer has reached the rebuffer threshold (`Slim::Player::Player::rebuffer`: 5 s of the stream
   bitrate - 882000 bytes for our 1411 kbit/s WAV - capped at the player's buffer size).
4. If that has not happened after **30 s**, Lyrion gives up ("Bug 6712", `Player::_buffering` → `REBUFFERING_FAILED`) and calls
   `jumpToTime(elapsed)`: it stops the group, drops the old source connection (`peer-reset`) and requests the stream again from the
   byte offset of the frozen elapsed time (`Protocols::HTTP::getSeekData`: `elapsed × 176400`, aligned to 4 bytes, `+ 44`). That is
   the new `GET … 206 range=bytes=<large>-`. The bridge serves it from the live edge (a live source cannot replay).

So in a sync group one member that stops taking data, or underruns, silences **all** rooms at once; the bridge cannot see which.

## What the 2026-11-25 Pi runs added (REAL-HARDWARE; reports/G1S_ROUND2_ANALYSIS.md)

* **A member that accepts audio but does not play it** (hung output) fills its buffers and then stops the whole group's source:
  stream start → first `STUCK` took 24.4 / 25.3 / 25.6 s in three runs ≈ Squeezelite's default stream + output buffers
  (21.9 s) plus queues, and ~32-38 s again after every restart. Signature: first `blk` ≈25 s after a start, `members:` all `ok`.
* **A restart does not need 30 s**: a member that has refilled while another has not triggers Lyrion's give-up path within 0.3 s
  (`Player::_buffering`, `bufferStarted(0)` then `time() > 0 + 30`). Seen once on the Pi: first ready 15:25:39.1667, `jumpToTime` 39.4700.
* **`range=bytes=4294967336-`** is Lyrion asking for its time clamped at the virtual duration (24347.887 s); the bridge only echoes it.
  `lms: play time=24347.9` right from the start means the master's elapsed time is already wrong.
* A live source gives every member only ~1 s of cushion, and a refill needs ≥4 s of real-time audio: one member's underrun costs ≈6 s
  of silence in all rooms even when the bridge never blocked (test 4: `blk` 1-3 ms throughout, Küche underran, `WAITING` 6 s).

## Reading an outage

Find the silent stretch the listener noted (wall time), then look at the lines **before** the new `start GET 206`:

| Pattern during the silence | Meaning | Where the cause is |
|---|---|---|
| `in:` nz≈1.00, `out: rN` ~176400 B/s nz≈1.00, `blk` small, no `end` | the bridge received and **sent** live audio the whole time | downstream of the bridge: LMS or a player (LMS `server.log`: "Unexpected data stream disconnect", "RETRYING") |
| `out: rN … STUCK` (LMS stopped reading) for several seconds, then `rN end reason=peer-reset` and at once `rN+1 start GET 206 range=bytes=<large>-` | LMS restarted the stream for the group - lab-reproduced when **one sync member stops taking data for ~20 s** and reconnects (lab evidence 3 below). Without `STUCK` but with `out: no connection` for ~5 s first: LMS's 5 s **retry** after a member reported a stream error (`StreamingController::_willRetry`) | a group member / its network (LMS web UI: player list and signal strength; LMS `server.log`), or the Pi's own uplink (see the next row) |
| `blk=…ms STUCK`, `out` bytes/s well below 176400, `lag` growing, maybe `skip`, **without** an LMS restart | the bridge **could not deliver**: TCP back-pressure - LMS not reading (a slow member, row 2) or the Pi's own network (e.g. Wi-Fi with Bluetooth audio on the Pi 3 B+'s shared 2.4 GHz combo chip) | network / LMS read side; STUCK alone cannot tell these apart - the LMS player list can |
| `in: 0ch … idle=…s` or `in:` nz=0.00, `feeder: +… silence`, `out` 176400 B/s nz=0.00 | the bridge's **own** capture delivered nothing or silence; the stream carried silence (fill) | ingress (PipeWire / capture child; the capture supervisor logs `capture child …`) |
| `lms: play time=<frozen> … WAITING`, a few s later `out: … STUCK` (or the reverse), `members:` all `ok`; after **~30 s** `rN end reason=peer-reset` + `rN+1 start GET 206 range=bytes=<≈ frozen time × 176400 + 44>-` | Lyrion rebuffers the group after a member's underrun and gives up after 30 s (section above) - lab-reproduced by stalling one member's data connection (lab evidence 4) | the member whose data path or output stalled: Lyrion `server.log` with `player.source` at INFO names it (`playerOutputUnderrun <MAC>: decoder: … / output: …`) |
| as before, but the restart comes earlier together with `members: …:DISC` or `poll=target-disconnected` | a member dropped its Slimproto connection; Lyrion restarted the group when it reconnected (lab evidence 3) | that member's network |
| `WAITING` for a few s, then output resumes on the **same** connection without a new `start` | a short member stall: the rebuffer completed (lab evidence 5) | as above, but brief |
| `lms: … time=` jumps back to ~0 together with a new `start` | LMS restarted the item (new request) | as row 2 |
| `lms: … time=` jumps or runs backwards right after a restart | Lyrion's `time` is `startOffset + the master player's reported elapsed`; until the new stream's first status it adds the OLD elapsed to the new offset (`StreamingController::playingSongElapsed`) - not a byte count | nothing to fix; read time only as "advancing" vs "frozen" |

## Lab evidence (INTEGRATION, real LMS 9.1.2 + three Squeezelite in a sync group, simulated Bluetooth; NOT hardware)

1. **Baseline, 150 s** (`tests/integration/test_14_stream_continuity.py`, `measurements/stream_continuity_sync_group_150s.json`):
   LMS made two requests at start-up - its header scan (`r1 … 200`, closed after 7 KB) and the proxied stream
   (`r2 … 206 range=bytes=44-`) - and **none** afterwards; `r2` carried the group for 150 s (26.5 MB, 1x), all three players 100 %
   audible, 0 stuck writes, 0 ring skips, 0 silence frames. The `lms:` field shows `duration=24348` - LMS treats the endless stream as a
   6.8-hour file because of the WAV header's 0xFFFFFFFF size / our `Content-Range … /4294967295` (OPEN_QUESTIONS #104).
2. **One member frozen for 8 s** (`scripts/exp_group_member_stall.sh 30 8 75`, `measurements/exp_group_member_stall_8s.json`):
   ~3 s after player3 stops taking data the **whole group** goes silent (LMS stops reading the source when one member's queue is full,
   `Slim::Player::Source::nextChunk`, "Bug 14117"). Trace: `in:` stays nz=1.00, `out: r2:0B … blk=2947ms STUCK` … `blk=4947ms STUCK`,
   then a 1 MB burst on the **same** connection when player3 resumes; audio back after ~6 s of silence, **no new request**.
3. **One member frozen for 20 s** (`scripts/exp_group_member_stall.sh 30 20 90`, `measurements/exp_group_member_stall_20s.json`):
   the group is silent for **~17 s**; LMS has dropped player3's control connection meanwhile. When player3 reconnects, LMS restarts the
   group: `r2 end reason=peer-reset after=45.93s`, **at the same instant** `r3 start GET 206 range=bytes=4806588- … lead=1000ms`, and all
   three players are audible again within ~1 s. That is the Pi's signature ("silent 10-15 s, back exactly with a new GET 206").

4. **Only player4's audio data connection stalled for 40 s, its control connection stayed up** (`scripts/exp_group_member_datastall.sh
   30 40 110`, group player1 + player2 + player4, `measurements/exp_group_member_datastall_40s.json`; player4 sits behind the
   userspace relay `btkit.stall_relay`, no NET_ADMIN). Lab Lyrion's log: `playerOutputUnderrun 02:00:00:00:00:04: decoder: 0 / output: 0`
   → `BUFFERING`; healthy members stay at `decoder 0 / output ≈360 KB` of an 882000-byte threshold because the group gets no more source
   data; **31 s later** `jumpToTime` → `r2 end reason=peer-reset … write-in-progress=30.37s` and at once `r3 start GET 206
   range=bytes=4608668-` (= 26.13 s × 176400 + 44, the frozen `time=26.1`). The trace shows `STUCK … lms: play time=26.1 … WAITING |
   members: lab-player1:ok, lab-player2:ok, lab-player4:ok` throughout - **the Pi's three-room signature, with every member "connected"**.
5. **The same, 5 s only** (`… 30 5 85`, `measurements/exp_group_member_datastall_5s.json`): the group is silent ~4 s, the rebuffer
   completes, output continues on the **same** connection, no new request. With an extra healthy member running a smaller buffer
   (`-b 400:600` KB, `…_5s_smallbuf.json`) the outcome is identical: a small but realistic member buffer does **not** turn a short
   stall into the 30 s give-up. (With `-b 128:256`, below Lyrion's 255 KB start threshold, the group never started at all; not the Pi
   behaviour, not kept.)

So a new `206` is LMS's **recovery**, not the cause; the trace lines before it show whether the bridge was still delivering.
