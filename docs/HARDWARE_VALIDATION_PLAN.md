# Hardware validation plan (deferred - NOT executed)

Tooling for executing this plan: `hwkit/` (runbook `hwkit/README.md`). Test IDs below are mirrored in `hwkit/hwkit/catalog.json`.

Status: **plan only.** Nothing in this document has been run. The development host has no Bluetooth, Raspberry Pi, speakers or physical
Squeezeboxes (owner decision, DECISIONS #20), so every item below is labelled **UNTESTED** until the owner or a person with a hardware
rig executes it and records the result with evidence labels (`REAL-HARDWARE`, `REAL-LMS`, ...). A green software test never replaces one of
these items.

What is already covered without hardware (do not repeat there): decision logic and ownership rules (UNIT + INTEGRATION against real LMS 9.1.2 +
Squeezelite 1.9.9), the D-Bus client and translator against a *fake* BlueZ (SIMULATED-BLUEZ), bit-exact transport, software timings.
Evidence index: `docs/FINDINGS.md`, `reports/HANDOFF_FOR_CHATGPT.md`.

## 0. Rules for the person executing this plan
* Use a **dedicated test Raspberry Pi** and a **dedicated test player/LMS player** first. Run against the owner's production LMS only read-only at first
  (observe), and only with explicit owner consent for the write steps (`playlist play`, `playlist deleteitem`, `power 1`, relative volume).
* Never power-cycle, reconfigure or regroup production players. The bridge needs no such step; if a test seems to require it, stop and ask.
* Record per test: date, hardware (board revision, Bluetooth chip, adapter/dongle, phone model + OS version + app + version), software versions
  (`bluetoothd -v`, `pipewire --version`, `wireplumber --version`, LMS version, player firmware/Squeezelite version, bridge git commit), exact steps,
  observed result, evidence files (traces, logs with tokens redacted, recordings), PASS / FAIL / PARTIAL.
* Never put the stream token, Wi-Fi credentials or MAC addresses of third parties into reports.
* Timing numbers come from a **capture rig** (microphone or line-level loopback recorded together with a timing mark). Without a rig, a value is
  *not measured*, never "approximately X".

## 1. Rig and matrix
| Item | Minimum | Preferred |
|---|---|---|
| Bridge boards | Raspberry Pi 3 B+ on Ethernet (reference) | + Pi Zero 2 W (Wi-Fi only) and Pi 4 |
| Image | any Linux with BlueZ >= 5.6x + PipeWire + WirePlumber (a stock Raspberry Pi OS Lite is fine for validation; **not** the appliance image) | the Buildroot image once it exists |
| Phones | 1 Android (record version/vendor) and 1 iPhone (record iOS version) | 2 Android vendors + 2 iOS versions |
| Phone apps | one music-streaming app, YouTube (audio), a messenger (notification sounds), a navigation app (voice prompts), a silent video | + a podcast app, a radio app |
| Lyrion | owner's LMS (read-only first) or a throw-away test LMS | both |
| Players | Squeezelite on a separate PC/Pi (REAL player) | + every real Squeezebox the owner owns, by **model and firmware** (e.g. Classic/SB3, Receiver, Boom, Touch, Radio) |
| Capture rig | USB audio interface or phone recording at the speaker + a click/marker | line-level loopback from the player's output |

## 2. Test sets
IDs are stable so results can be cross-referenced in handoffs. "Decision" = what the result feeds.

### A. BlueZ / PipeWire bring-up (finishes docs/BLUEZ_ADAPTER_DESIGN.md)
| ID | Test | Pass criterion | Decision |
|---|---|---|---|
| A1 | Record `bluetoothd` and PipeWire/WirePlumber versions; start bluetoothd with/without `--experimental` | `org.bluez.MediaPlayer1` visible for a connected phone; note whether `--experimental` is required | image config |
| A2 | Confirm the A2DP **sink** role is enabled in WirePlumber for the phone-as-source case | phone can select the bridge as speaker | image config |
| A3 | With a phone connected and playing, dump PipeWire nodes (`pw-dump`) | identify the node name/properties for the phone's audio (G1 2026-11-24: `bluez_input.<MAC_>.2`; an Audio/Source only with the role rule `bluez5.media-source-role = input`) | `find_phone_node()` (pw-dump: address + profile a2dp-source + media.class) |
| A4 | Run the capture command from `adapters/bluez/pcm.py` for that node: does `pw-cat --record ... -` write **headerless** s16 PCM? Compare with `parec_argv` | raw 44.1 kHz s16 stereo bytes, no header | which tool, flags |
| A5 | Negotiated codec and sample rate per phone (SBC baseline; AAC if exposed) | recorded | resampling need |
| A6 | `JeepneyBus` against the real system bus: GetManagedObjects, signals, property writes, agent registration (`journalctl -u bluetooth`) | no errors; same events as the fake | D-Bus library choice for the image |
| A7 | Restart bluetoothd while a phone is connected | adapter reports disconnect, re-registers the agent, re-syncs | robustness |

### B. Pairing and connection
| ID | Test | Pass criterion |
|---|---|---|
| B1 | Open the pairing window (adapter `Pairable`/`Discoverable`), pair Android and iPhone with Just-Works, no PIN | works within the window; window closes at 5 min (`Pairable=false`) |
| B2 | Pair attempt after the window closed | rejected by the agent |
| B3 | Reconnect of a paired phone without opening the window | connects, no LMS audio started by connecting alone |
| B4 | Second phone connects while the first is connected (and while it plays) | second is disconnected; first keeps playing; never evicted |
| B5 | Remove a paired device; pair again | clean |
| B6 | Phone walks out of range / BT off / phone reboot | disconnect detected; bridge stream released immediately |
| B7 | Devices that are not phones (headset, keyboard) connect | ignored, no stream |

### C. Media state traces (answers OPEN_QUESTIONS #2; feeds `core/fusion.py`)
| ID | Test | Pass criterion |
|---|---|---|
| C1 | Android: record traces (`python3 -m hwkit trace`) for play, pause, resume, track change, seek, app switch, screen lock, quiet passage (`bab_quiet_passage`), messenger notification, ringtone, navigation prompt, silent video, phone call, phone without AVRCP player | trace files + markers `expect:media` / `expect:no-media`; `hwkit replay` reports every expectation |
| C2 | iPhone: the same scenarios as C1 | as C1 |
| C3 | Decide the fusion rules from C1/C2: which signals separate music from notifications? Is the 2 s filter enough? Do apps leave the transport `active` while paused? | decision recorded; real traces added as regression fixtures (`tests/fixtures/traces/`, label REAL-HARDWARE) |

### D. Volume (answers OPEN_QUESTIONS #4)
| ID | Test | Pass criterion |
|---|---|---|
| D1 | Android: is `MediaTransport1.Volume` sent on connect and on slider changes? Scale 0-127? What happens without absolute volume? | trace + LMS volume log; no loudness jump |
| D2 | iPhone: as D1 | as D1 |
| D3 | Sync group of real players: relative room levels preserved; ceiling 85 and max step 10 (provisional) sensible | measured steps per slider move |

### E. Audio integrity, timing, resources (measure with the capture rig)
| ID | Test | Record |
|---|---|---|
| E1 | Play a known test track (sine sweep / click track) from each phone for 30 min through the bridge to a real player | dropouts, clicks, discontinuities (cross-correlate recording vs reference); BT SBC loss is expected - report it, do not hide it |
| E2 | Play -> first sound, Pause -> silence, Resume -> sound (>= 10 runs each, per phone/player/board/format WAV and FLAC) | median/min/max with the rig; compare with the **software** numbers (1.55 s / 0.07 s / 1.55 s) and the owner's acceptance of ~1.6 s (DECISIONS #16) |
| E3 | Drift/soak: 4 h continuous playback per board | feeder silence-inserts/drops, underruns, memory growth |
| E4 | CPU, RSS, temperature, throttling per board while streaming WAV and FLAC | numbers; the x86 figures in FINDINGS.md are **not** transferable |
| E5 | Pi Zero 2 W: Wi-Fi + Bluetooth coexistence on a busy 2.4 GHz band with the ~1.4 Mbit/s stream | stutters, BT dropouts, stream underruns; decide if Zero 2 W is a supported target |
| E6 | Ethernet vs Wi-Fi on the Pi 3 B+ | as E5 |

### F. Lyrion and players (answers OPEN_QUESTIONS #1, #9)
| ID | Test | Record |
|---|---|---|
| F1 | Real Squeezelite on separate hardware (different version from 1.9.9 if possible) | live stream plays; sample rate; sync |
| F2 | **Each real Squeezebox model/firmware:** WAV live stream; FLAC; pause; startup time; wake-from-standby (`power 1`); behaviour with the ICY option **on and off** | per model PASS/FAIL; decide the ICY default (DECISIONS #18 keeps it off until then) and whether a transcoding rule is needed |
| F2b | **Live MP3 320 kbit/s + ICY titles (DECISIONS #147, docs/PILOT.md section 9; experimental):** the owner's own sync group, WAV vs MP3, 10-15 min each; then titles on the Lyrion web UI, the Classic, both ESP32 and the Nest displays | per codec: stalls (count, length, Lyrion log), first audio after Play, `lame` CPU/RAM from `mp3-check`, `throttled`; per display: what it shows. Decides whether MP3 stays an option; nothing is assumed beforehand |
| F3 | Sync group with mixed real players, target = master and member | sync offset (rig), regroup never performed |
| F4 | Ownership races on the owner's real LMS (with consent): switch to a real radio station then late BT events; two bridges on one player; LMS restart; player offline | zero LMS commands after ownership loss; radio never touched |
| F5 | LMS server version differences (record version) | compatibility of `playlist play <url>` / `playlist deleteitem <url>` |

### G. Guest walk-through (the top product goal, DECISIONS #19/#24)
| ID | Test | Pass criterion |
|---|---|---|
| G1 | A person who has never seen the system finds the Bluetooth name, pairs, connects, opens their usual audio app, presses Play, hears music from the selected Lyrion players, pauses, leaves. No Lyrion, no Home Assistant, no app. | no help needed; every point of confusion recorded; time from "Play" to sound |

### H. Local access and owner UI on real devices (added 2026-11-16)
| ID | Test | Pass criterion |
|---|---|---|
| H1 | `hifi-bridge.local` from iPhone, Android, Windows and macOS (Avahi backend on the Pi: Secure-access page shows "avahi") | resolves on every client; record which ones need a second try |
| H2 | Install the device CA on iPhone and Android per the trust page; open `https://hifi-bridge.local:<port>/` | lock without warning; fingerprint matches the console/`hifi-status.txt`; HTTPS-only can then be enabled |
| H3 | Add the WebUI to the home screen (iOS "Add to Home Screen", Android "Install app") | icon and name "Hi-Fi Bridge" appear; opens standalone |
| H4 | Manual screen-reader pass: VoiceOver (iOS) and TalkBack (Android) through setup, pairing and status | every control announced with a name; errors read out; no trap |

## 3. What each result feeds
* A3/A4 -> final PipeWire capture command and node resolution (`pcm.py`); A6 -> D-Bus library for the image.
* C1-C3 -> `fusion` rules and the notification filter duration.
* D1-D3 -> `VolumePolicy` parameters.
* E2 -> confirm or revisit DECISIONS #15/#16 (pause strategy, ~1.6 s start latency, plugin question).
* E3-E6 -> supported boards and resource budget.
* F2 -> ICY default, FLAC/PCM format rules, list of verified players (never claim compatibility beyond it).
* H1-H4 -> mDNS backend default, trust-setup wording, accessibility fixes (OPEN_QUESTIONS #33).

## 4. Not testable without hardware (summary)
Real Bluetooth radio and phone behaviour (AVRCP, absolute volume, codecs), BlueZ/PipeWire/WirePlumber on a Pi, acoustic latency, speaker timing, Wi-Fi/BT
coexistence, Raspberry Pi CPU/RAM/thermal figures, real Squeezebox compatibility, power-failure and boot behaviour.
