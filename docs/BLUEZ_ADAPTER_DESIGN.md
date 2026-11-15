# Real BlueZ/PipeWire adapter - design and hardware-free preparation

Status (updated 2026-11-15, phase-1 review work package): the adapter is **implemented and tested without hardware, but NOT verified against a real
`bluetoothd`, real phones, PipeWire BlueZ nodes or a Raspberry Pi** - it must not be described as a working hardware adapter.
Evidence labels: translator/agent policy/PCM supervision **UNIT** (scripted events, hand-written from the BlueZ docs); D-Bus client + adapter against a
**fake** `org.bluez` on a private D-Bus daemon **INTEGRATION / SIMULATED-BLUEZ**; the same adapter driving the bridge into real LMS + Squeezelite
**INTEGRATION (simulated BlueZ + simulated PCM source)**; everything about real BlueZ/PipeWire behaviour **UNTESTED** (docs/HARDWARE_VALIDATION_PLAN.md).

## Code map (`bridge/btbridge/adapters/bluez/`)
| Module | Role | Hardware-free evidence |
|---|---|---|
| `bus.py` | `BlueZBus` interface + event types (`PropertiesChanged`, `InterfacesAdded/Removed`, `ServiceAppeared/Vanished`), BlueZ names/UUIDs | - |
| `model.py` | pure translator BlueZ object model -> `AudioSourceAdapter` callbacks: one active source, second refused via `Device1.Disconnect`, non-phone UUIDs ignored, replay of early transports/players, volume clamp 0..127, status mapping, metadata dedupe, bluetoothd vanish | UNIT, 5/5 targeted mutants killed |
| `adapter.py` | `BluezAdapter`: pairing window -> `Adapter1.Pairable/Discoverable` (bridge owns the 5 min, BlueZ timeouts 0), agent (`NoInputNoOutput`, authorises only inside the window or for paired devices, no PIN/passkey), PCM capture lifecycle, bluetoothd restart recovery | UNIT + INTEGRATION (fake BlueZ) |
| `jeepney_bus.py` | `BlueZBus` on the pure-Python **jeepney** library (optional dependency, provisional choice): variant unwrapping, signal match rules, `GetManagedObjects`, property `Set`, agent method calls, `NameOwnerChanged` | INTEGRATION (fake BlueZ on private dbus-daemon) |
| `pcm.py` | `PipeWireCapture`: supervised capture subprocess (retry while the node is missing, 20 ms chunks, clean stop); `pw-cat` and `parec` command lines | UNIT with real child processes; **command lines UNVERIFIED** |
| `factory.py` + `python -m btbridge --source bluez` | builds the real adapter on the system bus; exits with code 2 and a clear message when D-Bus/jeepney are unavailable | UNIT (negative path only) |

Test doubles: `testkit/btkit/fake_bluez.py` (minimal `org.bluez` service, **not** bluetoothd) and `tests/unit/fakes_bluez.py` (scripted bus).

## Findings of the preparation work
* `pw-cat --help` (PipeWire 0.3.65, Debian bookworm) has **no `--raw` flag**; my first draft used it and was wrong. Whether `pw-cat --record ... -` writes
  headerless PCM is unverified (libsndfile may be involved) -> `parec` alternative prepared; validation items A3/A4.
* A headless PipeWire + WirePlumber daemon *starts* in the lab container, but creating and linking a null sink for an automated capture test did not
  work within the time box (no ports appeared / no links). **No PipeWire capture test exists**; do not read the supervision tests as PipeWire evidence.
* jeepney's *local* message filter compares the unique sender name, so a rule with the well-known sender `org.bluez` never matched. Bus-side `AddMatch`
  keeps the sender; the local filter must not (found by the fake-BlueZ test, fixed).
* BlueZ calls the agent on the connection that registered it, so one connection must serve signals, calls and agent methods (jeepney threading router).

## Original design mapping (BlueZ documentation; **[doc]** / **[?]** as before)

It maps the hardware-independent `AudioSourceAdapter` interface (`bridge/btbridge/adapters/base.py`) to
BlueZ D-Bus objects so a real adapter can be written and validated later (*deferred hardware validation*).
Facts marked **[doc]** come from BlueZ documentation found by web search on 2026-11-15 (version-dependent,
not checked against the BlueZ release a Buildroot image would ship); **[?]** = unverified, must be tested.

