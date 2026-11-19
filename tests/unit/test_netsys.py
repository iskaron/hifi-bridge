"""UNIT: the Linux adapter of the network agent with a FAKE command runner, a fake sysfs tree and fake wpa_supplicant/hostapd control
sockets (real UNIX datagram sockets speaking their protocol). The real tools are exercised in tests/net (Docker)."""
import os
import shutil
import socket
import stat
import tempfile
import threading

import pytest

from btbridge.net import netconfig as N
from btbridge.net import netsys as S

WIFI = {"ssid": "Home", "psk": "wifi-secret-123", "hidden": False, "ipv4": {"method": "dhcp"}, "ipv6": {"method": "auto"}}
HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}

# Format of `iw dev wlan0 scan` (iw 6.x, scan.c); CONSTRUCTED from the format, not recorded on hardware (none on the dev host).
IW_SCAN = """BSS aa:bb:cc:00:00:01(on wlan0)
\tTSF: 1234 usec (0d, 00:00:00)
\tfreq: 2437
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -48.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK SAE
\t\t * Capabilities: 1-PTKSA-RC 1-GTKSA-RC MFP-capable (0x0080)
BSS aa:bb:cc:00:00:02(on wlan0)
\tfreq: 2412
\tsignal: -60.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
\t\t * Authentication suites: PSK
BSS aa:bb:cc:00:00:03(on wlan0)
\tfreq: 5180
\tcapability: ESS Privacy (0x0011)
\tsignal: -81.00 dBm
\tSSID: Caf\\xc3\\xa9 Gast
\tRSN:\t * Version: 1
\t\t * Authentication suites: SAE
BSS aa:bb:cc:00:00:04(on wlan0)
\tfreq: 2462
\tcapability: ESS (0x0001)
\tsignal: -70.00 dBm
\tSSID: Open Net
BSS aa:bb:cc:00:00:05(on wlan0)
\tfreq: 2462
\tcapability: ESS Privacy (0x0011)
\tsignal: -72.00 dBm
\tSSID: Old WEP
BSS aa:bb:cc:00:00:06(on wlan0)
\tsignal: -75.00 dBm
\tSSID: Company
\tRSN:\t * Version: 1
\t\t * Authentication suites: IEEE 802.1X
BSS aa:bb:cc:00:00:07(on wlan0)
\tsignal: -77.00 dBm
\tSSID: Legacy
\tWPA:\t * Version: 1
BSS aa:bb:cc:00:00:08(on wlan0)
\tsignal: -50.00 dBm
\tSSID:
BSS aa:bb:cc:00:00:09(on wlan0)
\tSSID: NoSignal
\tRSN:\t * Version: 1
"""


def test_parse_iw_scan():
    nets = S.parse_iw_scan("garbage before the first BSS\n" + IW_SCAN)
    by = {n["ssid"]: n for n in nets}
    assert [n["ssid"] for n in nets][:2] == ["Home", "Open Net"]
    assert by["Home"] == {"ssid": "Home", "signal_dbm": -48, "security": "wpa2/wpa3", "frequency": 2437}   # strongest BSS of the SSID
    assert by["Café Gast"]["security"] == "wpa3" and by["Open Net"]["security"] == "open" and by["Old WEP"]["security"] == "wep"
    assert by["Company"]["security"] == "enterprise" and by["Legacy"]["security"] == "wpa" and by["NoSignal"]["security"] == "wpa2"
    assert "" not in by and None not in by and nets[-1]["ssid"] == "NoSignal"


def test_kv_and_unescape():
    assert S.kv("wpa_state=COMPLETED\nssid=a=b\nnoise") == {"wpa_state": "COMPLETED", "ssid": "a=b"}
    assert S._unescape("x\\x41y") == "xAy"


def make_sysfs(root, spec):
    for name, (typ, kind) in spec.items():
        d = os.path.join(root, name); os.makedirs(d)
        open(os.path.join(d, "type"), "w").write(typ + "\n")
        if kind in ("phys", "wifi"):
            os.symlink("/dev/null", os.path.join(d, "device"))
        if kind == "wifi":
            os.symlink("/dev/null", os.path.join(d, "phy80211"))


