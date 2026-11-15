# HANDOFF_FOR_CHATGPT — Phase 1 `audio-spike`

## 1. Status and requested review
- **Phase:** 1 (audio feasibility). **Date:** 2026-11-15. **Status: COMPLETE as a hardware-free software-feasibility spike** (software integration verified; all radio/phone/hardware proof is *deferred hardware validation*, not failed).
- **Summary:** A simulated A2DP/AVRCP phone drives the real bridge pipeline into a **real Lyrion 9.1.2** and **real Squeezelite 1.9.9** players (single player, 3-player sync group, PCM+MP3-capability emulation); audio arrives **bit-exact**, ownership rules hold against a real LMS, and no LMS plugin is needed.
- **Decisions requested from the owner/ChatGPT** (details in `docs/OPEN_QUESTIONS.md`):
  1. Approve Phase 1 and the gate to hardware validation (BlueZ adapter, phones, Pi) / Phase 2?
  2. Pause default = `drop_restart` (silence in 0.07 s) vs `keepalive_silence` (1.6 s, no LMS traffic during pauses)?
  3. Accept ~1.6 s Play→sound / Resume→sound (LMS 255 KB start threshold) or later authorise an LMS-plugin investigation?
  4. Leave a woken standby player powered on after release (current behaviour)?
  5. ICY metadata default off (legacy hardware unverified) — OK?

## 2. Guest experience outcome
- **Real guest path (phone pairs, connects, plays): NOT TESTED — no hardware, by owner decision.**
- **Simulated path, INTEGRATION (real LMS + Squeezelite):** simulated phone connects (LMS untouched) → Play → LMS plays the bridge stream → audio at the player ~1.55 s later (WAV) / ~1.9 s (FLAC); Pause silences in ~0.07 s; resume ~1.55 s; disconnect releases immediately; notification beeps never interrupt other LMS music; sustained media overrides after 2 s and never restores the old track.
- Limits: all timings are software (virtual-DAC) times, not acoustic.

## 3. Implementation / architecture
- **Modules** (`bridge/btbridge`, stdlib only, ~1.4k lines): `core/controller.py` (state dimensions, ownership, filter, grace, outage recovery, diagnostic), `core/{ring,fusion,volume,pairing,clock}.py`, `stream/{server,feeder,encoders}.py` (token+session URL, Range/410, optional ICY, WAV/FLAC), `lms/{client,discovery}.py`, `adapters/{base,bluez(stub)}.py`, `app.py`, `__main__.py`.
- **Lab/test code** (`testkit/btkit`, numpy; ~2.1k lines incl. tests): simulated A2DP source, index-addressable test signal, paced "virtual DAC" PCM tap, analyzers, outage scenario, demo. `docker/` lab (internal network, LMS + player1..3 + legacy1).
- **Key design decisions:** (a) the bridge only ever issues `playlist play <our url>`, `playlist deleteitem <our url>` (URL-addressed: verified no-op against a foreign source), `power 1` on takeover and relative `mixer volume` while owning — no generic stop/pause/clear; (b) per-session random id in the URL, stale sessions answer 410; (c) real-time silence-fill is mandatory (stalls cost ~5.5 s per LMS/Squeezelite re-buffer); (d) `drop_restart` pause strategy; (e) WAV PCM baseline, FLAC optional; (f) real BlueZ adapter only designed (`docs/BLUEZ_ADAPTER_DESIGN.md`, STATIC-ONLY).
- **Not done / deferred:** real BlueZ/PipeWire adapter, Buildroot, WebUI, MQTT, OTA (out of phase-1 scope); Docker deployment (not required).
- **Third-party:** Debian bookworm packages in lab image (`squeezelite` GPL, `flac`, `python3-numpy`, `pytest`, `tini`), `lmscommunity/lyrionmusicserver:stable` (LMS 9.1.2, GPL-2.0). Bridge itself has no third-party Python deps (+ runtime `flac` binary for FLAC option only).
- **Git:** `/home/olm/bluetooth-audio-bridge` is **not a git repository**; no git operations were run. No `add/commit/push/tag`. Host untouched except files in this directory; Docker-only builds/tests; no hardware probing.
- **Files created/changed:** see `README.md` layout; docs added: `docs/FINDINGS.md`, `docs/BLUEZ_ADAPTER_DESIGN.md`, appended `docs/OPEN_QUESTIONS.md`; `README.md` rewritten; `reports/evidence/full_run.log`; `measurements/*.json`.

