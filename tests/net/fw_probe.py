"""Client side of the live firewall test: TCP connects to the expected open and closed ports of the device and pings it (exit 1 on
any mismatch). A closed port must time out (dropped), because the device listens on every probed port."""
import argparse
import socket
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("target")
ap.add_argument("--open", default="")
ap.add_argument("--closed", default="")
ap.add_argument("--ping", action="store_true")
a = ap.parse_args()


def connect(port):
    try:
        with socket.create_connection((a.target, port), timeout=2):
            return "open"
    except socket.timeout:
        return "dropped"
    except OSError as e:
        return f"error:{e.errno}"


bad = 0
me = socket.gethostname()
for p in [int(x) for x in a.open.split(",") if x]:
    r = connect(p)
    ok = r == "open"
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} {me} -> {a.target}:{p} expected open, got {r}")
for p in [int(x) for x in a.closed.split(",") if x]:
    r = connect(p)
    ok = r == "dropped"
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} {me} -> {a.target}:{p} expected dropped, got {r}")
if a.ping:
    ok = subprocess.run(["busybox", "ping", "-c", "1", "-W", "2", a.target], capture_output=True).returncode == 0
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} {me} -> {a.target} ICMP echo")
sys.exit(1 if bad else 0)
