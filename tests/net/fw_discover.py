"""Runs the bridge's REAL Lyrion discovery inside the device container (behind the generated ruleset, scripts/run_net.sh) and checks
that the fake Lyrion server's answer passes the firewall: the narrowed rule (udp sport 3483 udp dport 3484, review N-8) must still let
discovery work. Usage: fw_discover.py <expected server address>"""
import sys

sys.path.insert(0, "/work/bridge")
from btbridge.lms import discovery  # noqa: E402

want = sys.argv[1]
found = discovery.discover(timeout=2.5)
hit = [f for f in found if f.get("address") == want]
ok = bool(hit) and hit[0].get("NAME") == "Lab Lyrion" and hit[0].get("JSON") == "9000"
print(f"{'PASS' if ok else 'FAIL'} discovery from local port {discovery.REPLY_PORT} behind the firewall found {found}")
sys.exit(0 if ok else 1)
