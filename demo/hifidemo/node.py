"""The demo's 'Raspberry Pi': the UNCHANGED bridge product plus the SIMULATED radio stack it talks to.

Started processes (supervised; if one exits, all stop and Docker restarts the container):
  * a private D-Bus (stands in for the system bus) and PipeWire + WirePlumber + pipewire-pulse (real, no audio hardware);
  * ``hifidemo.vbluez`` - the simulated bluetoothd the phones talk to over the air;
  * ``btbridge.appliance --source bluez --dbus <private bus>`` - the product, exactly as on the device (no simulated adapter inside it).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

DATA = os.environ.get("DEMO_DATA", "/data")
BOOT = os.environ.get("DEMO_BOOT", "/boot-demo")
RUN = os.environ.get("DEMO_RUN", "/run/hifi")
PW_SCRIPT = os.environ.get("DEMO_PW_SCRIPT", "/opt/hifi-bridge/testkit/pw/start_pipewire.sh")
DEMO_DEFAULTS = {"extra_hostnames": "127.0.0.1"}     # so the notebook's browser can use HTTPS at 127.0.0.1 with the device CA


def log(m: str) -> None:
    print(f"[node] {m}", flush=True)


def pipewire_env() -> dict:
    """Start PipeWire with the lab script and return its environment (XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, ...)."""
    os.makedirs(os.path.join(RUN, "pw-logs"), exist_ok=True)
    out = subprocess.run(["bash", "-c", f'PW_LOG={RUN}/pw-logs . "{PW_SCRIPT}" >/dev/null && env -0'], capture_output=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("PipeWire did not start: " + out.stderr.decode(errors="replace")[-400:])
    env = dict(x.split("=", 1) for x in out.stdout.decode().split("\0") if "=" in x)
    return {k: env[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME") if k in env}


def seed_settings(bridge_data: str) -> None:
    """First start only: demo defaults the owner could also set in the WebUI (Access -> additional host names)."""
    if os.path.exists(os.path.join(bridge_data, "settings.json")):
        return
    from btbridge.config.store import ConfigStore
    ConfigStore(bridge_data).update(DEMO_DEFAULTS)
    log(f"first start: demo defaults {DEMO_DEFAULTS}")


def appliance_argv(bus: str) -> list[str]:
    return [sys.executable, "-m", "btbridge.appliance", "--data", os.path.join(DATA, "bridge"), "--boot-dir", BOOT,
            "--http-port", os.environ.get("DEMO_HTTP_PORT", "18080"), "--https-port", os.environ.get("DEMO_HTTPS_PORT", "18443"),
            "--stream-port", "8090", "--advertise-host", "hifi-bridge.local", "--source", "bluez", "--dbus", bus,
            "--thermal-path", ""]


def vbluez_argv(bus: str) -> list[str]:
    return [sys.executable, "-m", "hifidemo.vbluez", "--bus", bus, "--state", os.path.join(DATA, "bluetooth", "devices.json"),
            "--fifo-dir", os.path.join(RUN, "a2dp"), "--air-port", "8099", "--pcm-port", "8098"]


def main() -> int:
    from btkit.fake_bluez import PrivateBus
    os.makedirs(os.path.join(DATA, "bridge"), exist_ok=True)
    seed_settings(os.path.join(DATA, "bridge"))
    env = dict(os.environ, **pipewire_env())
    log("PipeWire running (no audio hardware)")
    bus = PrivateBus()
    log(f"private D-Bus for the (simulated) Bluetooth stack: {bus.address}")
    procs = [subprocess.Popen(vbluez_argv(bus.address), env=env)]
    time.sleep(1.0)                                     # bluetoothd first (the product also copes with a late one)
    procs.append(subprocess.Popen(appliance_argv(bus.address), env=env))
    log("bridge product started: " + " ".join(appliance_argv(bus.address)[1:]))
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    rc = 0
    while not stop["flag"]:
        dead = [p for p in procs if p.poll() is not None]
        if dead:
            rc = dead[0].returncode or 1
            log(f"process {dead[0].args[2]} exited with {dead[0].returncode}; stopping the node")
            break
        time.sleep(0.5)
    for p in reversed(procs):
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    bus.close()
    return 0 if stop["flag"] else rc


if __name__ == "__main__":
    sys.exit(main())
