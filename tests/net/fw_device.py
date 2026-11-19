"""Device side of the live firewall test (scripts/run_net.sh): loads the GENERATED hotspot ruleset into this container's namespace,
starts the GENERATED dnsmasq on the hotspot interface and listens on the WebUI ports, the stream port and an unrelated port (2222), so a
blocked port is blocked by the firewall and not by a missing service."""
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/work/bridge")
from btbridge.net import netlink, netrender  # noqa: E402

ifs = netlink.interfaces()
hs = next(n for n, i in ifs.items() if any(a["address"] == "10.42.0.1" for a in i["addresses"]))
open("/tmp/fw.nft", "w").write(netrender.nftables(80, 443, 8090, hotspot_iface=hs))
subprocess.run(["nft", "-f", "/tmp/fw.nft"], check=True)
open("/tmp/dnsmasq.conf", "w").write(netrender.dnsmasq(hs, "/tmp"))
dns = subprocess.Popen(["dnsmasq", "-k", "-C", "/tmp/dnsmasq.conf"])


def serve(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(8)
    while True:
        c, _ = s.accept()
        c.sendall(b"hello\n")
        c.close()


for p in (80, 443, 8090, 2222, 53):
    threading.Thread(target=serve, args=(p,), daemon=True).start()
time.sleep(0.5)
print(f"ready hotspot_iface={hs} dnsmasq_pid={dns.pid}", flush=True)
print(subprocess.run(["nft", "list", "table", "inet", "hifi"], capture_output=True, text=True).stdout, flush=True)
while True:
    time.sleep(3600)
