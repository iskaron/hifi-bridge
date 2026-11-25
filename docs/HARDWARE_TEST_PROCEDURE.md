# Hardware test procedure (the concrete sequence for the first hardware day)

**Status: prepared, NOT executed.** This is gate **G1** of docs/HARDWARE_GATE_CHECKLIST.md. Everything needed is in `hwkit/` (runbook `hwkit/README.md`). The test IDs come from
`docs/HARDWARE_VALIDATION_PLAN.md`. Every result entered with the kit is labelled `REAL-HARDWARE`, and the kit refuses anything else.

## 0. Bring
* Raspberry Pi 3 B+ (reference), power supply, microSD card (16 GB recommended; also one nominal 2 GB card for P4-1, DECISIONS #120), Ethernet cable. Optional: Pi Zero 2 W for E5.
* One Android phone and one iPhone, with a music app, a messenger, navigation and YouTube installed.
* One target player: Squeezelite on a PC or Pi, or a real Squeezebox (note model and firmware).
* Access to a **test** LMS, or the owner's LMS used read-only until F4 (plan §0).
* For E2/F3: a USB microphone on the Pi, placed about 30 cm in front of the speaker.

## 1. Prepare (about 45 min)
1. On the development machine (Docker): run `scripts/hwkit_bundle.sh`. It produces `dist/hwkit-bundle-<rev>.tar.gz` and `dist/hwkit-tracks/`.
2. Copy the `.m4a` test tracks to both phones.
3. Flash a stock **Raspberry Pi OS Lite** (not the appliance image) and copy the bundle to the Pi.
4. On the Pi: `sudo sh hwkit/pi-setup/setup_test_pi.sh --i-am-on-the-test-pi`, then reboot.
5. On the Pi: `PYTHONPATH=$PWD/bridge:$PWD/hwkit python3 -m hwkit preflight`. Fix every **FAIL** before you continue. Each check prints
   its own fix. It also shows which capture tool the bridge would use there (`pw-cat --raw` on PipeWire ≥ 1.x, otherwise `parec`).
6. Start the bridge on the Pi: `python3 -m btbridge.appliance --data ~/bridge-data --source bluez --boot-dir /boot/firmware`
   (Raspberry Pi OS: Avahi runs already, so the name is published through it; `hifi-status.txt` on the boot partition shows the CA fingerprint.
   `/boot/firmware` belongs to root: without write access the bridge reports "boot files NOT processed" and starts anyway; the console log
   then has the setup-code path and the fingerprint). Open `http://<pi>:8080/`, enter the setup
   code from `~/bridge-data/setup-code.txt`, and finish the wizard with the test LMS and the test player.
7. Firmware reference for the appliance image (gate P1-4, DECISIONS #134): on the stock Pi OS run
   `dmesg | grep -E "brcmfmac|Bluetooth: hci0|BCM"` after boot and after the first phone connects; save the output with the results
   (chip revision and the firmware file names this board loads).
8. Create the results file: `python3 -m hwkit results new results.json --board "<model rev>" --operator <name> --phone "<model / OS>" ...`,
   then `python3 -m hwkit env -o env.json`.

## 2. Run, in this order (about 4 h; `python3 -m hwkit guide results.json --address <phone MAC> [--mic <source>]`)
| Block | IDs | Why first | Time |
|---|---|---|---|
| Bring-up | A1, A2, A3, A4, A5 | Without a working capture node nothing else is meaningful. A3/A4 settle the open PipeWire questions. | 45 min |
| Pairing | B1, B2, B3, B4, B6, B7 | Guest experience and the single-source policy | 40 min |
| Media traces | C1 (Android), C2 (iPhone) | Notification vs. music: the main open software risk | 60 min |
| Volume | D1, D2 | Absolute volume per phone | 20 min |
| Audio quality and timing | E1 (30 min capture), E2 (10 × play/pause with mic) | First real numbers | 50 min |
| Players | F1, F2 (each real Squeezebox) | Compatibility list, ICY decision | as needed |
| Guest | G1 | The product goal | 15 min |
| Owner access | H1, H2, H3 (H4 if time) | name resolution, trusted HTTPS, home-screen shortcut on real phones | 30 min |

The rest (A6, A7, B5, C3, D3, E3–E6, F3–F5) goes on a second day or follows the decisions from day one.

**Stop rules:**
* Stop and report if A3/A4 fail, i.e. no capturable phone node exists. The capture design would need to change; fallback candidates are
  BlueZ `MediaEndpoint1` with in-process SBC decoding, or `pw-cat`.
* Stop and report if B4 shows that a second phone evicts the first.
* Never test against the production LMS beyond read-only observation without the owner's explicit consent (plan §0, F4).

## 3. Analyse (development machine, Docker)
```bash
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit analyze <capture dir>
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit replay <trace.jsonl>
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m hwkit latency <trace.jsonl> <capture dir>
python3 -m hwkit results check results.json && python3 -m hwkit results report results.json > REPORT.md
```

## 4. Bring back (for the next handoff)
`results.json`, `env.json`, `REPORT.md`, and `sessions/**` (traces, captures, `pw-dump`). Traces are anonymised by default. Do not include setup
codes, passwords or the stream token. Real traces become regression fixtures in `tests/fixtures/traces/`, labelled REAL-HARDWARE.

## 5. G1 retest after the PCM-ingress fix (2026-11-24; background: docs/PIPEWIRE_CAPTURE.md "G1")
Keeps the existing pairing, the bridge's data directory (`~/bridge-data`: owner password, settings) and Pi OS as they are; no reflash.
Takes about 30 minutes. Results are REAL-HARDWARE only when you observed them; write down what you heard per room.

**0. On the development machine** (Docker): `scripts/hwkit_bundle.sh`, then `scp dist/hwkit-bundle-<rev>.tar.gz olm@hifi-test.local:`.

**1. Install on the Pi** (stop the running bridge first with Ctrl-C):
```bash
mv ~/hifi-bridge ~/hifi-bridge.previous                       # rollback copy
mkdir ~/hifi-bridge && tar xzf ~/hwkit-bundle-<rev>.tar.gz -C ~/hifi-bridge && cat ~/hifi-bridge/dist/HWKIT_REVISION
sudo sh ~/hifi-bridge/hwkit/pi-setup/setup_test_pi.sh --i-am-on-the-test-pi --headless
```
The script installs nothing new if the packages are there, adds `/etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf`,
rewrites `80-no-logind.conf` with the same content you installed by hand, and restarts your PipeWire/WirePlumber.

**2. Reconnect the phone** (Bluetooth off and on, or disconnect/connect in its Bluetooth menu): the phone's node is created anew with the
input role. Then, read-only:
```bash
cd ~/hifi-bridge && export PYTHONPATH="$PWD/bridge:$PWD/hwkit"
python3 -m hwkit preflight                                    # no FAIL; new: seat-monitoring, a2dp-input-role, bluez5-plugin, audio-sink-uuid
wpctl status                                                  # expected: the phone under "Sources", NO bluez_input stream -> Headphones
pactl list short sources | grep bluez_input                   # expected: bluez_input.<MAC_>.2
```
Stop here and report if the phone is still listed under *Streams* with a link to the headphones.

**3. Volumes first.** In Lyrion set Küche, Arbeitszimmer and esparagus-01 to about 20 % (last known: 88 / 100 / 62 % - check the
current values and write them down to restore them later). Phone media volume about 30 %.

**4. PCM ingress without Lyrion** (bridge still stopped; nothing is sent to any player): start a known track on the phone, then
```bash
python3 -m hwkit capture -o ~/g1-ingress --address <phone MAC> --seconds 20
```
Expected in the printed JSON under `recordings.phone`: `seconds` about 20, `peak_dbfs` above -40 and `nonzero_fraction` above 0.5 while
music plays. Meanwhile nothing may sound from the Pi's headphone jack. `capture: ... playback stream ...` instead means the rule is not
active (step 2). The WAV stays in `~/g1-ingress` (delete it when done; it is a recording).

**5. End to end.** Start the bridge exactly as before:
`python3 -m btbridge.appliance --data "$HOME/bridge-data" --source bluez --boot-dir /boot/firmware`. The console shows
`capture tool: ...; capture target: the phone's Audio/Source node found with pw-dump`. Press Play on the phone: expected console lines
`transport pending (<MAC>)` / `transport active (<MAC>)` and no `capture waiting` / `capture child exited` lines. Listen in each room
of the group and note per room: audible yes/no, in sync yes/no.

**6. Behaviour** (note each result): pause 10 s and resume; pause 60 s and resume; phone Bluetooth off and on (reconnect); next/previous
track; while the phone is paused, start music in Lyrion by hand (manual source priority - the bridge must not take it over); WebUI
*Diagnostics* → download the bundle: `audio_capture` shows `state` and `transport`; the bridge console has no crash and no endless
`capture ...` messages.

**7. Restore** the Lyrion volumes from step 3.

**Bring back:** the console output of the bridge, `wpctl status` (during playback), `python3 -m hwkit preflight`, the JSON of step 4, the
diagnostics bundle, and on failure also `journalctl --user -u wireplumber -b --no-pager | tail -100` and `pw-dump > pw-dump.json`.

**Rollback** (back to `b648936` and the PipeWire configuration before this retest):
```bash
sudo rm /etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf
systemctl --user restart wireplumber pipewire pipewire-pulse
rm -rf ~/hifi-bridge && mv ~/hifi-bridge.previous ~/hifi-bridge
```

## 6. G1-S: audio-path trace during the intermittent silence (2026-11-25; background: docs/STREAM_TRACE.md)

Symptom (G1-R day): the music reaches the group, goes silent for ~10-15 s and returns exactly with a new `GET … 206` log line. This
run records WHERE the audio stops. It changes nothing on BlueZ, pairing, codecs or PipeWire, and nothing in Lyrion.

0. On the development host: `scp dist/hwkit-bundle-<rev>.tar.gz olm@hifi-test.local:`
1. On the Pi (stop the running bridge with Ctrl-C first; the PipeWire setup of section 5 stays as it is):
   ```sh
   mv ~/hifi-bridge ~/hifi-bridge.g1r && mkdir ~/hifi-bridge && tar xzf ~/hwkit-bundle-<rev>.tar.gz -C ~/hifi-bridge && cat ~/hifi-bridge/dist/HWKIT_REVISION
   ```
2. Start the bridge exactly as before, with the trace switched on, and keep a copy of the console:
   ```sh
   cd ~/hifi-bridge && export PYTHONPATH="$PWD/bridge:$PWD/hwkit"
   HIFI_STREAM_TRACE=1 python3 -m btbridge.appliance --data "$HOME/bridge-data" --source bluez --boot-dir /boot/firmware 2>&1 | tee ~/g1-trace.log
   ```
   Expect `[trace] audio-path trace ON` and then one `[trace] … in: … | out: … | lms: …` line per second.
3. Lyrion group at ~20 %, phone at ~30 %; play music for **at least 10 minutes**. Note the clock time (HH:MM:SS) of each silence and
   of its end. Do not run `hwkit capture` at the same time (one change at a time).
4. Ctrl-C, then bring back `~/g1-trace.log` (no stream token, no URL, no addresses; MACs are pseudonymised as in every bridge log) and
   two facts: is the Pi on Wi-Fi or Ethernet, and which of Küche / Arbeitszimmer / esparagus-01 are on Wi-Fi. Optional and read-only:
   Lyrion web UI → Settings → Information (player list, signal strength), and Lyrion's server log around the noted times
   ("Unexpected data stream disconnect", "RETRYING").

The fields that decide it (docs/STREAM_TRACE.md "Reading an outage"): `in: … nz=` (did the bridge's own capture carry audio), `out: rN:…B
… blk=… STUCK` (could the bridge deliver), `rN end reason=…` and the next `rN+1 start GET 206 range=…` (when and how LMS restarted).
Rollback: `rm -rf ~/hifi-bridge && mv ~/hifi-bridge.g1r ~/hifi-bridge` (without `HIFI_STREAM_TRACE` the trace is off anyway).

## 7. G1-S round 2: name the member that stalls the group (2026-11-25; background: reports/G1S_THREE_ROOM_STALLS.md)

Round 1 (section 6) showed: the bridge's capture and feeder carried live audio the whole time; Lyrion stopped reading, the group's
`time` froze, and after ~30 s Lyrion restarted the stream - Lyrion's sync-group **rebuffer give-up** (docs/STREAM_TRACE.md "Why Lyrion
stops reading"). Round 2 finds **which member** triggers it. Nothing changes on BlueZ, pairing, codecs, PipeWire, Home Assistant or the
network. Lyrion changes are made by the owner only: a temporary log level (step 2) and the group membership (step 4), both reversed at
the end. Keep the Yamaha power-off automation disabled as in the previous runs.

0. On the development host: `scp dist/hwkit-bundle-<rev>.tar.gz olm@hifi-test.local:`
1. On the Pi (Ctrl-C the running bridge first):
   ```sh
   mv ~/hifi-bridge ~/hifi-bridge.3a74653 && mkdir ~/hifi-bridge && tar xzf ~/hwkit-bundle-<rev>.tar.gz -C ~/hifi-bridge && cat ~/hifi-bridge/dist/HWKIT_REVISION
   ```
2. Lyrion: switch on the log category that names the member (runtime only - Lyrion does not save it; a Lyrion restart also resets it).
   `LMS` is the Lyrion address the bridge uses, e.g. `http://192.168.1.10:9000`:
   ```sh
   LMS=http://<lyrion-host>:9000
   curl -s -d '{"id":1,"method":"slim.request","params":["",["debug","player.source","?"]]}' $LMS/jsonrpc.js    # note "_value" (default WARN)
   curl -s -d '{"id":1,"method":"slim.request","params":["",["debug","player.source","INFO"]]}' $LMS/jsonrpc.js
   ```
3. Start the bridge with the trace, one log per run:
   ```sh
   cd ~/hifi-bridge && export PYTHONPATH="$PWD/bridge:$PWD/hwkit"
   HIFI_STREAM_TRACE=1 python3 -m btbridge.appliance --data "$HOME/bridge-data" --source bluez --boot-dir /boot/firmware 2>&1 | tee ~/g1s2-run1.log
   ```
   Expect the summary lines to end in `| members: Küche:ok …, Arbeitszimmer:ok …, esparagus-01:ok …`.
4. Runs, one variable at a time; each **10 minutes** of music, or until the third silence. Pause the phone, change the group in Lyrion,
   check that the bridge's target player (web UI) is a member of the new group, restart the bridge with a new log name, press Play:

   | Run | Group | Discriminates |
   |---|---|---|
   | 1 | Küche + Arbeitszimmer + esparagus-01 (unchanged) | Lyrion's log names the underrunning member (step 5) |
   | 2 | esparagus-01 + Arbeitszimmer | Arbeitszimmer-specific vs. "a third member" |
   | 3 | Küche + Arbeitszimmer | the same, with the other partner |
   | 4 (only if 2 and 3 pass) | all three again | count / load effect |

   Note per run: the clock time of each silence and its end, and which rooms were silent.
5. After each run, fetch Lyrion's log lines (Lyrion's own log contains the stream token - the `sed` removes it):
   ```sh
   curl -s "$LMS/server.log?lines=4000" | grep -E "playerOutputUnderrun|_Rebuffer|jumpToTime|new playing state|playerBufferReady|disconnect|RETRY" \
     | sed -E 's#/s/[^/ ]+/#/s/***/#g' > ~/g1s2-run1-lms.txt
   ```
6. At the end: restore the level noted in step 2 (`…["debug","player.source","WARN"]…`) and the original group.
7. Bring back: `~/g1s2-run*.log`, `~/g1s2-run*-lms.txt`, the noted times, and for Arbeitszimmer: what hardware and OS it runs on, its
   Squeezelite command line (`-b`, `-a`, `-o`) and, if easy, its own log around the times. Also: Pi on Wi-Fi or Ethernet; each player
   on Wi-Fi or Ethernet.

Pass/fail per run: **pass** = 10 min audible in every room of the run AND, after the first 10 s, no `STUCK`, no ` WAITING`, no further
`rN start GET` line and every `members:` entry `ok`. Gate G1-S passes only on a fresh **three-room** run that passes this.
Rollback: `rm -rf ~/hifi-bridge && mv ~/hifi-bridge.3a74653 ~/hifi-bridge`; Lyrion level as in step 6.
