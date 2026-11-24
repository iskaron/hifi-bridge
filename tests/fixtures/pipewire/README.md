# PipeWire graph fixtures (`pw-dump` format, reduced to the fields the bridge reads)

| File | What it reproduces |
|---|---|
| `g1_playback_role.json` | **Real-hardware G1 finding (Pi 3 B+, Pi OS Lite trixie, PipeWire/WirePlumber 1.4.2, 2026-11-24):** the phone's decoded A2DP audio is a *playback stream* `bluez_input.<MAC>.2` with `media.class = Stream/Output/Audio`, linked by WirePlumber to the Pi's headphone sink; there is no recordable Bluetooth source (WirePlumber default `bluez5.media-source-role = playback`). |
| `g1_input_role.json` | The same phone with the bridge's WirePlumber rule (`bluez5.media-source-role = input`): an `Audio/Source` node, no links. Adds a second phone, a hands-free source of the same phone and an internal loopback node, none of which may be captured. |

Hand-written from the operator's `wpctl status` / `pactl list short` output and the PipeWire 1.4.2 / WirePlumber 0.5.8 sources
(`spa/plugins/bluez5/bluez5-device.c` `emit_node()` properties, `media-source.c` media class, `monitors/bluez.lua` node names).
MAC addresses are pseudonyms. Label: SIMULATED topology of a real observation - not a dump taken on the Pi.
