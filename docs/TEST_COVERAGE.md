# Test coverage of the product code (`bridge/btbridge`; the flash tool is measured separately)

Measured with coverage.py (Debian `python3-coverage`, line **and** branch) in the lab image: `scripts/run_coverage.sh --integration`
(unit suite + integration suite against the real LMS/Squeezelite/Mosquitto lab + `tests/os` and `tests/flashtool`). Report:
`reports/evidence/coverage.txt` (file table with missing lines, then a per-package summary from `scripts/lib/cov_summary.py`). The flash tool
(`flashtool/hififlash`, including its GUI) is measured by `scripts/run_flashtool.sh` -> `reports/evidence/flashtool_coverage.txt`: **100 %
line and branch** with its own suite; its GUI needs PySide6, which the images of this script do not have, so since package 7b-3 it is no longer
counted here (it used to add a misleading 0 % for ten GUI files).

| Run (2026-11-16) | Statements | Branches | Combined |
|---|---|---|---|
| Stabilisation package: unit only | 4737 | 1776 | 82 % |
| Stabilisation package: unit + integration | 4742 | 1784 | 90 % |
| **Phase-2b preparation, commit `0ae4d2a`:** unit + integration + `tests/os` + `tests/flashtool` (bridge **and** flash tool) | 5606 | 2116 | **91 %** |
| **Demo package (2026-11-17), commit `0e307d1`:** same scope | 5615 | 2120 | **91 %** |
| **Package 7b-3, before (2026-11-20, develop `0d3b8f2`, `bridge/btbridge` only, after phases 2b-3..6)** | 9540 | 3620 | 94.0 % (line 94.9 %, branch 91.8 %) |
| **Package 7b-3, after (same scope, same command)** | 9540 | 3620 | **97.5 %** (line **98.1 %**, branch **95.9 %**) |
| **Final polish P6 (2026-11-21, develop `a7ae13b`, same scope and command; incl. the P1/P3 code)** | 9788 | 3748 | **97.6 %** (line **98.2 %**, branch **96.0 %**) |

