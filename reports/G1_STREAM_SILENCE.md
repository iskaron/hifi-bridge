# G1-S - intermittent 10-15 s silence downstream of the Bluetooth PCM ingress

Date 2026-11-25 · branch `develop` · starting point `303fdb1` · hardware gate **G1-S: NOT passed**.

## Owner evidence (REAL-HARDWARE, 2026-11-24, Pi 3 B+, Pi OS Lite trixie, Pixel, LMS 9.1.1 sync group Küche + Arbeitszimmer + esparagus-01)

The first end-to-end music was heard. The music goes silent for ~10-15 s at a time and comes back exactly when a new
`"GET /s/***/<sid>.wav HTTP/1.0" 206 -` line appears in the bridge log. The controller showed no state change during these outages.
A parallel 45 s `hwkit capture` of the phone's source while the bridge ran carried audio the whole time (0 silent chunks,
peak -10.4 dBFS, nz 0.9998). That capture came from a second PipeWire client, so it says nothing about the bridge's own child.

## What the code shows (STATIC, source read)

| # | Finding | Evidence |
|---|---|---|
| 1 | The `"GET …" 206 -` line is written when the response **starts**, not when it ends. The bridge never logged when or why a previous response ended (`client_disconnected` was kept in RAM only, without a reason). | `http.server.BaseHTTPRequestHandler.send_response()` calls `log_request()`; `bridge/btbridge/stream/server.py` `_handle` |
| 2 | Every request gets its own live reader at the live edge (≤ `lead_in_ms` back). A Range is honoured by skipping the 44-byte WAV header and is answered with 206 + `Content-Range: bytes N-4294967294/4294967295`, never with a Content-Length. While a session is open the feeder fills silence, so a held connection is never starved. No code path delivers brief audio, then waits for the next request. | `server.py` `_handle`, `stream/feeder.py`, `core/ring.py` |
| 3 | In a sync group LMS reads the source **once** and proxies it. It stops reading for the whole group as soon as one member's chunk queue is full. | LMS 9.1.2 `Slim/Player/Source.pm` `nextChunk` ("Bug 14117", `QUEUED_CHUNKS_HWM`) |
| 4 | A data-stream error from **any** member while playing stops the whole synced group, and LMS re-streams after 5 s (`RETRYING`). Mode stays `play` meanwhile (TRACKWAIT), which is why the bridge's 1 s poll sees no state change. | `StreamingController.pm` `_SyncStopNext`, `_willRetry` (5/10/15/30 s), `Source.pm` `_returnPlayMode`; `Slimproto.pm` (DSCO with a reason → `playerStreamingFailed(…, 'errorDisconnect')`) |
| 5 | LMS treats the endless stream as a finite 6.8 h file (`duration` 24347.9 s). This was not shown to be involved; it is recorded as OPEN_QUESTIONS #104 and left unchanged. | `Protocols/HTTP.pm` `parseDirectHeaders`/`parseHeaders`; INTEGRATION observation below |

**No bridge bug is demonstrated.** Per the task, nothing was "fixed"; this package stops at instrumentation.

## Ranked hypotheses (by evidence, not proven for the Pi)

1. **A sync-group member stops taking data, LMS stops reading for the group, then restarts the stream** (Wi-Fi of a member, or of the Pi).
   This matches the Pi signature exactly, and the lab reproduces it (see below). Inside LMS it is consistent with no bridge state
   change. Open: which member, or the Pi's own uplink (Bluetooth A2DP and 2.4 GHz Wi-Fi share the Pi 3 B+'s combo chip).
2. **A member reports a stream error, LMS stops the group, then retries after 5 s.** Same visible signature, but without a preceding
   stuck write. Distinguishable in the trace.
3. **The bridge's own capture delivered nothing or silence** (the hwkit capture used another client). Less likely: the earlier
   bridge-side recovery came with a new HTTP request, and an ingress gap would not produce one. The trace shows this directly (`in:`).
4. A stream-server defect. Not supported by the code or by the tests below.

## Instrumentation (opt-in, off by default): `HIFI_STREAM_TRACE=1`, docs/STREAM_TRACE.md

`bridge/btbridge/stream/trace.py`, wired in `app.py`, `stream/server.py`, `core/controller.py` (`last_observation`) and
`lms/client.py` (`Observation.time`, `.duration`).

