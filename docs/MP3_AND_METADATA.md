# Live MP3 320 kbit/s and now-playing titles — architecture, behaviour, evidence (EXPERIMENTAL)

Status (2026-11-27): implemented and tested **in software** (UNIT + INTEGRATION with a real Lyrion and real Squeezelite). **Nothing here is
verified on the Pi 3 B+ or on any display.** G1-S stays **open**. The MP3 mode is a *bitrate A/B experiment* for the owner's sync group; it is
**not** a proven fix for the stalls. DECISIONS #147; owner runbook: docs/PILOT.md section 9.

## 1. What was built

| Part | Where |
|---|---|
| Live MP3 encoder (`lame` child process, 320 kbit/s CBR, 44.1 kHz stereo) | `bridge/btbridge/stream/encoders.py` (`Mp3Encoder`) |
| MPEG frame-header reader (proof that the output is MPEG-1 Layer III CBR) | `bridge/btbridge/stream/mp3frames.py` |
| `.mp3` live endpoint, ICY switches, encoder limit | `bridge/btbridge/stream/server.py` |
| ICY framing (byte-exact, escaping, change/clear semantics) | `bridge/btbridge/stream/icy.py` |
| `Artist - Title` text from AVRCP | `bridge/btbridge/app.py` (`display_title`) |
| Settings `stream_format=mp3`, `icy_mp3` (WebUI → Settings → Playback) | `config/schema.py`, `webui/views.py`, i18n DE/EN |
| Trace fields (opt-in `HIFI_STREAM_TRACE=1`) | `stream/trace.py` |
| Owner check of the encoder (`pilot_service.sh mp3-check`) | `stream/mp3check.py`, `hwkit/pi-setup/pilot_service.sh` |
| Buildroot images carry `lame` | `os/br2-external` (fragment, defconfigs, package dependency) |

Unchanged: WAV (default) and FLAC, the pause/disconnect/target semantics, the Lyrion source-priority contract (the bridge only ever sends
`play`, URL-addressed `release`, `power on` for its own target and relative `volume`; no stop/pause/clear/sync/power-off), token and session
rules, the 5-minute pairing window.

## 2. Live MP3: how it works

```
phone PCM → LiveFeeder → PcmRing ─┬─ GET /s/<token>/<sid>.wav  → WAV bytes (as before)
                                  └─ GET /s/<token>/<sid>.mp3  → one `lame` child per connection → audio/mpeg
```