def test_discover_by_type_not_by_name(tmp_path):
    make_sysfs(str(tmp_path), {"lo": ("772", ""), "end0": ("1", "phys"), "enx0011": ("1", "phys"), "docker0": ("1", "virtual"),
                               "wlan0": ("1", "wifi"), "wlan1": ("1", "wifi"), "broken": ("1", "phys"), 'ev"il': ("1", "phys"),
                               "wl an": ("1", "wifi")})
    os.unlink(os.path.join(str(tmp_path), "broken", "type"))
    assert S.discover(str(tmp_path)) == (["end0", "enx0011"], "wlan0")
    assert S.discover(str(tmp_path / "missing")) == ([], None)


def test_rfkill_unblock_writes_one_change_all_event(tmp_path):
    dev = tmp_path / "rfkill"; dev.write_bytes(b"")
    assert S.rfkill_unblock_wifi(str(dev)) is True and dev.read_bytes() == bytes([0, 0, 0, 0, 1, 3, 0, 0])
    assert S.rfkill_unblock_wifi(str(tmp_path / "none")) is False
    d = tmp_path / "dir"; d.mkdir()
    assert S.rfkill_unblock_wifi(str(d)) is False


def test_avahi_set_hostname_without_a_bus_is_harmless():
    assert S.avahi_set_hostname("x", bus="unix:path=/nonexistent/bus") in ("unavailable", "error")


# ---------------------------------------------------------------- control socket client
class FakeCtrl:
    """A wpa_supplicant/hostapd-like control socket."""

    def __init__(self, path, replies, events_after_attach=()):
        self.path, self.replies, self.events_after_attach = path, replies, list(events_after_attach)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(path)
        self.sock.settimeout(0.2)
        self.seen, self._stop = [], threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self._stop.is_set():
            try:
                data, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            cmd = data.decode()
            self.seen.append(cmd)
            if cmd == "ATTACH":
                self.sock.sendto(b"OK\n", peer)
                for ev in self.events_after_attach:
                    self.sock.sendto(ev.encode(), peer)
                continue
            if cmd == "STATUS" and self.events_after_attach:
                self.sock.sendto(b"<2>CTRL-EVENT-SCAN-STARTED", peer)        # an event can arrive before the reply
            r = self.replies.get(cmd)
            if r is not None:
                self.sock.sendto(r.encode(), peer)

    def close(self):
        self._stop.set(); self.sock.close()


@pytest.fixture
def short_tmp():
    d = tempfile.mkdtemp(prefix="hn", dir="/tmp")             # UNIX socket paths are limited to 107 bytes
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_ctrl_client_requests_events_and_errors(short_tmp):
    srv = FakeCtrl(os.path.join(short_tmp, "wlan0"), {"STATUS": "wpa_state=COMPLETED\n"}, ["<3>CTRL-EVENT-CONNECTED - Connection to x"])
    c = S.CtrlClient(srv.path, short_tmp, timeout=1.0)
    try:
        assert c.attach() is True and c.attach() is True and srv.seen.count("ATTACH") == 1
        assert S.kv(c.request("STATUS"))["wpa_state"] == "COMPLETED"
        import time; time.sleep(0.1)
        ev = c.events()
        assert any("CTRL-EVENT-CONNECTED" in e for e in ev) and any("SCAN-STARTED" in e for e in ev)
        assert c.events() == []
        with pytest.raises(S.CtrlError):
            c.request("NO_REPLY")                                 # timeout -> closed
        assert c.sock is None and c.events() == []
    finally:
        c.close(); srv.close()
    with pytest.raises(S.CtrlError):
        S.CtrlClient(os.path.join(short_tmp, "missing"), short_tmp).request("PING")
    assert not [f for f in os.listdir(short_tmp) if f.startswith("ctrl-")]