## 4. Tests and environment
Environment: Docker 29.1.3 / Compose 2.40.3, Ubuntu 26.04 x86-64 host (4 CPU); lab image Debian 12, Python 3.11.2; **LMS 9.1.2**; **Squeezelite v1.9.9-1414**; isolated `internal` network.

Clean-slate full run (`scripts/run_all.sh`, log `reports/evidence/full_run.log`, started 2026-11-15T14:29Z):
```
UNIT          55 passed in 2.16s                       [UNIT/SIMULATED]
INTEGRATION   36 passed in 886.70s (0:14:46)           [INTEGRATION: real LMS + Squeezelite, SIMULATED Bluetooth]
OUTAGE lms    scenario exit code: 0                    [INTEGRATION, container stop/start]
OUTAGE player scenario exit code: 0
exit codes: unit=0 integration=0 outage_lms=0 outage_player=0
```
Reproduce: `scripts/lab_up.sh && scripts/run_unit.sh && scripts/run_integration.sh && scripts/test_outage.sh lms && scripts/test_outage.sh player1`.

Coverage by acceptance item (label):
| Item | Result |
|---|---|
| Synthetic live stream → real LMS → Squeezelite | PASS, INTEGRATION; bit-exact WAV 85/86, FLAC 80/81 blocks (1 = stop tail) |
| Android + iPhone pairing/streaming | **UNTESTED** (deferred hardware) |
| Play/Pause/Resume/Disconnect | PASS INTEGRATION (simulated phone); speaker timing **NOT MEASURED** |
| Ownership races (radio switch then late BT events; 2 bridges; stale session) | PASS UNIT + INTEGRATION; zero LMS commands after ownership loss |
| One-source policy, notification filter, idle target, override off, audio toggle, target change | PASS UNIT + INTEGRATION |
| LMS / player outage | PASS INTEGRATION (real container stop/start) |
| Sync group | PASS INTEGRATION (3 players; target master and slave) |
| Legacy Squeezebox | capability *emulation* only (pcm+mp3) PASS; **real hardware UNTESTED** |
| Pairing window | UNIT (expiry/notify) + wiring INTEGRATION; real pairing UNTESTED |
| Volume / metadata | policy UNIT; LMS-side effect INTEGRATION; real phone AVRCP **UNTESTED** |
| Diagnostic test refuses busy target | PASS UNIT + INTEGRATION |
| Token never in logs/status | PASS (test), rotation tested |
| Test-suite quality | mutation check: 8/8 deliberate ownership-invariant mutants killed by unit tests |

## 5. Timing and quality measurements (virtual-DAC, x86 Docker; **NOT acoustic, NOT hardware**; raw JSON in `measurements/`)
| Metric | Result |
|---|---|
| Play → first player PCM (WAV), idle target | median 1.55 s (n=5; probes: BT PCM +0.010 s, LMS cmd +0.002…0.011 s, player connects +0.09 s) |
| same, FLAC | median 1.89 s (n=5) |
| Takeover of a playing radio after Play | LMS command at 2.03 s (the 2 s filter) |
| Pause → last audible output | `drop_restart` 0.06–0.08 s; `keepalive_silence` 1.6 s (3 runs each) |
| Resume → first output | 1.54–1.59 s both |
| Grace release (keepalive) | 30.1 s after Pause |
| Stall of 4 s at source | no fill: 1 underrun, audio back 5.1–5.3 s; silence keepalive: 0 underruns, 1.3–1.4 s |
| Delivery hiccup tolerance | 0.9 s, 1.5 s: 0 underruns; 2.5 s: 1 underrun |
| Sync group offset | 4–20 ms between runs (stable within a run) + Squeezelite corrective skips 441–837 frames early |
| Clock drift ±3000 ppm (exaggerated) | slow: 7×~40 ms silence/100 s; fast: ~470 tiny drops; 0 underruns. ±300 ppm: no correction needed |
| CPU / RAM (dev host, relative only) | bridge ≈2.4–2.7 % of one core; `flac` 0.4 %; RSS flat; ring 1.06 MB fixed |
| LMS outage 20 s | BT stayed connected; player kept playing live stream (99.99 % audible); LMS re-attached |
| Player outage 15 s | silent during, audio back 0.5 s after return, first frame ~1.5 s old (not stale) |
Acoustic latency, real speaker timing, Wi-Fi/BT coexistence, Pi CPU: **NOT MEASURED — hardware unavailable.** Method caveats (unclocked Squeezelite stdout → paced virtual DAC, shared host clock) in `docs/FINDINGS.md`.