* **Encoder:** the `lame` CLI (LAME 3.100, **LGPL-2.0+**; Debian `lame`, Buildroot `lame`). Arguments are fixed: raw s16le, 44.1 kHz, stereo
  (`-m s`), `--cbr -b 320`, `-q 5` (lame's own speed/quality default), no Xing/Info tag (`-t`: meaningless in a live stream). A subprocess
  rather than a binding: a crash in the encoder ends one stream, never the bridge; no new Python dependency; the same pattern as FLAC.
* **One encoder per HTTP connection.** Every client starts at a frame boundary with an empty bit reservoir, so a late joiner never gets
  frames that depend on bytes it did not receive, and no shared state can couple two players. A sync group is normally *one* connection
  that Lyrion proxies, plus short probe connections. **At most 4 encoders** run at once (`MAX_MP3_ENCODERS`); more get `503`.
* **Order matters:** the encoder is started *before* the status line. A missing binary or a full limit is a clean `503`, not a `200`
  that dies.
* **Bounded:** the child's pipes hold 64 KiB; a reader thread moves its output into a queue of at most 128 KiB (about 3 s) and then stops
  reading, so a slow client blocks the child instead of growing memory. Nothing is written to disk, no PCM is kept except the existing
  6 s ring. `close()` always kills and reaps the child (also on client hang-up, session end, `StreamServer.stop()`; a unit test checks
  `/proc` for leftovers). A dead encoder ends that connection visibly (`reason=encoder-died`, counted) — never silence.
* **Response:** `200`, `Content-Type: audio/mpeg`, `icy-br: 320`, `Accept-Ranges: none`, **no** `Content-Length`, **no** `Content-Range`,
  `Connection` close-delimited (HTTP/1.0). A `Range: bytes=N-` request is answered with the live stream (`200`), never a fabricated `206`.
  `HEAD` answers the headers and starts no encoder. Stale session → `410`, wrong token → `403`, other format of the session → `404` (as before).
* **Start of a connection:** the same bounded lead-in as WAV (`lead_in_ms`, default 1000 ms of already-captured PCM, never across a gap):
  `lame` encodes it at CPU speed, so the first ~40 KB arrive at once and then the stream runs at real time. `lead_in_ms` is **not** a
  sustained client buffer (unchanged semantics).
* **Silence/idle:** unchanged — the feeder keeps the timeline alive with digital silence, which encodes to a valid, tiny-looking frame stream.
* **Missing encoder:** `stream_format=mp3` cannot be *selected* without `lame` (WebUI error, nothing changes). If `lame` disappears
  afterwards the controller sends **no** play command, reports `blocked_reason=stream_encoder_unavailable` (WebUI headline detail in DE/EN) and
  retries; it never substitutes WAV. WAV/FLAC code paths do not import or call the encoder.

Measured in the lab (INTEGRATION; Docker, virtual DAC; **not** a Pi): wire rate 319.5 kbit/s (WAV: 1411); Lyrion shows `320kbps CBR`; the
encoder used about 2–4 % of one x86 core (`mp3check`: 2.2 %, 5.4 MiB RSS) — **not transferable to a Pi 3 B+**; run `pilot_service.sh mp3-check`
there. First audio after Play: **3.3 s with MP3 vs 1.5 s with WAV** in the lab — Lyrion appears to wait for a byte threshold before it starts,
which a 4.4× smaller stream reaches later (hypothesis; OPEN_QUESTIONS #116). Expect a longer start on the Pi too.

## 3. Titles: the path, end to end

```
phone AVRCP Track(title, artist, album)
  → BlueZ MediaPlayer1 → adapters/bluez (model.py: on change only) → BridgeRuntime._on_metadata
  → display_title(): "Artist - Title" | "Title" | "Artist" | ""   (album is not carried: ICY has one text field)
  → Controller.on_metadata → StreamServer.title            (the WebUI "now playing" reads this; it was always correct)
  → ICY block in the .mp3 (or .wav) response            ← only if the switch is on AND the client sent `Icy-MetaData: 1`
  → Lyrion reads the stream, parses StreamTitle        → status: current_title, playlist item title + artist
  → players / controllers / displays
```

Why the display said just "Hi-Fi Bridge": that is the static `icy-name` response header (the *station* name). Lyrion shows it until a
`StreamTitle` arrives. `icy-name` is deliberately unchanged; changing it would not make a title dynamic.

**What Lyrion 9.1.2 does with it (INTEGRATION, `tests/integration/test_13_mp3_icy_lms.py`, real server, real Squeezelite, simulated AVRCP):**

| Moment | Lyrion `status` |
|---|---|
| playing, no title yet / ICY off | `current_title = "Hi-Fi Bridge"`, item title `Hi-Fi Bridge`, no artist |
| phone reports *Miles Davis – Blue in Green* | `current_title = "Miles Davis - Blue in Green"`, item **title `Blue in Green`, artist `Miles Davis`** (Lyrion splits it), within 0.1–1 s |
| track change mid-stream | new title after ~3 s (Lyrion reads ahead of playback and applies the title when that point is played), **no restart** (still one `play`), no gap, 0 underruns |
| metadata reported as empty | explicit empty `StreamTitle` → Lyrion falls back to the play title `Bluetooth` (after ~3 s) |
| phone leaves / new session | bridge title cleared; the next session starts at `Hi-Fi Bridge` — nothing stale |
| sync group (3 players) | all members report the same `current_title` |
| foreign source / manual change | not one bridge command; the radio is untouched |

The ICY block is sent only on change (plus a safety repeat every 16 blocks ≈ 3 s); an unchanged title costs one zero byte. "Length 0" means
"unchanged" in ICY, so **clearing is an explicit `StreamTitle='';`**. `metaint` is counted over the **MP3 bytes actually sent** (8192 → a
block roughly every 0.2 s); WAV keeps 16384 and its repeat-every-block behaviour. Text is UTF-8; control characters become blanks; the quote
that would end the field early becomes `’`; a title longer than one block is cut on a character boundary. A client that does not send
`Icy-MetaData: 1` gets the plain MP3 stream (verified byte-for-byte: removing the ICY blocks leaves a clean frame stream).

**Switches (both default OFF; DECISIONS #18 stands):** `icy_metadata` = titles on **WAV** (unchanged, never enable it for the Classic
without a test). `icy_mp3` = titles on **MP3** only. They are separate so that rolling back to WAV can never change what a legacy
player receives. FLAC never carries ICY.

**No LMS plugin and no extra Lyrion command were needed**: Lyrion's own ICY handling updates its now-playing model. Nothing else
(`playlist`, `title`, …) is sent to Lyrion for metadata.

## 4. Who shows it — what is verified and what is not

| Client | Shows the title? | Evidence |
|---|---|---|
| Bridge WebUI | yes (already) | UNIT/INTEGRATION |
| Lyrion JSON `status` (what every controller reads) | **yes**, see §3 | INTEGRATION, real Lyrion 9.1.2 (the owner's server is 9.1.1: not identical) |
| Lyrion web UI / Material skin | expected, because it renders the same `status`; **not looked at** | not verified visually |
| Squeezelite (software) | no display; decodes the MP3 | INTEGRATION |
| **Squeezebox Classic** (Küche) | **UNVERIFIED.** It decodes MP3 natively and its screen shows Lyrion's title; whether it shows the ICY title in a sync group is untested. Lyrion proxies a group stream, so the player needs no ICY handling itself | hardware test needed |
| **Squeezelite-ESP32** (Arbeitszimmer, Wohnzimmer) | **UNVERIFIED** (same reasoning; its display, if any, is fed by Lyrion) | hardware test needed |
| **Nest displays** | **UNVERIFIED and possibly out of the bridge's hands**: they appear as Lyrion players through some bridge/Cast integration that decides what metadata it forwards. If a Nest only shows `Hi-Fi Bridge`, the follow-up is a look at that integration (which `status` fields it reads: `current_title`, `title`, `artist`), not at the stream | hardware test needed |

## 5. Failure modes

| Situation | Behaviour |
|---|---|
| `lame` missing / not startable | `mp3` cannot be chosen; a stored `mp3` plays nothing and says so (`stream_encoder_unavailable`); WAV/FLAC unaffected |
| 5th simultaneous MP3 connection | `503`, counted (`encoder_refused`) |
| encoder process dies | connection ends (`encoder-died`), Lyrion reconnects like any broken stream |
| slow / stalled reader | writes block (60 s timeout as for WAV); queue capped; child blocked; memory flat |
| ICY switch on, client does not ask | plain stream |
| title changes while paused | kept; sent when the next session's stream passes a block boundary |

## 6. Trace (opt-in, `HIFI_STREAM_TRACE=1`)

`start` lines carry `fmt=mp3 icy=yes|no`. Each MP3 connection's per-second summary gets `mp3[<kbit/s> pcm=<B> enc=<ms> lame-cpu=<%>]`
(encoded rate that second; PCM bytes fed; time spent handing PCM to the encoder; CPU of that encoder child as % of one core). The `end`
line adds `mp3: pcm-in=… out=… avg=…kbit/s`. These let the owner compare PCM ingress cadence (`in:`), encoded throughput (`out:` `mp3[…]`),
HTTP backpressure (`blk`, `STUCK`) and Lyrion's `WAITING` in one log, without recording audio or any URL. Counters in `/status`:
`encoders_started`, `encoder_refused`, `encoder_died`, `icy_blocks`, `icy_titles`.

## 7. Packaging and licence

* **Stock Pi OS (pilot):** `sudo apt install lame` (owner action; the installer never installs packages and says so). `pilot_service.sh check`
  reports whether `lame` is present; `pilot_service.sh mp3-check` encodes 20 s of synthetic audio in real time and prints CPU/RAM/rate.
* **Images:** `BR2_PACKAGE_LAME=y` in `os/br2-external/fragments/common.fragment` (all three boards), `select`/dependency in the `hifi-bridge`
  package. LAME is LGPL-2.0+; the image uses it as a separate program/library, and `make legal-info` supplies source and licence text like
  for every other package. Size and SBOM/licence reports were regenerated (`reports/evidence/os_*`). **No image was rebuilt or booted.**
* Debian's `lame` links libsndfile and prints a harmless "libsndfile may ignore -r" warning on stderr (discarded); raw input from a pipe works
  and is covered by the tests.

## 7a. Not done / out of scope

No change to Lyrion's server, players, HA, ESP32 firmware, BlueZ bonds, the sync group or the radio-buffer setting. No ICY on FLAC. No album
field. No shared encoder. No `StreamUrl`/cover art. No claim about stalls: the A/B on the real hardware decides (PILOT.md section 9).
