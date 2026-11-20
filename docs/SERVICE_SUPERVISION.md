# Service supervision and restart strategy (DECISIONS #39) – prepared for phase 2b

Status: the code (`btbridge/supervision.py`, `/healthz`) is implemented and tested. The unit file `packaging/systemd/hifi-bridge.service` has passed
**`systemd-analyze verify`**, with a security exposure of 2.1 ("OK"). It has **not** been run under systemd on a real image yet (phase 2b).

## Layers
| Layer | Mechanism | Detects | Action |
|---|---|---|---|
| Inside the process | reconnect loops: LMS polling and back-off, MQTT reconnect with back-off, BlueZ `ServiceAppeared` re-sync, PipeWire capture retry plus no-data watchdog, certificate renewal | dependency outages | recover in place; the phone stays connected |
| Health | `service.health()`: heartbeats of the control loop and the service watch loop; feeder and stream threads alive. A loop that waits inside one LMS call (bounded by the client's timeouts: 3 s per attempt, 2 s name lookup) counts as busy, not hung; a call longer than 60 s counts as hung | a dead or **hung** internal loop (for example a deadlock) | status `failed` |
| systemd watchdog | `Type=notify`, `WatchdogSec=30`. The process sends `WATCHDOG=1` every 15 s, **only while health is not `failed`** | hang / failed health | systemd kills the process (SIGABRT) and restarts it |
| Process exit | `Restart=always`, `RestartSec=2`, `StartLimitBurst=20` within 600 s | crash | restart; stops after a crash loop (fault visible to the owner) |
| Without systemd | `packaging/bin/hifi-bridge-respawn`: exponential back-off from 2 s to 60 s, reset after 5 min healthy (measured with `/proc/uptime`, immune to the NTP jump of a Pi without RTC), crash log bounded to the last 200 lines (`RESPAWN_LOG_KEEP`), removed by a factory reset | crash | restart |
| After a crash | `<data>/session.json` journal of the LMS item the bridge owns | an item left behind at LMS | the next start sends `playlist deleteitem <that URL>` (URL-addressed, so a no-op if anything else plays; retried while LMS is unreachable) |

**Stop signals are handled async-signal-safely** (`supervision.StopSignal`): the SIGTERM/SIGINT handler only sets a flag. An earlier handler
called `threading.Event.set()` and deadlocked when two signals arrived back to back (the second handler waited for the lock the first one
held); the process then never shut down cleanly. Found by the mDNS interop test, fixed, and covered by a test with real processes.

**Slow or dead LMS (review H2, fixed):** the controller never holds its state lock across an LMS call, so status, `/healthz` and the
watchdog answer at once during an outage; the LMS host name is looked up in one worker with a 2 s timeout and cached; a tick makes no
further LMS call after one failed. Test: a fake LMS that blocks 10 s per call while the phone plays - 25 s of watchdog pings, none
withheld, `health()` < 0.5 s (`tests/unit/test_review_controller.py`).

**Optional integrations never abort the start (review H4/H5/H6, fixed):** a failing MQTT or remote-syslog start, an mDNS error (Avahi
error reply, a label that does not fit) and a torn certificate are logged and degrade; the WebUI still starts. A part of a settings change
that fails does not skip the other parts.

`degraded` (LMS unreachable, MQTT down, warm CPU) does **not** trigger a restart. A restart would not fix an external problem and would cut a
working Bluetooth connection.

## Hardening in the unit
The service runs as user `hifi-bridge` with `CAP_NET_BIND_SERVICE` only (for ports 80 and 443). The unit also sets:
* `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`;
* the `@system-service` syscall filter;
* `MemoryHigh=192M` and `MemoryMax=256M` (to be re-measured on a Pi Zero 2 W);
* `StateDirectory=hifi-bridge` with mode 0700.

## Order and dependencies (phase-2b decisions)
* The unit is `After=` bluetooth, pipewire, wireplumber and pipewire-pulse. Whether PipeWire runs system-wide or per user in the image is open
  (OPEN_QUESTIONS #24/#25).
* WirePlumber 0.4 without logind needs `with-logind = false` (docs/PIPEWIRE_CAPTURE.md).

## Tests
| Test | Label |
|---|---|
| sd_notify on a real unix datagram socket; watchdog pings only while healthy, withholds on `failed`, and keeps pinging on `degraded` | UNIT |
| Watchdog keeps pinging while every LMS call blocks 10 s; a call that never returns is still a hang | UNIT (real threads, fake LMS) |
| `/healthz` reports a stopped control loop as `failed` with HTTP 503 | UNIT (real HTTP) |
| Crash recovery: abandoned process, next start releases exactly its URL; harmless with a foreign source; waits for an unreachable LMS | UNIT (fake LMS) |
| Respawn wrapper: back-off sequence, crash log, SIGTERM forwarded | UNIT (real `/bin/sh`) |
| Unit file | STATIC (`systemd-analyze verify`, `systemd-analyze security`) |
| Real systemd watchdog kill and restart on a device | **UNTESTED** (phase 2b) |

## Recovery entry points (stabilisation package)
* `python3 -m btbridge.recovery --data <dir> reset-owner-password | factory-reset --yes | boot-request --boot <dir>` - console only.
  On the image the boot FAT is root-owned and writable only through the group of `hifi-bridge-boot.service` (review N-14); the tool drops
  to the data owner without supplementary groups, so `boot-request --boot /boot` cannot write there - use
  `systemctl restart hifi-bridge-boot.service` (the image has no console login anyway).
* Boot-partition step `python3 -m btbridge.bootfiles --data <dir> --boot /boot` (image: `hifi-bridge-boot.service`, before the bridge;
  test Pi: `appliance --boot-dir`): recovery request (exactly once, power-cut safe), provisioning file (docs/PROVISIONING.md), an
  owner-requested settings snapshot restore (docs/OTA.md section 6; here so that `hifi-netd` starts with it), CA creation, and `hifi-status.txt` (name, CA fingerprint; a generated one-time setup code only while no owner password exists).