## 6. Risks, defects, unverified claims
- **High (unverified):** real-phone AVRCP/transport behaviour (notification vs media), absolute volume, codec/PCM delivery from BlueZ/PipeWire — the whole BT front end is simulated.
- **Medium:** startup/resume ≈1.6 s floor from LMS's 255 KB threshold (`icy-br` hint and pre-roll tried — no effect, removed; evidence `measurements/NEGATIVE_startup_policy_icybr_preroll.json`). Only a plugin/handler or LMS setting beyond standard could change it.
- **Medium:** real legacy Squeezebox and other firmware/players (only Squeezelite 1.9.9 exercised); sync correction behaviour on hardware.
- **Low/known:** slow-clock phones → rare ~40 ms silence gaps; residual race window observe→`play` during an intentional takeover (LMS has no CAS); a user pressing Stop in LMS is treated as manual change (not resurrected); woken player left powered on.
- **Bugs found during development, all fixed with regression tests:** feeder drop logic not converging; volume policy replaying a capped jump (small phone volume-down raised LMS volume); diagnostic tone cut short by player lead; unclocked stdout invalidating first timings. A flaky-looking first outage metric was a measurement-design error, corrected (documented in findings).
- Honesty notes: integration "radio" is a second synthetic stream played via real LMS, not a real station; tests share one process for bridge + simulator.

## 7. Next best actions (no phase 2 started)
1. Owner decisions in §1. 2. Hardware validation sprint: implement `BluezAdapter` per `docs/BLUEZ_ADAPTER_DESIGN.md`; record AVRCP/transport traces from Android + iPhone; tune `fusion`; measure with a real capture rig; test Pi 3B+/Zero 2 W. 3. Test a real Squeezebox/other players and an owner's real LMS (read-only first). 4. Optional improvements: resampling drift compensation; CI wiring of `run_unit.sh` + `run_integration -m "not slow"`. 5. Then Phase 2 only on explicit approval.

## 8. Operator handoff (copy/paste for ChatGPT)
> Phase 1 (hardware-free `audio-spike`) of Bluetooth Audio Bridge is complete as a software-integration result, run entirely in Docker: a *simulated* A2DP/AVRCP phone → bridge (Python, stdlib) → real Lyrion 9.1.2 → real Squeezelite 1.9.9 (single, 3-player sync group, PCM+MP3-capability emulation). Clean-slate run: 55 unit + 36 integration tests + 2 outage scenarios all pass; audio is bit-exact end to end; no LMS plugin needed. The bridge only issues `playlist play <own url>` and URL-addressed `deleteitem` (safe against foreign sources), and ownership/notification/grace/disconnect/two-bridge/outage rules were verified against a real LMS; 8/8 mutation checks caught. Software timings (virtual DAC, not acoustic): Play→sound ≈1.55 s (WAV)/1.9 s (FLAC), Pause→silence 0.07 s with `drop_restart` (1.6 s with keepalive), resume ≈1.55 s; startup is bounded by LMS's 255 KB start threshold (tuning attempts failed, documented). NOT verified: real Bluetooth, Android/iPhone AVRCP/volume, real Squeezebox, speaker timing, Pi/Zero 2 W resources — all deferred hardware validation. Please review `reports/HANDOFF_FOR_CHATGPT.md`, `docs/FINDINGS.md`, `docs/OPEN_QUESTIONS.md` and decide: approve phase 1/gate, pause strategy default, acceptance of ~1.6 s startup, player power-off policy, ICY default. Repo is not under git; nothing was committed or published.
