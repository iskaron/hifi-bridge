# Test coverage of the product code (`bridge/btbridge`)

Measured with coverage.py (Debian `python3-coverage`, line **and** branch) in the lab image: `scripts/run_coverage.sh --integration`
(unit suite + integration suite against the real LMS/Squeezelite/Mosquitto lab). Report: `reports/evidence/coverage.txt`.

| Run (2026-11-16) | Statements | Branches | Combined |
|---|---|---|---|
| Unit only | 4737 | 1776 | 82 % |
| Unit + integration | 4742 | 1784 | **90 %** |

Coverage counts only the Python process that runs pytest. Code executed in **other** containers (the Avahi publisher CLI in
`scripts/test_mdns.sh`, the appliance in the browser lab, Home Assistant tests, the PipeWire suites) is exercised but not counted. 100 % is
not the goal: a number does not replace the hardware tests (CLAUDE.md).

## Known gaps and why
| Module | Gap | Reason / where it is tested instead |
|---|---|---|
| `__main__.py` | the long-running bridge-only CLI loop | thin wrapper; the phase-1 runner. Appliance entry point is tested (`test_appliance_entry.py`) |
| `net/avahi.py` | `main()` CLI, D-Bus error branches | CLI runs inside the `mdns-avahi-pub` container: 6/6 checks against a real Avahi (`scripts/test_mdns.sh`) |
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