def test_ctrl_events_after_the_peer_vanished(short_tmp):
    srv = FakeCtrl(os.path.join(short_tmp, "wlan0"), {"STATUS": "x=1\n"})
    c = S.CtrlClient(srv.path, short_tmp, timeout=1.0)
    c.request("STATUS")

    class Broken:
        def setblocking(self, f): pass
        def recv(self, n): raise ConnectionResetError()
        def settimeout(self, t): pass
        def close(self): pass
    c.sock = Broken()
    assert c.events() == [] and c.sock is None
    srv.close()


# ---------------------------------------------------------------- LinuxSystem
class Runner:
    def __init__(self, fail=(), outputs=None):
        self.calls, self.fail, self.outputs = [], set(fail), outputs or {}

    def __call__(self, argv, timeout=30.0):
        self.calls.append(argv)
        key = " ".join(argv[:3])
        if key in self.fail:
            return 1, "boom"
        return 0, self.outputs.get(key, "")

    def has(self, *argv):
        return list(argv) in self.calls


def system(short_tmp, runner=None, ifs=None, **kw):
    sysfs = os.path.join(short_tmp, "sys"); os.makedirs(sysfs)
    make_sysfs(sysfs, {"eth0": ("1", "phys"), "wlan0": ("1", "wifi")})
    logs = []
    s = S.LinuxSystem(os.path.join(short_tmp, "run"), 80, 443, 8090, sysfs=sysfs, networkd_dir=os.path.join(short_tmp, "nd"),
                      runner=runner or Runner(), interfaces=(lambda: ifs or {}), timesync_flag=os.path.join(short_tmp, "synced"),
                      log=logs.append, avahi=lambda n: "error", rfkill=lambda: True, **kw)
    return s, logs


def test_client_role_files_units_and_firewall(short_tmp):
    r = Runner()
    s, logs = system(short_tmp, r)
    assert (s.eth, s.wlan) == (["eth0"], "wlan0")
    s.ethernet(N.default_ip())
    assert os.listdir(s.networkd_dir) == ["10-hifi-ethernet.network"] and r.has("networkctl", "reload")
    s.client(WIFI, "DE")
    run = s.run_dir
    conf = open(os.path.join(run, "wpa_supplicant.conf")).read()
    assert "country=DE" in conf and stat.S_IMODE(os.stat(os.path.join(run, "wpa_supplicant.conf")).st_mode) == 0o600
    assert r.has("systemctl", "stop", "hifi-net-wpa@wlan0.service", "hifi-net-hostapd@wlan0.service", "hifi-net-dhcp@wlan0.service")
    assert r.has("iw", "reg", "set", "DE") and r.has("nft", "-f", os.path.join(run, "nftables.nft"))
    assert r.calls[-1] == ["systemctl", "start", "hifi-net-wpa@wlan0.service"]
    assert sorted(os.listdir(s.networkd_dir)) == ["10-hifi-ethernet.network", "11-hifi-wifi.network"]
    assert stat.S_IMODE(os.stat(os.path.join(s.networkd_dir, "11-hifi-wifi.network")).st_mode) == 0o644
    assert "jump hotspot" not in open(os.path.join(run, "nftables.nft")).read() and s.firewall_ok is True
    # the stop of the hotspot units comes BEFORE the client starts (never both)
    i_stop = r.calls.index(["systemctl", "stop", "hifi-net-wpa@wlan0.service", "hifi-net-hostapd@wlan0.service", "hifi-net-dhcp@wlan0.service"])
    assert i_stop < r.calls.index(["systemctl", "start", "hifi-net-wpa@wlan0.service"])


