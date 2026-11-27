# Product specification — accepted behavior

## Core goal

The Bluetooth Audio Bridge is a standalone, open-source Raspberry Pi appliance which receives standard smartphone Bluetooth audio (A2DP sink) and makes it available as a live stream to Lyrion Media Server (LMS). LMS plays it on one configured existing player or an existing synchronized multiroom group. A guest **does not** install anything or control LMS: they connect as to a Bluetooth speaker and use their normal audio app (e.g. Amazon Music, Spotify, YouTube audio). Video lip-sync and ultra-low latency are not target requirements.

**Do not set an arbitrary time limit for Play-to-sound.** Measure first. A few seconds of startup can be acceptable. Pause should feel responsive; distinguish immediate phone pause from LMS draining a playback buffer. Optimize only where actual measurements justify the engineering cost.

## Bluetooth / normal experience

- Owner can rapidly open an existing mobile-friendly WebUI shortcut, authenticate with a password stored by the browser if desired, enable **pairing** and tell a visitor which configurable Bluetooth device name to select.
- Pairing mode is explicit and time-limited; accepted default: **5 minutes**. Paired devices persist, can be listed and removed one-by-one, and reconnect normally without opening the UI again.
- Only **one actively connected Bluetooth source** at a time; another source does not silently evict it. Retain this already-agreed policy as a secondary concern to first-class guest UX.
- Separate controls: **allow audio playback** and **enable pairing**. Disabling bridge audio stops only bridge-owned playback, not another Lyrion source. A Bluetooth connection may remain while bridge playback is disabled.
- Do not start LMS audio merely because a phone connected; require real audio playback. Use available A2DP state plus AVRCP metadata/playback status when reliable, with documented fallbacks.
- Ignore very short notification sounds **when another Lyrion source would be interrupted**: provisional ~2 seconds continuous media activity, with initial-audio buffering if technically feasible. When destination is idle, begin playback promptly instead of always waiting 2 s. Evaluate real-phone status behavior; don't treat quiet music passages as pauses.
- Playback pause: **30 s logical grace** before ending bridge playback, the accepted default is the **controlled restart (`drop_restart`)**: our LMS item is removed immediately for a quick silence, ownership is kept logically for the grace period, and a resume within grace starts a fresh session from live audio (keepalive silence remains an option; see DECISIONS #15). Disconnect: stop bridge-owned playback immediately. On a manual LMS source change, do not resurrect an old stream or stop the newly chosen source later. A fresh user Play action can re-take over if override is enabled.
- By default allow Bluetooth playback to override currently playing LMS audio when a genuine new media session begins. This override can be configured through Buildroot `menuconfig` (default on) and surfaced appropriately if useful. When the bridge overrides LMS, do **not** automatically resume the old track afterwards.
- AVRCP metadata and reasonable phone volume behavior where supported; a compatible fallback if absolute volume is unavailable. Never allow unpredictable loud jumps; preserve relative room volume where feasible.
- Codec: standard SBC input is baseline; optional better codecs only on verified platform support; no claims of end-to-end losslessness from Bluetooth.

## Lyrion / playback target

- Discover Lyrion servers, or configure manually by hostname/IP/URL and port. Save one explicitly chosen server; don't silently switch server.
- Target is one persistently configured Lyrion player **or an existing sync group treated as a single target**. Never create/rearrange sync groups or choose an arbitrary alternative target if unreachable.
- Start audio at the selected target only when appropriate; waking a standby player via LMS control is allowed where supported/tested; **a player the bridge woke is left powered on afterwards - the bridge never sends power-off** (DECISIONS #17). Changing configured target while Bluetooth remains connected can briefly interrupt sound: cease old bridge-owned stream, start on new target without touching unrelated playback.
- Respect source ownership. A late Bluetooth disconnect, pause timer, target switch, or audio toggle **must never stop unrelated radio or another bridge's stream**. Async status/events may help; guard against races using revalidation, session identity and current player state.
- Bluetooth stays connected through LMS/network outages. Retry without replaying stale buffered minutes; once LMS returns, resume from live incoming audio when phone still plays. Show explicit degraded status in UI.
- Support modern Squeezelite and older Squeezebox devices when **verified**, using LMS transcoding or a tested compatible stream format. Prefer high-quality transfer without unnecessary lossy re-encoding. Automatic format selection; no mandatory technical codec selector for an ordinary user. Baseline stream = WAV/PCM, FLAC optional. In-band ICY metadata is **off by default** until older Squeezebox hardware is tested (DECISIONS #18). **Experimental, owner-selected (DECISIONS #147):** a bridge-encoded live MP3 320 kbit/s CBR stream (WebUI "MP3 320 kbit/s (experimental)") as a bitrate A/B for sync groups, with its own opt-in ICY switch for song titles; WAV remains the default, nothing is claimed about its benefit or about legacy-display behaviour until hardware results exist (docs/MP3_AND_METADATA.md).
- Dedicated test audio action in WebUI for owner diagnostics, no phone needed. **Refuse** test if target already has active music; never interrupt another listener's radio to test audio.

## Local streaming / security

- Local access (DECISIONS #35, #48-#50): stable mDNS name via **Avahi** (own responder only as fallback where no Avahi daemon exists), HTTPS
  with a per-device local CA or an own certificate (OpenSSL), **no HSTS by default**, no silent TLS exceptions; details in docs/LOCAL_ACCESS_TLS.md.
- Local network only by default. Expose a hard-to-guess per-device stream URL with a cryptographically random access token, rotatable by owner. Some players may fetch directly; account for that routing/compatibility.
- Token is redacted from bridge logs, exports and diagnostics. Because URL may flow through LMS and remote players, don't promise complete secrecy in third-party logs. Be explicit about risks of HTTP and offer HTTPS for WebUI/admin where practical; don't break old HTTP-only players.
- No universal credentials. Factory setup must provide individually generated password/setup code (flash tool / supported manual bootstrap), clear onboarding, LAN/Wi-Fi/Hotspot tradeoffs, secure credential storage and no cloud dependency.

## Appliance, deployment and networking

- Buildroot-based minimal read-only root filesystem, writable persistent data, bootable flashable image; no normal apt/desktop, no routine SSH administration. Pi Zero 2 W, Pi 3 B+, Pi 4 target hardware; Pi 3 B+ sensible primary prototype, Zero 2 W explicitly requires verified Bluetooth + shared 2.4 GHz Wi-Fi performance.
- Client Wi-Fi OR device setup hotspot, **not both simultaneously**. On configured Wi-Fi failure after provisional **60 s**, offer recoverable setup fallback; avoid captive network confusion. Allow Ethernet when available. Configurable SSID/device identifier, secure per-device hotspot credentials; never a shared factory password.
- International Wi-Fi regulatory domain configured via Buildroot `menuconfig` default/override, flash tool country selection, and WebUI settings. Do not infer location via GPS/cloud.
- mDNS where available, plus reliable access via known IP/fallback instructions. WebUI mobile-first, Deutsch/English, accessible from phone/tablet/desktop with light/dark themes and **Hi-Fi & elegant** visual identity across WebUI, flasher, logo/docs. Audio status is primary; CPU graphs secondary.
- System health: CPU load/temp, RAM, storage, uptime, signal/network. MQTT + Home Assistant autodiscovery optional; normal measurements every **5 min**, immediate important state changes and initial report after startup; internal temp polling ~30 s and immediate critical thermal alert.
- MQTT controls/status: connected source, playback state, pairing on/off, playback permission, metadata as available. Keep local audio fully functional if MQTT/Home Assistant is down.
- Aim for quick boot/readiness (<30 s desired, measure), watchdog/self recovery, meaningful diagnostics, logs and the fault history only in bounded RAM buffers (gone after a reboot; owner decision 2026-11-20, DECISIONS #121) and optionally secure remote syslog.
- Signed full-system A/B OTA, automatic rollback after failed boot, persistent settings/data, WebUI update upload plus reproducible `make ota`, optional update check **without automatic installation**. Known-good previous release should be selectable for recovery. Test power-failure paths per actual supported board. Recovery/flash utility for Windows/macOS/Linux.
- **Storage size is NOT pre-decided**. Build and measure actual A/B root images, data requirements, OTA/recovery margin. Compute minimum and recommendation, grow data partition on first boot; 4 GB is an *unverified optimization target*, 32 GB not accepted as default minimum.
- Intended future independent Docker runtime: maintain separation between core bridge logic and hardware services; not part of initial Raspberry Pi release.
- AGPL-3.0-only; central repository `https://github.com/iskaron/hifi-bridge`, private until the owner releases it (DECISIONS #70). CI on GitHub Actions; official releases and OTA bundles are distributed through GitHub Releases of this repository. No publication or release without owner go-ahead.

## Optional capabilities vs promises

Everything above is a product intention. Compatibility with legacy Squeezeboxes, live FLAC/PCM playback, exact absolute-volume behavior, synchronized multiroom latency, power recovery and Raspberry Pi Zero 2 W performance must be **measured**. Document risk and required adjustments, never claim guaranteed compatibility without evidence.
