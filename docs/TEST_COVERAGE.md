# Test coverage of the product code (`bridge/btbridge`, `flashtool/hififlash`)

Measured with coverage.py (Debian `python3-coverage`, line **and** branch) in the lab image: `scripts/run_coverage.sh --integration`
(unit suite + integration suite against the real LMS/Squeezelite/Mosquitto lab). Report: `reports/evidence/coverage.txt`.

| Run (2026-11-16) | Statements | Branches | Combined |
|---|---|---|---|
| Stabilisation package: unit only | 4737 | 1776 | 82 % |
| Stabilisation package: unit + integration | 4742 | 1784 | 90 % |
| **Phase-2b preparation, commit `0ae4d2a`:** unit + integration + `tests/os` + `tests/flashtool` (bridge **and** flash tool) | 5606 | 2116 | **91 %** |

90 % was an intermediate measurement, not a target ceiling (DECISIONS #60). New code of this package: bootfiles 97 %, provisioning 92 %,
recovery 91 %, trustproof 95 %, flash tool modules 93–100 %.

Coverage counts only the Python process that runs pytest. Code executed in **other** containers (the Avahi publisher CLI in
`scripts/test_mdns.sh`, the appliance in the browser lab, Home Assistant tests, the PipeWire suites) is exercised but not counted. 100 % is
not the goal: a number does not replace the hardware tests (CLAUDE.md).

## Known gaps and why
| Module | Gap | Reason / where it is tested instead |
|---|---|---|
| `__main__.py` | the long-running bridge-only CLI loop | thin wrapper; the phase-1 runner. Appliance entry point is tested (`test_appliance_entry.py`) |
| `net/avahi.py` (84 %) | `main()` CLI | CLI runs inside the `mdns-avahi-pub` container: 6/6 checks against a real Avahi (`scripts/test_mdns.sh`); D-Bus error branches now unit-tested |
| `adapters/base.py` | bodies of abstract interface methods | interface only; conformance of BlueZ adapter and simulator is tested |
| `mqtt/client.py` | TLS/socket error branches, partial reads | real broker paths covered by integration + real Home Assistant (`scripts/run_homeassistant.sh`, separate container) |
| `adapters/bluez/jeepney_bus.py`, `bus.py` | reconnect/error branches of the real system bus | **hardware-dependent**: against the fake BlueZ only; real BlueZ is plan A1–A7/B1–B7 |
| `adapters/bluez/pcm.py` | process-level failure branches of `parec`/`pw-cat` | real PipeWire 0.3.65/1.4.2 in `scripts/run_pipewire.sh` (separate containers); real phone nodes are plan A3/A4 |
| `system/monitor.py` | `/sys` paths of a Pi (throttling, real thermal zone) | **hardware-dependent**; parsers tested with fake `/proc` trees |
| `service.py`, `webui/views.py` | rare error branches (e.g. LMS errors during settings changes) | partly covered by outage scenarios (`scripts/test_outage.sh`) |

## Code that only real hardware can exercise
BlueZ D-Bus objects of a real adapter, PipeWire BlueZ nodes (`bluez_input.*`), AVRCP from real phones, the Pi's thermal zone and
throttling flags, systemd watchdog kill/restart on an image. These are listed in `docs/HARDWARE_VALIDATION_PLAN.md`, not counted as
covered.

## Not measurable by coverage in this repository
* `os/br2-external/board/hifi-bridge/*.sh` (shell): exercised by `tests/os` (post-build with fake trees, post-image with genimage, growdata
  on image files) and shellcheck, but shell is not instrumented.
* `packaging/systemd/*.service`: `systemd-analyze verify/security` only.

## Interactive demo (`demo/hifidemo`, not product code)
The demo is a development tool and is **not** counted in the product coverage above. Its own tests:
* `scripts/run_demo_tests.sh` - 52 unit tests: music generation, the simulated bluetoothd **against the product's real BlueZ adapter** on a
  private D-Bus (pairing window, agent, refusal of a second phone, owner removal, link loss, persistence), phone model, decoding, audio
  monitor framing and pacing, gateway, Lyrion helpers, console API incl. rebinding/CSRF guards.
* `scripts/test_demo.sh` - the full stack on its own compose project: isolation check, P7 trust proof, owner setup in Chromium, 11 audio
  chain tests (the room's PCM behind Lyrion and Squeezelite matched to the phone's track by cross-correlation, score 1.0), takeover rules,
  multiroom, manual source change, console UI in two languages/themes/sizes with axe-core, both audio monitor engines, MQTT discovery,
  SD-card recovery.