def test_hotspot_role_scans_first_and_restricts_the_firewall(short_tmp):
    r = Runner(outputs={"iw dev wlan0": IW_SCAN})
    s, _ = system(short_tmp, r)
    results = s.hotspot(HOTSPOT, "DE")
    assert results[0]["ssid"] == "Home"
    assert r.has("ip", "link", "set", "wlan0", "up") and r.has("iw", "dev", "wlan0", "scan")
    assert 'iifname "wlan0" jump hotspot' in open(os.path.join(s.run_dir, "nftables.nft")).read()
    assert "ssid2=" in open(os.path.join(s.run_dir, "hostapd.conf")).read() and "port=0" in open(os.path.join(s.run_dir, "dnsmasq.conf")).read()
    assert r.calls[-1] == ["systemctl", "start", "hifi-net-hostapd@wlan0.service", "hifi-net-dhcp@wlan0.service"]
    assert "Address=10.42.0.1/24" in open(os.path.join(s.networkd_dir, "11-hifi-wifi.network")).read()
    s.scan("ap")
    assert r.calls[-1] == ["iw", "dev", "wlan0", "scan", "ap-force"]
    s.radio_off()
    assert os.listdir(s.networkd_dir) == ["10-hifi-ethernet.network"] and "jump hotspot" not in open(os.path.join(s.run_dir, "nftables.nft")).read()


def test_failures_are_logged_and_reported_without_secrets(short_tmp):
    r = Runner(fail={"nft -f " + os.path.join(short_tmp, "run", "nftables.nft")})
    s, logs = system(short_tmp, r)
    s.client(WIFI, "DE")
    assert s.firewall_ok is False and s.errors and "boom" in s.errors[-1] and WIFI["psk"] not in " ".join(logs)
    r.fail = {"iw dev wlan0"}
    assert s.scan("station") is None and any("scan (station) not possible" in m for m in logs)


def test_without_wifi_hardware_radio_calls_do_nothing(short_tmp):
    r = Runner()
    sysfs = os.path.join(short_tmp, "sys"); os.makedirs(sysfs); make_sysfs(sysfs, {"eth0": ("1", "phys")})
    s = S.LinuxSystem(os.path.join(short_tmp, "run"), sysfs=sysfs, networkd_dir=os.path.join(short_tmp, "nd"), runner=r,
                      interfaces=lambda: {}, log=lambda m: None, rfkill=lambda: True, avahi=lambda n: "ok")
    s.client(WIFI, "DE")
    assert s.hotspot(HOTSPOT, "DE") is None and s.scan("station") is None and s.role == "off"
    assert not any(c[0] == "systemctl" and "start" in c for c in r.calls)
    s.radio_off()
    assert s.wifi_details() == {} and s.time_synchronized() is False


def test_hostname_and_avahi(short_tmp):
    r = Runner()
    s, logs = system(short_tmp, r)
    s.hostname("wohnzimmer")
    s.hostname("wohnzimmer")
    assert r.calls.count(["hostnamectl", "--transient", "set-hostname", "wohnzimmer"]) == 1
    assert any("avahi host name not updated (error)" in m for m in logs)
    r.fail = {"hostnamectl --transient set-hostname"}
    s.hostname("kitchen")
    assert s.current_hostname == "wohnzimmer"


def test_default_runner_reports_missing_tools_and_timeouts():
    rc, out = S.LinuxSystem._run(["/nonexistent/tool"])
    assert rc == 127 and "FileNotFoundError" in out
    rc, out = S.LinuxSystem._run(["sleep", "2"], timeout=0.1)
    assert rc == 127 and "TimeoutExpired" in out
    assert S.LinuxSystem._run(["true"]) == (0, "")


