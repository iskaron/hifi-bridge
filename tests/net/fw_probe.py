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
ap.add_argument("--udp-open", default="")
ap.add_argument("--udp-closed", default="")
ap.add_argument("--udp-sport", type=int, default=0, help="source port of the UDP probes (3483 = a spoofed Lyrion server)")
a = ap.parse_args()


def udp(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", a.udp_sport))
    s.settimeout(2)
    try:
        s.sendto(b"probe", (a.target, port))
        return "open" if s.recvfrom(64)[0].startswith(b"echo") else "other"
    except socket.timeout:
        return "dropped"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


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
for kind, want in (("udp_open", "open"), ("udp_closed", "dropped")):
    for p in [int(x) for x in getattr(a, kind).split(",") if x]:
        r = udp(p)
        bad += r != want
        print(f"{'PASS' if r == want else 'FAIL'} {me} -> {a.target}:{p}/udp from source port {a.udp_sport} expected {want}, got {r}")
if a.ping:
    ok = subprocess.run(["busybox", "ping", "-c", "1", "-W", "2", a.target], capture_output=True).returncode == 0
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} {me} -> {a.target} ICMP echo")
sys.exit(1 if bad else 0)
