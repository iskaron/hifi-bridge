# Report — live MP3 320 kbit/s and now-playing titles (2026-11-27)

Scope: the owner's brief "live MP3 320 kbps and real now-playing metadata". Architecture and behaviour: **docs/MP3_AND_METADATA.md**.
Decision: DECISIONS #147. Owner runbook (update, A/B, titles, rollback): **docs/PILOT.md section 9**. G1-S stays **OPEN**; no version bump,
tag, release or `main` merge. Evidence labels: UNIT, INTEGRATION (real Lyrion 9.1.2 + Squeezelite, simulated Bluetooth), STATIC, REAL-HARDWARE.

## 1. Outcome in one paragraph

The bridge can serve a genuine bridge-encoded **MPEG-1 Layer III, 44.1 kHz stereo, CBR 320 kbit/s** live stream (`lame` child process per
connection, `audio/mpeg`, no invented length or ranges) next to the unchanged WAV default and FLAC, selectable in the WebUI and instantly
reversible. With a separate opt-in switch the phone's **artist and title reach Lyrion's now-playing model** (`current_title`, item
title/artist) through standard ICY metadata, change mid-stream **without restart, gap or underrun**, and never leak stale text. All of this is proven
in software against a real Lyrion. **Not proven:** that MP3 helps the sync-group stalls (a bitrate hypothesis), the Pi 3 B+ cost, and what the
Squeezebox Classic, the two ESP32 players and the Nest displays show.

## 2. What the code now does (verified facts)

| Claim | Evidence |
|---|---|
| output is real MPEG-1 L3, 44.1 kHz, stereo, CBR 320, frames 1044/1045 B, starts on a frame, no ID3/Xing/Info tag; decodes (via `lame --decode`) to the input with L/R in place | UNIT `test_mp3_encoder.py` |
| data rate 40 kB/s (≈320 kbit/s) instead of 176.4 kB/s (WAV) | UNIT + INTEGRATION (319.5 kbit/s on the wire; Lyrion reports `320kbps CBR`) |
| bounded: ≤128 KiB queue + 2×64 KiB pipes, child blocks when the reader stalls; ≤4 encoders (else 503); killed and reaped on hang-up, session end, `stop()`; no leftover process | UNIT `test_mp3_encoder.py`, `test_mp3_stream.py` (checks `/proc`) |
| missing `lame`: WebUI refuses to select mp3; stored mp3 plays nothing and reports `stream_encoder_unavailable`; WAV/FLAC unaffected; recovers when `lame` returns | UNIT `test_mp3_integration_wiring.py`, `test_mp3_stream.py` |
| HTTP: `200`, `audio/mpeg`, `icy-br: 320`, no `Content-Length`/`Content-Range`; `Range` gets the live stream; HEAD starts no encoder; 410 stale session, 403 bad token, 404 other format | UNIT `test_mp3_stream.py` |
| ICY: `metaint` over MP3 bytes (8192), byte-exact for any chunking, title only on change + safety repeat, explicit clear, UTF-8/quote/control-char/length handling; plain stream without the request or the switch | UNIT `test_icy_framing.py`, `test_mp3_stream.py` |
| Lyrion shows `Artist - Title` (+ split title/artist) within 0.1–1 s of the first title and ~3 s after a track change, one `play` only, 0 underruns, no audio gap; cleared → `Bluetooth`; next session → `Hi-Fi Bridge`; same title on all 3 members of a sync group; foreign sources untouched; WAV-ICY switch does not enable MP3 titles | INTEGRATION `test_13_mp3_icy_lms.py` (6 tests) |
| WAV and FLAC behaviour unchanged (existing suites) | UNIT (1198 tests) + INTEGRATION regression subset |
| images carry `lame`; defconfigs resolve; licence report lists `lame 3.100 LGPL-2.0+ ok` | STATIC (`os_defconfigs.sh check`, `os_licenses.sh`) |

Measured in the lab (INTEGRATION, `measurements/mp3_320_lms.json`, `mp3_icy_titles.json`; virtual DAC, **not acoustic, not a Pi**): first audio after Play
3.3 s (WAV 1.5 s); first title 0.12 s; track-change title 3.1 s; encoder ≈2–4 % of one **x86** core, 5.4 MiB.

## 3. What was NOT verified (do not read more into this)

* **Pi 3 B+**: `lame -q 5` CPU/RAM, start-up delay, alongside BlueZ/PipeWire — run `pilot_service.sh mp3-check` and the A/B (PILOT.md section 9).
* **Stalls**: whether MP3 reduces `playerOutputUnderrun`/`WAITING_TO_SYNC` in the owner's group. The lab cannot reproduce them (OPEN_QUESTIONS #113).
* **Displays**: Lyrion web UI visually, **Squeezebox Classic**, **Squeezelite-ESP32** ×2, **Nest displays** (OPEN_QUESTIONS #114). A Nest may depend on its Lyrion
  integration; if it stays on "Hi-Fi Bridge" while Lyrion's UI shows the title, the follow-up is that integration, not the stream.
* **Lyrion 9.1.1** (owner) vs 9.1.2 (lab) (#115); a real phone's AVRCP behaviour (the lab simulates it).
* **Images**: nothing rebuilt or booted; the CycloneDX SBOMs (`os_sbom_*.cdx.json`) are produced by the image-build step and were **not** regenerated here.

## 4. Test and CI results (actual)

See the delivery section of the chat report / commit list below for the exact commands and exit codes; summary: static rc 0, security medium/high gate rc 0,
unit 1198 passed, hwkit 61 passed, tests/os + flashtool 225 passed, defconfig check PASS ×3, integration `test_13` 6 passed + regression subset, CI on `develop`
as listed in the delivery message. The pre-existing demo `test_08` flake (OPEN_QUESTIONS #112) is unrelated to this change.

## 5. Owner artefacts

Bundle `dist/hwkit-bundle-<rev>.tar.gz` (contains `docs/PILOT.md`, `docs/MP3_AND_METADATA.md`, `pilot_service.sh mp3-check`), runbook PILOT.md section 9,
rollback PILOT.md section 9.4 (WebUI → WAV + both ICY boxes off; or restore `~/hifi-bridge.prev-<oldrev>`).
