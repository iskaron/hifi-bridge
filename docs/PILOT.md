# Private daily-use pilot on the Pi 3 B+ (stock Raspberry Pi OS Lite) — PROVISIONAL

Status 2026-11-27. **This is a private pilot with known limitations, not a release and not gate G1-S.** Gate G1-S (strict multi-room
hardware acceptance) is **still OPEN**: runs 6, 9, 10 and 11 with two players failed the strict technical criteria, no fresh three-room
run has passed, and the cause of Lyrion's group rebuffering is unknown (reports/G1S_ROUND3_RUNS_6_11.md, OPEN_QUESTIONS #105/#106/#109).
A passed smoke test below means **"private pilot ready"**, nothing more. The version stays `0.3.0-dev` (no bump); the pilot is identified by
the bundle revision (`dist/HWKIT_REVISION`, shown by `pilot_service.sh status`). The Buildroot appliance image is **not** hardware-proven.

## 1. What the pilot adds (and what it does not touch)

The working setup is unchanged: the same code, the same command as the manual hwkit launch, the same `~/bridge-data`, the same BlueZ bonds,
PipeWire/WirePlumber configuration and Lyrion. Added: a **systemd user unit** that starts that command at boot and restarts it.

| Need | How the pilot meets it | Evidence |
|---|---|---|
| start after boot, survive SSH logout | user unit `hifi-bridge-pilot.service` + `loginctl enable-linger` (the user manager runs without a login session) | STATIC (unit verified); boot behaviour **REAL-HARDWARE pending** |
| PipeWire/WirePlumber are per-user on Pi OS Lite | it is a *user* unit (`Wants=` pipewire, wireplumber, pipewire-pulse); the appliance's system unit has no PipeWire session on this OS | STATIC |
| BlueZ and the network are *system* units | a user unit cannot order itself after them: `hifi-bridge-pilot-wait.sh` waits (max 60 s) for BlueZ on the system bus and a default route, **never fails**; the bridge's own reconnect loops do the rest | UNIT (stubs) |
| restart on failure with back-off, no spinning | `Restart=always`, 5 s growing to 60 s (`RestartSteps`/`RestartMaxDelaySec`), start-limit off so it never stays dead | STATIC; real failure behaviour pending |
| hung process | `Type=notify` + `WatchdogSec=60`: the bridge pings only while its control loop is healthy. A degraded Lyrion/MQTT never restarts it (docs/SERVICE_SUPERVISION.md) | UNIT (existing watchdog tests) |
| clean shutdown | SIGTERM → the bridge's async-signal-safe stop (`StopSignal`), `TimeoutStopSec=20` | UNIT (existing tests); on the Pi pending |
| no SD wear from the service | fault history in `%t/hifi-bridge` (tmpfs), no audio is ever stored, tracing stays off; logs go to the journal | STATIC |
| secrets | settings file `0600`, `UMask=0077`, data directory tightened to `0700` by `install` (content untouched); nothing secret is printed | UNIT |

**Not in the unit on purpose:** sandboxing options (`ProtectSystem`, `PrivateDevices`, syscall filters, memory limits) — their behaviour under a
user manager on this Pi is unverified and a silent failure would take the audio path down; they can be added with evidence. **No `--boot-dir`:**
the stock `/boot/firmware` is root-owned, the pilot needs neither provisioning nor recovery files, and the bridge already treats that error
as non-fatal ("boot files NOT processed ... the bridge starts without them"). So the permission error needs **no fix**, and nothing
is `chmod`-ed on `/boot` or any system directory. **No automatic reboot, no unsync, no stream restart** is ever triggered by this unit.

## 2. Files

| File | Purpose |
|---|---|
| `hwkit/pi-setup/pilot_service.sh` | `check`, `install [--start]`, `start`, `stop`, `restart`, `status`, `logs [-f]`, `disable`, `uninstall`. Normal user only; refuses off a Pi; idempotent |
| `hwkit/pi-setup/hifi-bridge-pilot.service` | the unit template (`@APP_DIR@`, `@DATA_DIR@` are filled in) → `~/.config/systemd/user/` |
| `hwkit/pi-setup/hifi-bridge-pilot-wait.sh` | bounded readiness wait (ExecStartPre) |
| `~/.config/hifi-bridge/pilot.env` | optional environment (`0600`, created commented-out, never overwritten); `HIFI_STREAM_TRACE=1` goes here for a trace run |
| `~/.config/hifi-bridge/backup/` | copies of a unit that `install` had to replace |

## 3. Owner commands on `hifi-test` (nothing runs until you run it; the installer never reboots)

