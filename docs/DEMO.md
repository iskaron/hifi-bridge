# Interactive demo (`make demo`)

Try Hi-Fi Bridge on an ordinary Linux notebook - no Bluetooth adapter, no Raspberry Pi, no speakers needed. German short version:
[DEMO.de.md](DEMO.de.md).

| Real (the same software as in the product and in a home) | **SIMULATED** (clearly marked in every demo page) |
|---|---|
| the bridge software, **unchanged**, with its BlueZ adapter (`--source bluez`), WebUI, HTTPS, MQTT | the phones ("Annas Smartphone", "Bens Smartphone") |
| PipeWire 1.4 and the bridge's own capture command (`pw-cat --raw`) | the Bluetooth radio and BlueZ (`bluetoothd` stand-in on a private D-Bus) |
| Lyrion Media Server with three Squeezelite players: Wohnzimmer, Schlafzimmer, Arbeitszimmer | the rooms' speakers (a virtual DAC per player) |
| Mosquitto; Home Assistant (optional) | the "internet radio" (a generated programme on the internal network) |
| the flash-tool core that provisions the setup code and trust key | the SD card (a Docker volume) |

What you hear in the audio monitor is the **output of the Squeezelite players behind Lyrion** - not the phone's source signal.
Nothing in this demo proves real Bluetooth, real phones or a Raspberry Pi: that is the hardware gate (`docs/HARDWARE_GATE_CHECKLIST.md`).

## 1. Start
Requirements on the host: Docker with Compose v2, `make`. Nothing is installed on the host; no privileged containers; no hardware is touched.
```bash
make demo          # build + start, then print all URLs and the one-time setup code
make demo-status   # status, URLs, setup code (until used), CA fingerprint, MQTT credentials, CPU/RAM
make demo-logs     # follow the logs (SERVICE=hifi-bridge make demo-logs for one service)
make demo-stop     # stop; all data stays (settings, pairings, Lyrion, uploads)
make demo-reset    # after typing RESET: delete ONLY the demo's own data (compose project "hifi-demo")
make demo-trust    # verify the bridge's CA and save it to .demo/hifi-bridge-ca.pem (section 3)
make demo-recover  # forgot the owner password: SD-card recovery, new one-time setup code
make demo-ha       # additionally start Home Assistant, onboard it and add the MQTT integration
```

| Interface | Default URL |
|---|---|
| Bridge WebUI | http://127.0.0.1:18080 and https://127.0.0.1:18443 |
| Demo console (overview) | http://127.0.0.1:18100 - phones: `/phones`, audio monitor: `/monitor` |
| Lyrion Media Server | http://127.0.0.1:19000 |
| Home Assistant (after `make demo-ha`) | http://127.0.0.1:18123 |

**Configuration** (environment variables for `make demo`): `DEMO_HTTP_PORT`, `DEMO_HTTPS_PORT`, `DEMO_CONSOLE_PORT`, `DEMO_LMS_PORT`,
`DEMO_HA_PORT` (ports), `DEMO_BIND` (default `127.0.0.1`; only change it if you understand that the demo then becomes reachable from your
network), `DEMO_HOST` (the host name printed in links), `DEMO_SUBNET_DEMO` / `DEMO_SUBNET_EDGE` (the two Docker subnets, default
`172.29.251.0/24` / `172.29.250.0/24`). `make demo` checks the ports first and tells you which one is busy.

**Isolation.** The demo is the compose project `hifi-demo` with its own networks and volumes; it never touches other containers. All services
sit on an `internal` network on which the host has **no address**: they can reach neither the internet nor your LAN nor services on your
computer (for example a real Lyrion server - its discovery answers would otherwise reach the demo's setup wizard; found and fixed while
building the demo). Only a small gateway publishes the ports above, bound to 127.0.0.1.

**Resources** (measured on the development machine): about 430 MB RAM for the whole demo while playing (bridge ~65 MB, Lyrion ~125 MB,
console ~150 MB, each room ~25 MB), a few percent CPU. Images: `hifi-demo:dev` ~395 MB (one image for all demo services), plus Lyrion,
Mosquitto and - only with `make demo-ha` - Home Assistant.