def test_observe_client_and_hotspot_through_control_sockets(short_tmp):
    ifs = {"eth0": {"carrier": True, "addresses": [{"address": "fe80::1", "usable": False}], "mac": "x", "default_route": False},
           "wlan0": {"carrier": True, "addresses": [{"address": "192.168.1.57", "usable": True}], "mac": "y", "default_route": True}}
    s, _ = system(short_tmp, Runner(), ifs=ifs)
    o = s.observe()
    assert o.eth_carrier is True and o.eth_up is False and o.wifi_present and o.wifi_address is False      # radio off: not "connected"
    s.client(WIFI, "DE")
    o = s.observe()
    assert o.wifi_state == "" and o.wifi_addresses == ["192.168.1.57"]                                   # wpa_supplicant not up yet
    wpa = FakeCtrl(os.path.join(s.run_dir, "wpa", "wlan0"), {"STATUS": "wpa_state=COMPLETED\nssid=Home\n", "SIGNAL_POLL": "RSSI=-52\nFREQUENCY=2437\n"},
                   ["<3>CTRL-EVENT-SSID-TEMP-DISABLED reason=WRONG_KEY"])
    try:
        o = s.observe()
        assert o.wifi_state == "COMPLETED" and o.wifi_address is True
        import time; time.sleep(0.1)
        assert any("WRONG_KEY" in e for e in s.observe().events + o.events)
        assert s.wifi_details() == {"signal_dbm": -52, "frequency": 2437}
        wpa.replies["SIGNAL_POLL"] = "RSSI=bad\n"
        assert s.wifi_details() == {}
    finally:
        wpa.close()
    os.unlink(wpa.path)
    s.wpa.sock.settimeout(0.2)
    assert s.observe().wifi_state == "" and s.wpa is None                                                 # daemon gone: client reset
    s.hotspot(HOTSPOT, "DE")
    ap = FakeCtrl(os.path.join(s.run_dir, "hostapd", "wlan0"), {"STATUS": "state=ENABLED\nnum_sta[0]=2\n"})
    try:
        assert s.observe().stations == 2
        ap.replies["STATUS"] = "num_sta[0]=x\n"
        assert s.observe().stations == 0 and s.ap is None
    finally:
        ap.close()
    open(os.path.join(short_tmp, "synced"), "w").close()
    assert s.time_synchronized() is True


def test_observe_rediscovers_a_late_wifi_interface(short_tmp):
    s, _ = system(short_tmp, Runner())
    shutil.rmtree(os.path.join(s.sysfs, "wlan0"))
    s.refresh()
    assert s.wlan is None and s.observe().wifi_present is False
    make_sysfs(s.sysfs, {"wlan0": ("1", "wifi")})
    assert s.observe().wifi_present is True and s.wlan == "wlan0"


def test_one_control_client_used_by_two_threads_never_mixes_up_replies(short_tmp):
    """Review N-7: the agent's tick (ATTACH/STATUS/events) and its status request (SIGNAL_POLL) share one CtrlClient. Unserialised,
    a STATUS reply was consumed as SIGNAL_POLL and vice versa ('Wi-Fi connection lost', missing signal) and one thread closed the
    socket under the other."""
    import time
    path = os.path.join(short_tmp, "wlan0")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(0.2)
    stop = threading.Event()

    def serve():                                              # like wpa_supplicant: answers after a moment, sometimes an event first
        n = 0
        while not stop.is_set():
            try:
                data, peer = srv.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:                                   # closed at the end of the test
                return
            n += 1
            time.sleep(0.0003)
            if n % 7 == 0:
                srv.sendto(b"<3>CTRL-EVENT-SCAN-RESULTS ", peer)
            reply = {"STATUS": b"wpa_state=COMPLETED\nssid=x\n", "SIGNAL_POLL": b"RSSI=-50\nFREQUENCY=2412\n"}.get(data.decode(), b"OK\n")
            srv.sendto(reply, peer)
    threading.Thread(target=serve, daemon=True).start()
    c = S.CtrlClient(path, short_tmp, timeout=2.0)
    bad, errors = [], []

    def tick():
        for _ in range(1000):
            try:
                c.attach()
                if S.kv(c.request("STATUS")).get("wpa_state") != "COMPLETED":
                    bad.append("STATUS")
                c.events()
            except S.CtrlError as e:
                errors.append(e)

    def status():
        for _ in range(1000):
            try:
                if "RSSI" not in S.kv(c.request("SIGNAL_POLL")):
                    bad.append("SIGNAL_POLL")
            except S.CtrlError as e:
                errors.append(e)
    try:
        threads = [threading.Thread(target=tick), threading.Thread(target=status)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert bad == [] and errors == []
    finally:
        stop.set(); c.close(); srv.close()
