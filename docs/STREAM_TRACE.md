# Audio-path trace (`HIFI_STREAM_TRACE=1`)

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
| summary, once per second while anything is active | `in:` chunks/bytes the bridge's **own** capture child delivered in that second, `peak` (dBFS) and `nz` (share of non-zero samples), `gap-max` (longest pause between chunks), `idle=` (seconds without any chunk) · `feeder:` real frames written, silence frames inserted, frames dropped, `fill` on/off · `out:` per connection: bytes **written to the socket** in that second (WAV: 176400/s at 1x), level of that audio, `lag` (behind the live edge), `blk` (longest write; `STUCK` = a write has been blocked for more than 1 s and has not returned yet), `skip` (frames jumped because the reader fell a whole ring behind) · `lms:` the controller's last LMS poll of the target (`mode`, elapsed `time`, `duration` as LMS believes it, `ours` = our URL is current, `seen` = age of that poll) |

Privacy and size: no audio, no stream token, no URL, no client address, the `Range` header only as `bytes=<digits>-<digits>`.
At most one summary per second (~150 bytes) plus two lines per connection.

## Reading an outage

Find the silent stretch the listener noted (wall time), then look at the lines **before** the new `start GET 206`:

| Pattern during the silence | Meaning | Where the cause is |
|---|---|---|
| `in:` nz≈1.00, `out: rN` ~176400 B/s nz≈1.00, `blk` small, no `end` | the bridge received and **sent** live audio the whole time | downstream of the bridge: LMS or a player (LMS `server.log`: "Unexpected data stream disconnect", "RETRYING") |
| `out: rN … STUCK` (LMS stopped reading) for several seconds, then `rN end reason=peer-reset` and at once `rN+1 start GET 206 range=bytes=<large>-` | LMS restarted the stream for the group - lab-reproduced when **one sync member stops taking data for ~20 s** and reconnects (lab evidence 3 below). Without `STUCK` but with `out: no connection` for ~5 s first: LMS's 5 s **retry** after a member reported a stream error (`StreamingController::_willRetry`) | a group member / its network (LMS web UI: player list and signal strength; LMS `server.log`), or the Pi's own uplink (see the next row) |
| `blk=…ms STUCK`, `out` bytes/s well below 176400, `lag` growing, maybe `skip`, **without** an LMS restart | the bridge **could not deliver**: TCP back-pressure - LMS not reading (a slow member, row 2) or the Pi's own network (e.g. Wi-Fi with Bluetooth audio on the Pi 3 B+'s shared 2.4 GHz combo chip) | network / LMS read side; STUCK alone cannot tell these apart - the LMS player list can |
| `in: 0ch … idle=…s` or `in:` nz=0.00, `feeder: +… silence`, `out` 176400 B/s nz=0.00 | the bridge's **own** capture delivered nothing or silence; the stream carried silence (fill) | ingress (PipeWire / capture child; the capture supervisor logs `capture child …`) |
| `lms: … time=` jumps back to ~0 together with a new `start` | LMS restarted the item (new request) | as row 2 |

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

So a new `206` is LMS's **recovery**, not the cause; the trace lines before it show whether the bridge was still delivering.