## 2. First setup (Erstinstallation)
1. `make demo` prints the **setup code** (it came from the flash-tool step, exactly as for a real card).
2. Open http://127.0.0.1:18080. Enter the setup code and choose an owner password.
3. Bluetooth name: for example "Hi-Fi Bridge Wohnzimmer" - the name guests will see.
4. Lyrion: the demo's server is found automatically; choose it.
5. Player: choose **Wohnzimmer** (or an existing synchronised group).
6. Finish. The dashboard shows "Ready".

## 3. Trusted HTTPS (the device's own CA)
The bridge creates its own certificate authority. You trust it once after **checking its fingerprint out of band** - never by clicking away a
browser warning.
* **Before the setup** (the flash tool's way, docs/TRUST_BOOTSTRAP.md): `make demo-trust` proves with the one-time trust key from the card
  that the CA belongs to this device and checks HTTPS with only that CA. It prints `VERIFIED` and saves `.demo/hifi-bridge-ca.pem`.
* **After the setup** the one-time key is deleted (by design). `make demo-trust` then downloads the CA and compares its fingerprint with the
  one on the device's boot partition (`hifi-status.txt`) - the manual method. It saves the file only if they match.
* Import `.demo/hifi-bridge-ca.pem`: Firefox: Settings -> Privacy & Security -> Certificates -> View Certificates -> Authorities -> Import,
  tick "Trust this CA to identify websites". Chromium/Chrome: Settings -> Privacy and security -> Security -> Manage certificates ->
  Authorities -> Import. Then open https://127.0.0.1:18443 (the certificate contains `127.0.0.1`: the demo pre-sets it as an additional
  host name under Settings -> Secure access).
* The CA is name-constrained to local names and private addresses: it cannot vouch for any internet site. Remove it from the browser when
  you are done with the demo.

## 4. Things to try
Open the bridge, the phones page and the audio monitor side by side. In the monitor choose a room and press **Listen** (browsers only play
sound after a click; the "listening level" changes only your browser, not the room).

**Pair a guest and play music.** Bridge -> Pairing -> "Allow pairing for 5 minutes". Phones -> Anna: "Search for devices" -> "Pair" ->
"Connect" -> Play. Within about two seconds Lyrion plays the bridge stream in the Wohnzimmer; listen in the monitor. Outside the pairing
window the bridge is invisible and pairing is refused; a paired phone reconnects later without a window.