**Per second:**
- (a) the bridge's **own** ingress: chunks, bytes, peak in dBFS, non-zero share, longest gap;
- (b) the feeder: real frames, inserted silence, dropped frames;
- (c) per connection: bytes actually written, audio level, lag behind the live edge, longest write, and `STUCK` while a write has not returned;
- (e) the LMS observation: mode, elapsed time, duration, whether the current item is ours.

**Per connection:**
- `start` with status, the sanitised `Range` and `Content-Range`, and the lead;
- (d) `end` with the reason (`peer-closed` / `peer-reset` / `write-timeout-60s` / `session-ended` / `os-error-N` / `error-T`), duration and bytes.

**Bounds and privacy:**
- Every line has monotonic and wall time.
- There is no token, no URL and no peer address (pseudonyms `peerN`), and no audio, only levels.
- At most one summary line per second.
- When the trace is off, no hook runs. The test `test_the_trace_does_not_change_a_single_byte_of_the_stream` checks that the output is unchanged.

## Tests

### UNIT: `tests/unit/test_stream_continuity.py`, 16 tests

These run against real sockets with a real `StreamServer`/`LiveFeeder`/`PcmRing` and a simulated PipeWire source.

- 200 and 206 headers.
- Four LMS-style Range forms, each answered with fresh live PCM.
- Six repeated 206 requests after close and RST, with slots released.
- A **130 s held connection**: bit-exact, contiguous, one connection, no silence, no drop, no skip.
- A source gap: the same connection keeps delivering at 1x with silence.
- Three simultaneous readers get identical bytes.
- A slow reader is skipped forward and the others are not disturbed.
- The trace is off by default and its env switch works.
- Trace fields, and no private data in them.
- A stuck write is shown while it is stuck, with a growing lag, and ends as `write-timeout`.
- The session-end reason.
- The helpers.
- The trace leaves the stream byte-identical.

### INTEGRATION: `tests/integration/test_14_stream_continuity.py`

Real LMS 9.1.2 and three Squeezelite players in a sync group, with the trace on, for 150 s.

- All three players were 100 % audible.
- LMS made one scan request (`200`) and one `206 range=bytes=44-`, and **no later request**.
- 0 stuck writes, 0 skips, 0 silence frames.
- LMS reports `duration=24347.9`.
- Evidence: `measurements/stream_continuity_sync_group_150s.json`.

### EXPERIMENT: `scripts/exp_group_member_stall.sh`

INTEGRATION lab. player3's Squeezelite is frozen with SIGSTOP; this is not hardware.

**8 s freeze** (`measurements/exp_group_member_stall_8s.json`):
- The whole group was silent for ~6 s, starting ~3 s after the freeze.
- Trace: `in:` stayed nz=1.00 while `out: r2:0B … blk=4947ms STUCK`; then a 1 MB burst on the **same** connection.
- No new request.

**20 s freeze** (`…_20s.json`):
- The whole group was silent for ~17 s.
- LMS had dropped player3's control connection. On its reconnect, LMS restarted the group: `r2 end reason=peer-reset after=45.93s` and, in the same instant, `r3 start GET 206 range=bytes=4806588- … lead=1000ms`.
- All players were audible again within ~1 s.
- This is **the Pi signature**.

### Suites run on this change

| Suite | Result |
|---|---|
| Unit | 1146 passed, 1 warning: an unhandled thread exception "Bad file descriptor" in `tests/unit/test_supervision.py`, which does not touch the changed code |
| Integration | 58 passed (full suite, including `test_14`) |
| Static | exit 0 |
| Demo | 56 passed |
| hwkit | 36 passed |
| Entry | 100 passed |
| PipeWire | see the final message |

## Hardware gate

**G1-S: NOT passed.** It passes only when the owner confirms several minutes of uninterrupted music in every room. The next step is the
trace run in docs/HARDWARE_TEST_PROCEDURE.md section 6.

Bring back `~/g1-trace.log` with the clock times of the silences, and say whether the Pi and each player are on Wi-Fi or Ethernet.

**What the answer will be:**
- **STUCK** (or `no connection`) before a new `206`, while `in:` stays nz≈1 → the cause is downstream (LMS / a member / the network).
  The next step is then to find the member (Lyrion player list, signal strength), not to change the bridge.
- **`in:` nz=0 or idle** → the bridge's own ingress.
- Delivery steady at 176400 B/s with audio and no restart, yet silence → LMS or a player.