```sh
# 0. On the dev machine, after the revision is pushed: scripts/hwkit_bundle.sh, copy dist/hwkit-bundle-<rev>.tar.gz to the Pi.
# 1. On the Pi. FIRST stop the manual launch (Ctrl-C in its terminal): it owns the ports and must not run while its directory is replaced.
#    Keep the working install for the rollback; ~/bridge-data is NOT part of it and is not touched.
cd ~ && mv hifi-bridge hifi-bridge.manual && mkdir hifi-bridge && tar xzf hwkit-bundle-<rev>.tar.gz -C hifi-bridge
cd ~/hifi-bridge && sh hwkit/pi-setup/pilot_service.sh check           # read-only; fix every FAIL, read every WARN
# 2. Install and start (the manual launch is stopped by now):
sh hwkit/pi-setup/pilot_service.sh install --start                      # asks for sudo once, only to enable linger
sh hwkit/pi-setup/pilot_service.sh status                               # state, restarts, /healthz, last log lines
```
If the PipeWire A2DP-input / headless WirePlumber files are missing (`check` warns), they come from the existing
`sudo sh hwkit/pi-setup/setup_test_pi.sh --i-am-on-the-test-pi` — on a Pi that already runs the hwkit setup nothing is needed.

**Rollback to the manual launch (any time):**
```sh
sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh disable      # stops + disables; prints the manual command; unit and settings stay
# or, to also go back to the previous code:  rm -rf ~/hifi-bridge && mv ~/hifi-bridge.manual ~/hifi-bridge
# full removal of the unit: pilot_service.sh uninstall (keeps pilot.env, backups and ~/bridge-data; prints the linger-off command if the installer set it)
```
To change a setting such as the trace: edit `~/.config/hifi-bridge/pilot.env`, then `pilot_service.sh restart`. Logs: `pilot_service.sh logs -f`.

## 4. Not claimed

I (the development session) have **not** run any of this on the Pi: there is no access to it. Verified in the development repository only:
the installer's file handling (UNIT, dry run), the unit's directives and syntax (STATIC `systemd-analyze --user verify`, systemd 257), the
wait helper (UNIT with stubs) and the existing watchdog/stop-signal behaviour. **Not verified:** that systemd on the Pi starts the unit at
boot, that linger and the PipeWire user services come up in the right order, audio under the unit, a real crash/watchdog restart.
The smoke test below is exactly that evidence.

## 5. Pilot smoke test (owner-executed, ~30 minutes, not a G1-S campaign)

Do not change HA automations, the Lyrion master, ESP32 Wi-Fi/buffer settings or BlueZ bonds. Note date/time and the bundle revision.

| # | Step | Pass if |
|---|---|---|
| S0 | `pilot_service.sh check`, then `install --start` | no FAIL; `status` shows `ActiveState=active`, `NRestarts=0`, `/healthz` answers |
| S1 | `sudo reboot`; wait 2 minutes; do **not** SSH-login or start anything | WebUI reachable at the usual address; `status` (login now) shows `active`, started shortly after boot; log has `pilot-wait: bluez=yes default-route=yes` |
| S2 | Kitchen = target. Phone (Pixel, already paired) connects by itself, press Play | audio in the kitchen; phone shows connected; no pairing window needed |
| S3 | Play **10 minutes** kitchen solo | continuous; in `logs`: no `watchdog: health failed`, no restart; the WebUI shows playing |
| S4 | Pause on the phone, wait for the bridge to release; then Play again | resumes without touching the WebUI |
| S5 | Intentional stop: `pilot_service.sh stop`, then `start` | the kitchen stops cleanly (no stuck item in Lyrion), the phone is not asked to pair again, Play works after `start` |
| S6 | While the phone plays: `pilot_service.sh restart` | the stream returns within about a minute; the phone stays paired; the next Play works |
| S7 | Start something **else** in Lyrion on the kitchen (a playlist, radio) while the bridge is idle, then connect the phone and press Play | Lyrion's own source keeps priority as designed (docs/PLAYBACK_STATES.md); it is not removed or cleared |
| S8 | Pairing window: open it in the WebUI; it closes after 5 minutes by itself | unchanged behaviour; no discoverability afterwards |
| S9 | optional: living-room solo (ESP32) 10 minutes | as S3 |
| S10 | **optional, known risk:** two-player sync (Kitchen master + living room) | audible only; **not** an acceptance criterion. A dropout is the known G1-S issue (record time + `logs`) |

Bring back, if anything fails: `pilot_service.sh status` and `logs` (no secrets), which step, clock time. **Do not** reboot or unsync as a
workaround for a stall; the unit never does. Pass S0–S8 = **private pilot ready**.

## 6. Feature inventory (none of these blocks the pilot)