**Multi-room.** Monitor -> "Synchronised group": tick Wohnzimmer and Schlafzimmer -> "Form group", then listen to the Schlafzimmer: it plays
the same music in sync. (The bridge never builds or changes groups itself; you can also pick an existing group as the bridge's target.)

**Radio taken over by Bluetooth.** Stop the phone. Monitor -> Wohnzimmer -> "Play demo radio in this room" (or start the favourite "Demo
Radio (simulated)" in Lyrion). Now press Play on the phone: after the notification filter (about 2 s) the bridge takes the room over.
Turn off Settings -> Playback -> "Phones may take over a playing Lyrion player" to see the bridge wait instead.

**Short notification sounds.** While the radio plays, connect the phone and press "Message arrives (short sound)": the radio keeps playing,
a 0.7 s chime does not take a room over. While the phone itself plays music, the chime is mixed into it - as on a real phone.

**Manual source change.** While the bridge plays, start something else on the Wohnzimmer in Lyrion. The bridge notices that another source
took the player and steps back; it does not fight for the room (dashboard: "Taken over by another source").

**Volume.** Move the phone's volume slider: the bridge forwards the change to the room's Lyrion volume (absolute AVRCP volume, applied
relatively). Lyrion's own volume control and the monitor's listening level are independent.

**Track change and titles.** Use previous/next on the phone. To see titles in Lyrion, enable Settings -> Playback -> "Send track titles in
the stream (ICY)".

**Connection loss.** "Simulate connection loss": the phone vanishes like out of range; the room stops at once (disconnect = immediate stop).

**A second phone.** While Anna plays, pair and connect Ben: the bridge refuses the second phone and never interrupts the first.
Bridge -> Pairing lists both phones; "Remove" there makes Ben pair again inside a pairing window.

**Your own music.** On a phone, "Add own file" (WAV, FLAC, MP3, Ogg Vorbis, up to 80 MB). It is decoded inside the demo and never leaves it.

**MQTT and Home Assistant.** Bridge -> Settings -> MQTT: host `mosquitto`, port 1883, user and password from `make demo-status`. Then
`make demo-ha`: Home Assistant starts (first start takes a few minutes), is onboarded with a generated owner password (shown by
`make demo-status`) and gets the MQTT integration. The bridge appears as a device; the optional controls (pairing window, playback, test tone)
can be enabled one by one under Settings -> MQTT.

**Diagnostics, test tone, system monitor, languages, themes.** All in the bridge WebUI; the demo console also has German/English and
light/dark. The system page shows real CPU/memory of the bridge container; there is no temperature sensor in a container.

**Recovery.** Forgot the owner password? `make demo-recover` does what an owner does with the SD card (P8): it places the request
`reset-owner-password` on the boot partition and restarts the device; the device processes it exactly once and shows a new one-time setup
code (also in `make demo-status`). Settings and pairings are kept.

## 5. Measured values (SIMULATED phones and rooms - software timing in Docker, not acoustic, not Bluetooth)
From `scripts/test_demo.sh` (`measurements/demo_*.json`, 2026-11-17):

| What | Value |
|---|---|
| Play on the phone -> Lyrion plays the bridge stream | 2.1 s |
| Phone -> room DAC while playing (Lyrion + Squeezelite buffering) | 2.2 s (Wohnzimmer and grouped Schlafzimmer) |
| Takeover of the demo radio after Play | 2.3 s (notification filter ~2 s) |
| Link loss -> room stops | 0.02 s |
| Audio monitor, **added** latency (player DAC -> browser output) | ~260-295 ms: ~225 ms jitter buffer + ~50 ms reported browser output (headless Chromium; your browser and sound card add their own) |

The monitor's latency is measured live from time stamps (player DAC time of the sample being played) and shown on the monitor page. It is
not part of the bridge's latency.

## 6. Troubleshooting
* **"Port(s) already in use"**: start with other ports, e.g. `DEMO_HTTP_PORT=28080 DEMO_HTTPS_PORT=28443 DEMO_CONSOLE_PORT=28100
  DEMO_LMS_PORT=29000 DEMO_HA_PORT=28123 make demo`.
* **"Pool overlaps with other one"**: set `DEMO_SUBNET_DEMO` / `DEMO_SUBNET_EDGE` to free /24 networks.
* **Silent monitor**: is the room playing (monitor shows "Lyrion: playing")? Raise the listening level; Lyrion starts players at volume 50.
* **Browser warning on HTTPS**: import the CA (section 3) and use exactly https://127.0.0.1:18443.
* **Start over**: `make demo-reset`.

## 7. How it is built (for developers)
`demo/compose.yml`, image `demo/Dockerfile` (one image), code `demo/hifidemo/`:
`node.py` (the "Pi": private D-Bus, PipeWire, `vbluez.py` = simulated bluetoothd, the unchanged `btbridge.appliance --source bluez`),
`phones.py`/`console.py` (virtual phones; they only act over the simulated radio), `player.py`/`monitor.py` (Squeezelite -> virtual DAC ->
monitor tap), `gateway.py` (the only published ports), `provision.py` (flash-tool core), `music.py` (generated audio), `hasetup.py`.
Tests: `scripts/run_demo_tests.sh` (unit, incl. the virtual bluetoothd against the product's real BlueZ adapter) and `scripts/test_demo.sh`
(full stack on a separate project: isolation check, P7 trust proof, setup in Chromium, audio chain proven by cross-correlation, takeover,
multiroom, console UI with axe-core, MQTT discovery). Decisions: DECISIONS #71.
