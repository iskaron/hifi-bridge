"""INTEGRATION (real tools in a disposable container with CAP_NET_ADMIN in its OWN network namespace; scripts/run_net.sh): the files the
network agent generates are accepted by the real nft, dnsmasq, hostapd, wpa_supplicant and systemd-networkd; the rtnetlink reader sees
what the kernel has; the control-socket client works against a real wpa_supplicant; the Avahi host-name adapter works against a real
avahi-daemon; the units pass systemd-analyze. Negative controls prove that each parser check can fail.

Not covered (no hardware): association, real AP mode, regulatory enforcement, brcmfmac firmware - see docs/NETWORK.md."""
import os
import re
import shutil
import subprocess
import tempfile
import time

import pytest

from btbridge.net import netconfig as N
from btbridge.net import netlink, netrender, netsys

pytestmark = pytest.mark.skipif(shutil.which("nft") is None or os.geteuid() != 0, reason="run in btbridge-net via scripts/run_net.sh")

CFG = N.validate({"country": "DE", "wifi": {"ssid": 'Wohnzimmer "5G" ☕', "psk": 'pa"ss word#1', "hidden": True},
                  "hotspot": {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"},
                  "ethernet": {"ipv4": {"method": "static", "address": "192.168.77.5/24", "gateway": "192.168.77.1", "dns": ["192.168.77.1"]},
                               "ipv6": {"method": "static", "address": "fd00:77::5/64", "gateway": "fe80::1", "dns": ["fd00:77::53"]}}})
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNITS = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge", "overlay", "usr", "lib", "systemd", "system")


def sh(*argv, check=False, timeout=30, **kw):
    r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode:
        raise AssertionError(f"{argv}: {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


@pytest.fixture
def d():
    p = tempfile.mkdtemp(prefix="hn", dir="/tmp")
    yield p
    shutil.rmtree(p, ignore_errors=True)


@pytest.fixture
def dummy():
    names = []

    def make(name):
        sh("ip", "link", "add", name, "type", "dummy", check=True)
        sh("ip", "link", "set", name, "up", check=True)
        names.append(name)
        return name
    yield make
    for n in names:
        sh("ip", "link", "del", n)


def networkd(level):
    """systemd-networkd without systemd as PID 1: started as its own user with ambient capabilities (Docker grants no inheritable
    capabilities, so networkd's own privilege drop fails)."""
    os.makedirs("/run/systemd/netif", exist_ok=True)
    shutil.chown("/run/systemd/netif", "systemd-network", "systemd-network")
    caps = "+net_admin,+net_raw,+net_broadcast,+net_bind_service"
    env = {**os.environ, "SYSTEMD_LOG_LEVEL": level, "SYSTEMD_LOG_TARGET": "console"}
    return subprocess.Popen(["setpriv", "--reuid=systemd-network", "--regid=systemd-network", "--clear-groups", f"--inh-caps={caps}",
                             f"--ambient-caps={caps}", "/usr/lib/systemd/systemd-networkd"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def write(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


# ---------------------------------------------------------------- nftables
@pytest.mark.parametrize("hotspot", [None, "wlan0"])
def test_nft_accepts_and_loads_the_generated_ruleset(d, hotspot):
    p = write(d, "fw.nft", netrender.nftables(80, 443, 8090, hotspot_iface=hotspot))
    sh("nft", "-c", "-f", p, check=True)
    sh("nft", "-f", p, check=True)
    sh("nft", "-f", p, check=True)                                    # re-apply: atomic replace, no "table exists" error
    listing = sh("nft", "list", "table", "inet", "hifi", check=True).stdout
    assert "policy drop" in listing and ("chain hotspot" in listing) == bool(hotspot)
    base = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge", "overlay", "usr", "share", "hifi-bridge", "nftables-base.nft")
    sh("nft", "-c", "-f", base, check=True)
    sh("nft", "delete", "table", "inet", "hifi", check=True)


def test_nft_negative_control(d):
    p = write(d, "bad.nft", netrender.nftables(80, 443, 8090).replace("policy drop;", "policy maybe;"))
    assert sh("nft", "-c", "-f", p).returncode != 0


# ---------------------------------------------------------------- dnsmasq
def test_dnsmasq_accepts_the_generated_configuration(d):
    p = write(d, "dnsmasq.conf", netrender.dnsmasq("wlan0", d))
    r = sh("dnsmasq", "--test", "-C", p)
    assert r.returncode == 0 and "syntax check OK" in (r.stdout + r.stderr)
    bad = write(d, "bad.conf", netrender.dnsmasq("wlan0", d) + "dhcp-range=nonsense\n")
    assert sh("dnsmasq", "--test", "-C", bad).returncode != 0


# ---------------------------------------------------------------- hostapd / wpa_supplicant (parse up to the driver)
def run_until_driver(argv, timeout=8):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return (e.stdout or b"").decode() + (e.stderr or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "") + (e.stderr or "")


def test_hostapd_parses_the_generated_configuration(d, dummy):
    dummy("wlan0")
    good = write(d, "hostapd.conf", netrender.hostapd(CFG["hotspot"], "DE", "wlan0", os.path.join(d, "hostapd")))
    out = run_until_driver(["hostapd", "-dd", good])
    assert "Line " not in out and "errors found in configuration file" not in out, out[-2000:]
    assert "nl80211" in out                                           # parsing done, the driver (no Wi-Fi here) is what stops it
    bad = write(d, "bad.conf", open(good).read().replace("wpa_key_mgmt=WPA-PSK", "wpa_key_mgmt=NONSENSE"))
    assert "errors found in configuration file" in run_until_driver(["hostapd", "-dd", bad])


def test_wpa_supplicant_parses_the_generated_configuration(d, dummy):
    dummy("wlan0")
    good = write(d, "wpa.conf", netrender.wpa_supplicant(CFG["wifi"], "DE", os.path.join(d, "wpa")))
    out = run_until_driver(["wpa_supplicant", "-i", "wlan0", "-D", "nl80211", "-c", good, "-dd"])
    assert "failed to parse" not in out and not re.search(r"Line \d+: ", out), out[-2000:]       # "Line: 6 - start of ..." is info
    assert "Reading configuration file" in out and "nl80211" in out
    raw = write(d, "raw.conf", netrender.wpa_supplicant({**CFG["wifi"], "psk": "ab" * 32}, "DE", os.path.join(d, "wpa")))
    assert "failed to parse" not in run_until_driver(["wpa_supplicant", "-i", "wlan0", "-D", "nl80211", "-c", raw, "-dd"])
    bad = write(d, "bad.conf", open(good).read().replace("key_mgmt=WPA-PSK WPA-PSK-SHA256 SAE", "key_mgmt=NONSENSE"))
    assert "failed to parse" in run_until_driver(["wpa_supplicant", "-i", "wlan0", "-D", "nl80211", "-c", bad, "-dd"])


@pytest.mark.parametrize("pw,ssid", [('pa"ss word#1', 'Wohnzimmer "5G" ☕'), ("correct horse battery", "Hi-Fi Bridge 4F2A"),
                                     ("~!@#$%^&*()_+{}|:<>?`-=[]\\;',./ x", "Küche")])
def test_pmk_equals_wpa_passphrase(pw, ssid):
    out = sh("wpa_passphrase", ssid, pw, check=True).stdout
    assert f"\tpsk={netrender.pmk(pw, ssid)}" in out


def test_control_socket_client_against_a_real_wpa_supplicant(d, dummy):
    """wpa_supplicant with the wired driver on a dummy link: same control interface as with nl80211."""
    dummy("hn0")
    ctrl = os.path.join(d, "wpa")
    conf = write(d, "wired.conf", f"ctrl_interface=DIR={ctrl} GROUP=root\nap_scan=0\nnetwork={{\n\tkey_mgmt=NONE\n}}\n")
    p = subprocess.Popen(["wpa_supplicant", "-i", "hn0", "-D", "wired", "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if os.path.exists(os.path.join(ctrl, "hn0")):
                break
            time.sleep(0.1)
        os.makedirs(os.path.join(d, "ctl"), exist_ok=True)
        c = netsys.CtrlClient(os.path.join(ctrl, "hn0"), os.path.join(d, "ctl"), timeout=3)
        assert c.attach() is True
        st = netsys.kv(c.request("STATUS"))
        assert "wpa_state" in st, st
        assert c.request("PING").strip() == "PONG"
        c.request("REASSOCIATE")
        time.sleep(0.5)
        assert isinstance(c.events(), list)
        c.close()
    finally:
        p.terminate(); p.wait(5)


# ---------------------------------------------------------------- systemd-networkd + netlink
def test_networkd_applies_the_generated_static_addresses_and_netlink_reads_them(d, dummy):
    dummy("hn1")
    nd = "/run/systemd/network"
    os.makedirs(nd, exist_ok=True)
    for name, text in netrender.networkd_files(CFG, ["hn1"], None, "off").items():
        write(nd, name, text)
    p = networkd("info")
    try:
        found = {}
        for _ in range(60):
            found = netlink.interfaces().get("hn1", {})
            have = {a["address"] for a in found.get("addresses", [])}
            if {"192.168.77.5", "fd00:77::5"} <= have:
                break
            time.sleep(0.25)
        have = {a["address"] for a in found.get("addresses", [])}
        assert {"192.168.77.5", "fd00:77::5"} <= have, (found, p.poll())
        prefixes = {(a["address"], a["prefix"]) for a in found["addresses"]}
        assert ("192.168.77.5", 24) in prefixes and ("fd00:77::5", 64) in prefixes
    finally:
        p.terminate()
        try:
            out = p.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            p.kill(); out = p.communicate()[0]
        for name in os.listdir(nd):
            if name.startswith(("10-hifi", "11-hifi")):
                os.unlink(os.path.join(nd, name))
    assert "Unknown key" not in out and "Failed to parse" not in out and "Invalid" not in out, out


def test_networkd_accepts_the_dhcp_and_hotspot_files(d, dummy):
    """Parse check of the remaining variants: networkd logs every unknown or invalid line."""
    dummy("hn2")
    nd = "/run/systemd/network"
    os.makedirs(nd, exist_ok=True)
    files = dict(netrender.networkd_files(N.validate({"country": "DE", "wifi": {"ssid": "x", "psk": "12345678"}}), ["hn2"], "hn3", "client"))
    files["12-hifi-ap.network"] = netrender.networkd_files(CFG, [], "hn2", "hotspot")["11-hifi-wifi.network"]
    for name, text in files.items():
        write(nd, name, text)
    p = networkd("debug")
    time.sleep(3)
    p.terminate()
    out = p.communicate(timeout=10)[0]
    for name in list(files):
        os.unlink(os.path.join(nd, name))
    assert "Unknown key" not in out and "Failed to parse" not in out and "unknown section" not in out.lower(), out[-3000:]


def test_netlink_reader_matches_ip_addr(dummy):
    dummy("hn4")
    sh("ip", "addr", "add", "10.99.0.7/16", "dev", "hn4", check=True)
    sh("ip", "-6", "addr", "add", "fd00:99::7/64", "dev", "hn4", "nodad", check=True)
    sh("ip", "route", "add", "default", "via", "10.99.0.1", "dev", "hn4", check=True)
    i = netlink.interfaces()["hn4"]
    addrs = {(a["address"], a["prefix"], a["usable"]) for a in i["addresses"]}
    assert ("10.99.0.7", 16, True) in addrs and ("fd00:99::7", 64, True) in addrs and i["default_route"] is True and i["up"] is True


# ---------------------------------------------------------------- the agent's Linux adapter with the real nft
def test_linux_system_applies_the_firewall_with_the_real_nft(d):
    calls = []

    def runner(argv, timeout=30.0):
        if argv[0] == "nft":
            r = subprocess.run(argv, capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr
        calls.append(argv)
        return 0, ""
    sysfs = os.path.join(d, "sys", "wlan9"); os.makedirs(sysfs); os.symlink("/dev/null", os.path.join(sysfs, "phy80211"))
    s = netsys.LinuxSystem(os.path.join(d, "run"), sysfs=os.path.join(d, "sys"), networkd_dir=os.path.join(d, "nd"), runner=runner,
                           log=lambda m: None, rfkill=lambda: True, avahi=lambda n: "ok")
    s.hotspot(CFG["hotspot"], "DE")
    assert s.firewall_ok is True and 'iifname "wlan9" jump hotspot' in sh("nft", "list", "table", "inet", "hifi").stdout
    s.client(CFG["wifi"], "DE")
    assert s.firewall_ok is True and "jump hotspot" not in sh("nft", "list", "table", "inet", "hifi").stdout
    sh("nft", "delete", "table", "inet", "hifi")


# ---------------------------------------------------------------- Avahi host name (root) against a real avahi-daemon
def test_avahi_set_hostname_against_a_real_daemon():
    os.makedirs("/run/dbus", exist_ok=True)
    dbus = subprocess.Popen(["dbus-daemon", "--system", "--nofork", "--nopidfile"])
    time.sleep(0.5)
    avahi = subprocess.Popen(["avahi-daemon", "--no-drop-root", "--no-rlimits"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            if netsys.avahi_set_hostname("hifi-netd-probe") in ("ok", "same"):
                break
            time.sleep(0.25)
        assert netsys.avahi_set_hostname("wohnzimmer") == "ok"
        assert netsys.avahi_set_hostname("wohnzimmer") == "same"
        r = sh("avahi-resolve", "-n", "wohnzimmer.local") if shutil.which("avahi-resolve") else None
        assert r is None or r.returncode in (0, 1)
    finally:
        avahi.terminate(); avahi.wait(5); dbus.terminate(); dbus.wait(5)


# ---------------------------------------------------------------- systemd-resolved with the image's drop-in (review N-8)
RESOLVED_DROPIN = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge", "overlay", "etc", "systemd", "resolved.conf.d",
                               "hifi-bridge.conf")


def resolved_listeners(until=None, seconds=10.0):
    """Start the real systemd-resolved (own user, like networkd above) and return its listening sockets once ``until(sockets)`` is
    true, or after ``seconds`` (the stub resolver must be up in any case)."""
    os.makedirs("/run/systemd/resolve", exist_ok=True)
    shutil.chown("/run/systemd/resolve", "systemd-resolve", "systemd-resolve")
    caps = "+net_raw,+net_bind_service"
    p = subprocess.Popen(["setpriv", "--reuid=systemd-resolve", "--regid=systemd-resolve", "--clear-groups", f"--inh-caps={caps}",
                          f"--ambient-caps={caps}", "/usr/lib/systemd/systemd-resolved"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "SYSTEMD_LOG_TARGET": "console"})
    try:
        end, socks = time.time() + seconds, set()
        while time.time() < end:
            socks = {ln.split()[0] + " " + ln.split()[4] for ln in sh("ss", "-H", "-lntu").stdout.splitlines() if ln.strip()}
            if until and until(socks):
                break
            time.sleep(0.25)
        return socks
    finally:
        p.terminate()
        p.wait(5)


def test_resolved_with_the_image_dropin_answers_no_llmnr_or_mdns(dummy):
    """Compiled-in defaults: LLMNR responder on UDP+TCP 5355 of every multicast link (negative control). With the image's drop-in:
    still a DNS client (stub on 127.0.0.53), but no 5355 and no 5353 socket."""
    link = dummy("hr0")
    sh("ip", "link", "set", link, "multicast", "on", check=True)
    sh("ip", "addr", "add", "192.168.99.5/24", "dev", link, check=True)
    conf_d = "/etc/systemd/resolved.conf.d"
    shutil.rmtree(conf_d, ignore_errors=True)
    default = resolved_listeners(until=lambda x: any(s.endswith(":5355") for s in x))
    assert any(s.endswith(":5355") for s in default), default
    os.makedirs(conf_d)
    shutil.copy(RESOLVED_DROPIN, conf_d)
    try:
        ours = resolved_listeners(seconds=8.0)                       # the full time: a responder could appear late
    finally:
        shutil.rmtree(conf_d, ignore_errors=True)
    assert any("127.0.0.53" in s and s.endswith(":53") for s in ours), ours       # still the DNS client
    assert not any(s.endswith((":5355", ":5353")) for s in ours), ours


# ---------------------------------------------------------------- units
def test_units_pass_systemd_analyze_verify(d):
    etc = "/etc/systemd/system"
    for name in ("hifi-netd.service", "hifi-firewall.service"):
        shutil.copy(os.path.join(UNITS, name), etc)
    for tpl in ("hifi-net-wpa@", "hifi-net-hostapd@", "hifi-net-dhcp@"):
        shutil.copy(os.path.join(UNITS, tpl + ".service"), etc)
    os.makedirs("/usr/share/hifi-bridge", exist_ok=True)
    shutil.copy(os.path.join(UNITS, "..", "..", "..", "share", "hifi-bridge", "nftables-base.nft"), "/usr/share/hifi-bridge/")
    r = sh("systemd-analyze", "verify", "hifi-netd.service", "hifi-firewall.service", "hifi-net-wpa@wlan0.service",
           "hifi-net-hostapd@wlan0.service", "hifi-net-dhcp@wlan0.service", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    sec = sh("systemd-analyze", "security", "--offline=yes", os.path.join(etc, "hifi-netd.service"), timeout=60)
    print("hifi-netd.service", sec.stdout.splitlines()[-1] if sec.stdout else sec.stderr)