90 % was an intermediate measurement, not a target ceiling (DECISIONS #60). New code of the demo package: bootfiles 97 %, provisioning 92 %,
recovery 91 %, trustproof 95 %, flash tool modules 93–100 %.

### Package 7b-3 (2026-11-20): per package, before -> after (`bridge/btbridge`, unit + integration + OS suites)
Line = statements covered / statements. Branch = branch exits covered / branch exits. Combined = coverage.py's own number (TOTAL row).

| Package | Statements | Line % | Branch % | Combined % |
|---|---|---|---|---|
| top-level modules (service, app, appliance, recovery, provisioning, ...) | 1424 | 91.8 -> **97.3** | 89.8 -> **95.5** | 91.3 -> **96.8** |
| `adapters/` (BlueZ adapter, model, bus, PCM capture) | 617 | 92.9 -> **93.8** | 84.9 -> **85.9** | 90.3 -> **91.3** |
| `config/` | 294 | 93.9 -> **99.0** | 92.9 -> **100.0** | 93.6 -> **99.3** |
| `core/` (controller, ring, ...) | 661 | 94.7 -> **98.0** | 88.0 -> **93.4** | 92.6 -> **96.6** |
| `lms/` | 204 | 96.6 -> **99.0** | 90.7 -> **98.7** | 95.0 -> **98.9** |
| `mqtt/` | 333 | 92.2 -> **97.3** | 81.9 -> **93.1** | 89.5 -> **96.2** |
| `net/` (mDNS, Avahi, TLS, network agent, ...) | 2415 | 94.9 -> **98.8** | 93.9 -> **97.0** | 94.6 -> **98.3** |
| `ota/` | 1299 | 99.9 -> **99.9** | 99.4 -> **99.4** | 99.8 -> **99.8** |
| `stream/` | 323 | 92.9 -> **95.7** | 91.1 -> **95.0** | 92.5 -> **95.5** |
| `system/` | 177 | 96.6 -> **98.3** | 87.0 -> **91.3** | 93.9 -> **96.3** |
| `webui/` | 1793 | 95.3 -> **98.3** | 91.5 -> **97.4** | 94.3 -> **98.1** |
| **`bridge/btbridge`** | **9540** | **94.9 -> 98.1** | **91.8 -> 95.9** | **94.0 -> 97.5** |

The "before" numbers are the same command on develop before this package, recomputed for the same scope (the report then still counted the
flash tool: 12568 statements, 84.8 % line / 85.3 % combined, of which 1358 statements were the GUI at 0 %). 485 missed statements became 182.

What was added (real tests of behaviour that matters - no test exists only to run a line; there is no `# pragma: no cover` anywhere in the project
and none was added): `test_service_faults.py` (optional integrations that cannot start, a refused Bluetooth name, crash leftovers at Lyrion, a
failing discovery, listeners and the watch loop that never kill each other), `test_webui_pages.py` and `test_webui_robustness.py` (owner pages
in the situations that go wrong; duplicated/short/JSON bodies, garbled cookies, HEAD, a client that leaves), `test_access_faults.py` (a
regenerated CA, a torn certificate, a new DHCP address, a missing custom certificate, a vanished openssl), `test_mdns_faults.py` (an interface that
cannot join or send, a second device claiming our name after we are up, the lab CLIs under SIGTERM, Avahi going away), `test_mqtt_client_faults.py`
(a scripted broker: refused login, no CONNACK, QoS 1, a stalled broker, a dropped socket, an untrusted TLS certificate),
`test_core_faults.py` (full disk during a settings write, a settings file that is not JSON, validation codes, the audio ring overrun, the bounded
audit lists, a dead encoder, a systemd that does not listen), `test_controller_faults.py` (Lyrion vanishing mid-volume, owner actions that change
nothing, the test sound while playing, a stream that dies under us, a long outage), `test_recovery_faults.py` (corrupt copies of secrets removed by a
factory reset, journal after a power cut, the privilege drop of the console tool in a forked child, malformed provisioning documents),
`test_net_faults.py` (Avahi host-name answers, rfkill, unreadable kernel tables), `test_stream_faults.py`, `test_lms_client_faults.py`,
`test_auth_faults.py`, `test_bluez_faults.py`, `test_misc_faults.py`. Also: `tests/demo/test_node.py` (demo wiring, not counted here).
The 12-item list of the independent review (REVIEW_PHASE2B_NEW_CODE.md, "untested code paths worth real tests") was already covered by the
tests of packages 7b-1/7b-2; item 12 (`reconcile` after a failed install) is asserted in `test_reconcile_table`.
**A real defect was found by the flood test:** the login back-off computed `2 ** (failures - free)` without a bound; after about 1,030 failures
from one address (about 1,070 globally) the float conversion raised `OverflowError` before the bookkeeping was stored, so a patient attacker ended
with an answering login and no back-off. Fixed (`fix(webui)` commit), regression test `test_back_off_keeps_working_after_thousands_of_failures`.

Coverage counts only the Python process that runs pytest. Code executed in **other** containers (the Avahi publisher CLI in
`scripts/test_mdns.sh`, the appliance in the browser lab, Home Assistant tests, the PipeWire suites) is exercised but not counted. 100 % is
not the goal: a number does not replace the hardware tests (CLAUDE.md).

## Known gaps and why (what is still not covered after package 7b-3)
182 statements and 149 branch exits remain. None of them is a behaviour the owner or a guest can reach in a test rig without hardware or fault
injection into the kernel; each group is listed with the reason. (No `# pragma: no cover` is used - the lines stay visible in the report.)

| Group | Where (missing lines in `reports/evidence/coverage.txt`) | Why it stays uncovered |
|---|---|---|
| Entry points and CLI shells | `__main__.py` 42-59 (the phase-1 bridge-only loop), `appliance.py` 71/141, `bootfiles.py` 97, `net/mdns.py` 364, `net/avahi.py` 180, `net/netagent.py` 308, `provisioning.py` 282, `recovery.py` 241/246 (`if __name__` and the dispatch line) | thin wrappers; the lab CLIs of mDNS/Avahi are run under SIGTERM by `test_mdns_faults.py`, the appliance entry by `test_appliance_entry.py`, mDNS interoperability by `scripts/test_mdns.sh` |
| Protocol / interface bodies | `adapters/base.py`, `adapters/bluez/bus.py`, `adapters/bluez/pcm.py` 27-28, `factory.py` 17 (`->exit` of abstract methods) | no code; conformance of the BlueZ adapter and the simulator is tested |
| Real system D-Bus errors | `adapters/bluez/jeepney_bus.py` (reconnect, reader errors, agent error replies), `adapters/bluez/adapter.py` 46-155 (guards), `net/avahi.py` 63/67 (a real `GetVersionString`) | **hardware-dependent**: against the fake BlueZ only; real BlueZ is plan A1-A7/B1-B7; Avahi against a real daemon in `scripts/test_mdns.sh` |
| `parec` / `pw-cat` process failures | `adapters/bluez/pcm.py` 67-68, 101-111, 139-140, 153, 161, 168 | real PipeWire 0.3.65/1.4.2 in `scripts/run_pipewire.sh` (separate containers, not counted); real phone nodes are plan A3/A4 |
| Interleaving guards | `core/controller.py` 168, 262, 326, 348, 358, 370-373, 388, 407, 436-440, 454, 464, 482, 496, 512, 583-593, 617; `service.py` 185; `app.py` 80, 100-112, 175-176 | "the state changed while this call was in flight" returns; deterministic tests would need a hook between two lock acquisitions. The invariants they protect are tested end to end (`test_controller_faults.py`, `test_review_controller.py`) |
| Failures of OS calls that need fault injection into the kernel | `fsync`/`close` errors: `net/certs.py` 54-55 and 296-297, `provisioning.py` 171-176, `recovery.py` 96-97, `net/netconfig.py` 264-274, `config/store.py` 47-48, `supervision.py` 115-116, `mqtt/client.py` 132-133, `stream/encoders.py` 59-87, `stream/server.py` 53-70, `net/netagent.py` 250-253, `net/netclient.py` 63/118-119 | each is a `try: ... except OSError: pass` around a best-effort durability or clean-up step; the surrounding behaviour (the previous file intact, no temporary file left) is tested where the failure can be provoked (`replace` failing, a missing directory) |
| Standard-library cases that did not occur | `webui/server.py` 65-66 (`CookieError` - the stdlib parser accepted every garbled header tried), 155-163 (accept loop), 216-217, 283-284, 325 (no `POST /api/*` route exists), 351; `webui/views.py` 43, 137, 686-687, 719, 738, 753-754, 871, 944, 949; `webui/views_network.py` 63, 181, 218, 245-246, 387; `net/mdns.py` 130-132, 215, 272-284, 302, 326-337; `lms/discovery.py` 35-36 (an unreadable `fib_trie`) | defensive branches behind validated input; reviewed, not provoked |
| `system/syslog.py` | 40, 68-99 | flush-on-stop and `close_notify` timing against a live TLS peer: covered by the TLS tests of `test_syslog.py` and `scripts/test_syslog.sh` (separate container) |
| Trivial | `core/clock.py` 12/25 (`time.time()` and the fake clock's accessor), `core/volume.py` 33 | one-line accessors |

Code executed in **other** containers or processes is exercised but not counted: the Avahi publisher CLI in `scripts/test_mdns.sh`, the
appliance in the browser lab, Home Assistant tests, the PipeWire suites, and the **privilege drop** `recovery._drop_to` (run in a forked child by
`test_recovery_faults.py`, which coverage does not follow). 100 % is not the goal: a number does not replace the hardware tests (CLAUDE.md).

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