| Interface callback | BlueZ / PipeWire source | Notes |
|---|---|---|
| `set_pairable(True/False)` | `org.bluez.Adapter1` `Pairable` (rw, default true), `PairableTimeout` (0 = forever), `Discoverable`, `DiscoverableTimeout` (default 180 s) **[doc]** | Pairing window is owned by `core/pairing.py` (5 min); the adapter only mirrors it: Discoverable+Pairable on at open, off at close. Don't rely on `DiscoverableTimeout` alone (the bridge must also close it explicitly). Needs a BlueZ pairing *agent* (NoInputNoOutput / Just-Works) **[?]** |
| `on_connect(dev)` -> bool | `org.bluez.Device1` `Connected` property change (PropertiesChanged) | Single-source policy: if another device is connected, call `Device1.Disconnect()` on the newcomer; never evict the first. |
| `on_disconnect(dev)` | `Device1.Connected = false` | Triggers immediate release (no grace). |
| `on_transport(state)` | `org.bluez.MediaTransport1` `State`: `idle` (not streaming), `pending` (streaming, not acquired), `active` (streaming and acquired) **[doc]** | With PipeWire, BlueZ transports are acquired by PipeWire; `active` should correspond to audio flowing **[?]**. |
| `on_avrcp(status)` | `org.bluez.MediaPlayer1` `Status`: `playing`, `stopped`, `paused`, `forward-seek`, `reverse-seek`, (`error`) **[doc]**; interface marked *Experimental* | Phones that expose no AVRCP player -> `None` (core falls back to transport+PCM activity, see `core/fusion.py`). Real phone behaviour (notification sounds vs. `Status`) is the key open question #2. BlueZ may need `--experimental` **[?]**. |
| `on_metadata` | `MediaPlayer1` `Track` dict (Title, Artist, Album, ...) | Forwarded as ICY `StreamTitle` (lab-verified on the LMS side only). |
| `on_volume(v)` | `MediaTransport1` `Volume` (uint16, rw, optional) **[doc]**; AVRCP absolute volume scale is 0-127 (a community project clamps to 127; BlueZ scaling not confirmed) **[?]** | Core policy (`core/volume.py`) treats first value as a reference and only applies bounded relative deltas. |
| `on_pcm(bytes)` | PipeWire/WirePlumber bluez5 monitor creates a node per connected phone; documented naming includes `bluez_input.<MAC>...` for source-role devices and `bluez_output.<MAC>.a2dp-sink` for sinks **[doc, partially]** | Capture options to evaluate on hardware: `pw-cat --record` / a native PipeWire stream targeting the node, converted to 44.1 kHz s16le stereo. Whether the node of a *phone acting as A2DP source* is `bluez_input.*` and how to capture it without a monitor port: **[?]** - the search results did not confirm it. Alternative: BlueZ's own `MediaEndpoint1` + SBC decoding in-process (more code, fewer moving parts). |

## What the core already assumes (and tests simulate)
(Original design text, unchanged; still the assumptions that hardware validation must confirm.)
* PCM arrives as 44.1 kHz / 16-bit / stereo frames in roughly real time with jitter (sim: 20 ms chunks). If the phone
  or PipeWire delivers 48 kHz, resample in the adapter (the stream format is fixed to 44.1 kHz by the lab results).
* `on_pcm` may keep firing with *digital silence* while the phone is paused (some stacks keep the stream open);
  fusion treats the AVRCP status as authoritative when available.
* Clock drift between phone/BT clock and the bridge clock is handled by the feeder (drop/insert, measured in lab
  with *simulated* offsets only).

## Hardware validation checklist (deferred)
1. Pair + connect Android and iPhone via the pairing window; verify second phone is refused, first not evicted.
2. Record `MediaPlayer1.Status` + `MediaTransport1.State` traces for: play, pause, track change, notification sound
   (messenger/ringtone), navigation prompts, silent video; decide `fusion` rules from data.
3. Absolute volume: does the phone send `Volume` on connect / on slider change; scale; behaviour without support.
4. Codec negotiated (SBC baseline; AAC/aptX only if the platform supports it), sample rate, PCM continuity.
5. Pi Zero 2 W: Wi-Fi + BT coexistence under the measured 1.4 Mbit/s stream; CPU/RAM.