| Feature | State | Evidence | Pilot default |
|---|---|---|---|
| WebUI, owner auth, setup wizard, persistent `~/bridge-data` | implemented, working on the Pi | REAL-HARDWARE (manual runs) | on |
| 5-minute pairing window, one-phone policy, passive reconnect | implemented | UNIT/INTEGRATION; Pi pairing worked | unchanged |
| Manual Lyrion source priority | implemented | INTEGRATION (lab LMS); S7 checks it on the Pi | unchanged |
| Diagnostics: `/healthz`, opt-in stream trace | implemented | UNIT/INTEGRATION; trace REAL-HARDWARE | trace **off** |
| MQTT + Home Assistant discovery, 14 entities, read-only by default | implemented; **not verified on the owner's live HA** | INTEGRATION (Mosquitto, `test_11_mqtt_mosquitto.py`) | **off** (`mqtt_enabled=false`) |
| ICY metadata to Lyrion | implemented; **off by default** for legacy Squeezebox compatibility (DECISIONS #18); the kitchen is a Squeezebox Classic. Two separate switches since DECISIONS #147: `icy_metadata` (WAV, as before) and `icy_mp3` (MP3 only) | UNIT/INTEGRATION; no legacy-hardware test | **off** |
| Live MP3 320 kbit/s CBR stream mode (experimental, bridge-encoded with `lame`) | implemented (DECISIONS #147, docs/MP3_AND_METADATA.md); needs `sudo apt install lame`; **not hardware-verified, not a proven fix** | UNIT/INTEGRATION (real Lyrion 9.1.2) | **WAV** (unchanged) |
| Dynamic 6-digit SSP numeric comparison via the WebUI | **not implemented** (docs/PAIRING_CODE_FEASIBILITY.md) | analysis only | n/a |
| Audio-stall alert in the WebUI | design only (docs/STALL_ALERT_DESIGN.md) | — | n/a |
| Buildroot appliance image, A/B update, flash tool | development images BUILD-VERIFIED in Docker for three boards (os/README.md); **nothing has ever been booted** | not hardware-proven | n/a |

If, **after the pilot is proven and only on the owner's explicit request**, MQTT/HA or ICY on **WAV** is wanted, use a separate reversible checklist
(not part of this pilot): *MQTT:* the WebUI's MQTT settings (host, user, password); watch `status`/`logs`; verify the entities appear in HA;
revert = disable MQTT in the WebUI. **Unverified:** whether the retained discovery messages then disappear from the broker; if entities linger in HA,
list them with `mosquitto_sub -t 'homeassistant/#' -v -W 5` and clear them on the owner's broker (`mosquitto_pub -r -n -t <topic>`). *ICY:* never on while a Squeezebox Classic is the target; if wanted, test with the living-room ESP32 first
(WebUI → playback → ICY metadata), watch for a stuck/garbled stream, revert = switch it off and `restart`.

## 7. Known limitations

* G1-S open; multi-room (two or three players) may rebuffer for several seconds; the cause is unknown. Solo rooms passed long tests.
* A bridge restart (watchdog, crash, `restart`) interrupts playback for up to about a minute.
* Linger needs `sudo` once; without it the unit starts only while someone is logged in.
* The unit's sandboxing is minimal (see section 1); the Pi's real systemd behaviour is unverified until the smoke test.
* Not a public release: no tag, no `main` merge.

## 8a. (see section 9 for the MP3 / titles trial)

## 8. After the pilot is stable: the appliance image

Next real-hardware step (gate G2, docs/HARDWARE_GATE_CHECKLIST.md, docs/IMAGE_BUILD.md): build the image on the development machine, write it
to a **spare** SD card with the flash tool (never the pilot's card), boot a second Pi 3 B+ (or the same Pi from the spare card), and check
the first-boot flow, A/B update with rollback, a power cut, and the audio path on the *image's* PipeWire. Do this only after S0–S8 pass and
the pilot has run for a few days; keep the pilot card untouched as the fallback.


## 9. MP3 320 kbit/s + song titles: owner trial (REAL-HARDWARE, not done yet; an experiment, not a G1-S campaign)

What this tests: whether a 4.4× smaller continuous stream (320 kbit/s instead of WAV's 1411 kbit/s) behaves better in **your** current sync
group, and whether artist/title shows on Lyrion and the players. **Not** a proven fix; the lab cannot say. Details and the lab evidence:
docs/MP3_AND_METADATA.md. Settings live in the WebUI (Settings → Playback) and are stored in `~/bridge-data` — the unit file does not change.

### 9.1 Update (WAV stays the default; nothing changes until you switch the format)

```sh
# dev machine: scripts/hwkit_bundle.sh  -> dist/hwkit-bundle-<rev>.tar.gz ; copy it to the Pi
# Pi (as the normal user; ~/bridge-data, bonds, WebUI setup and pilot.env are NOT touched):
sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh stop                       # music for this Pi stops; Lyrion and the other players are untouched
mv ~/hifi-bridge ~/hifi-bridge.prev-<oldrev>                              # the rollback copy (keep it)
mkdir ~/hifi-bridge && tar -xzf ~/hwkit-bundle-<rev>.tar.gz -C ~/hifi-bridge   # same layout as the first install (PILOT.md section 3)
sudo apt install lame                                                      # the ONLY system change; WAV/FLAC need no encoder
sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh check                     # expect OK for lame; fix any FAIL first
sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh mp3-check --seconds 30    # prints encoder CPU (% of one core), RAM, kbit/s: write the numbers down
sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh install --start           # idempotent: same unit, restarts the service on the new code
curl -s http://127.0.0.1:8080/healthz                                      # still "ok"; the stream format is still WAV
```

### 9.2 The A/B (same group, same settings, 10–15 minutes per codec; no other matrix)

Use the group you actually listen with; do not change members, master, volumes, or Lyrion's Internet Radio buffer (**stays 3**). Switch
tracing on once (it is off by default): in `~/.config/hifi-bridge/pilot.env` remove the `#` from `HIFI_STREAM_TRACE=1`, then
`pilot_service.sh restart`. Do **A** (WAV), then **B** (MP3), same phone, same music:

| Step | A: WAV | B: MP3 |
|---|---|---|
| format | WebUI → Playback → *WAV / PCM*, ICY **off** → Save (a running session of the bridge restarts; nobody else's does) | *MP3 320 kbit/s (experimental)*, ICY off → Save |
| play | 10–15 min, listen, note each audible gap with the clock time | same |
| Pi, during the run | `curl -s localhost:8080/healthz`; `ps -o pid,pcpu,rss,args -C python3,lame,pipewire,wireplumber`; `vcgencmd get_throttled` (expect `0x0`) | same (+ the `lame` row) |
| trace | `sh …/pilot_service.sh logs \| grep '\[trace\]' > ~/trace-A.log` (B: `trace-B.log`): `out:` bytes/s (WAV ≈ 176400, MP3 ≈ 40000), `blk`, `STUCK`, `WAITING`, `lame-cpu` | same |
| Lyrion (host `bart`) | in `server.log`, note the time stamps of `playerOutputUnderrun`, `BUFFERING`, `WAITING_TO_SYNC`, `PLAYING` for those minutes | same |

Compare: count and length of the stalls, first audio after Play (MP3 is expected to start later: about +2 s in the lab), CPU of
`python3`/`lame`, `throttled`. One run per codec is an indication, not proof; repeat only if it is close.

### 9.3 Titles (separate from the A/B, afterwards)

Format MP3, tick **“Send track titles in MP3 streams (ICY, test)”** → Save. Play from the Pixel and change tracks. Look at: the Lyrion web UI
(Now Playing), the **Küche** Classic's screen, both ESP32 screens, the Nest displays — and write down *what each shows* (artist - title, only
"Hi-Fi Bridge", or "Bluetooth"). Expected from the lab: Lyrion shows `Artist - Title` within about 3 s of a change without a gap. Whether the
Classic, the ESP32 and the Nests show it is exactly what is unknown. A device that stays on “Hi-Fi Bridge” while Lyrion's web UI shows the title
points at that device's integration, not at the bridge. **Never tick the WAV ICY box for this.**

### 9.4 Restore and rollback

* Back to the old behaviour at once: WebUI → Playback → *WAV / PCM*, untick both ICY boxes → Save (restarts only the bridge's own session).
* Back to the previous code: `sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh stop; rm -rf ~/hifi-bridge; mv ~/hifi-bridge.prev-<oldrev> ~/hifi-bridge; sh ~/hifi-bridge/hwkit/pi-setup/pilot_service.sh install --start`
  (the older code does not know `mp3`/`icy_mp3`: by the config store's rollback rule it runs with its defaults — WAV, ICY off — and keeps the file's values untouched, so a later update finds them again; to be safe set the format to WAV in the WebUI before going back).
* Restore your own state after the test: the preferred sync group (set only by you), Internet Radio buffer **3**, `HIFI_STREAM_TRACE` off
  again (re-comment it, `restart`). `lame` can stay installed; it is inert in WAV mode.
* If the MP3 mode misbehaves (silence, repeated reconnects): switch to WAV first, then collect `pilot_service.sh logs` and the trace.
