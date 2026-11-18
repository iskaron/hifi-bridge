"""UNIT: rendered configuration files (the same files are parsed by the REAL wpa_supplicant/hostapd/dnsmasq/nft/systemd-networkd in
tests/net). Here: content rules - hex SSIDs (no injection), security settings, no DNS/default route on the hotspot, firewall policy."""
from btbridge.net import netconfig as N
from btbridge.net import netrender as R

CFG = N.validate({"country": "DE", "wifi": {"ssid": 'Home "net"\nx', "psk": 'pa"ss word#1'},
                  "hotspot": {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}})


def test_wpa_supplicant_uses_hex_ssid_wpa2_wpa3_and_pmf():
    conf = R.wpa_supplicant(CFG["wifi"], "DE")
    assert "ssid=" + 'Home "net"\nx'.encode().hex() in conf and "\nx" not in conf.split("network={")[1].split("psk")[0]
    assert 'psk="pa"ss word#1"' in conf and "key_mgmt=WPA-PSK WPA-PSK-SHA256 SAE" in conf and "ieee80211w=1" in conf
    assert "country=DE" in conf and "update_config=0" in conf and "scan_ssid=0" in conf
    hidden = R.wpa_supplicant({**CFG["wifi"], "hidden": True}, "DE")
    assert "scan_ssid=1" in hidden
    raw = R.wpa_supplicant({**CFG["wifi"], "psk": "AB" * 32}, "DE")
    assert "psk=" + "ab" * 32 in raw and "SAE" not in raw                        # a raw PSK cannot do WPA3-SAE


def test_hostapd_is_wpa2_ccmp_with_isolation_and_country():
    conf = R.hostapd(CFG["hotspot"], "DE", "wlan0")
    for line in ("interface=wlan0", "driver=nl80211", "ssid2=" + "Hi-Fi Bridge 4F2A".encode().hex(), "country_code=DE", "ieee80211d=1",
                 "channel=6", "wpa=2", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP", "wpa_passphrase=correct horse battery", "ap_isolate=1",
                 "ignore_broadcast_ssid=0"):
        assert line in conf.splitlines(), line
    assert "wpa=1" not in conf and "TKIP" not in conf and "wep" not in conf.lower()


def test_dnsmasq_serves_dhcp_only_without_dns_or_router():
    conf = R.dnsmasq("wlan0").splitlines()
    assert "port=0" in conf and "interface=wlan0" in conf and "bind-interfaces" in conf and "listen-address=10.42.0.1" in conf
    assert "dhcp-option=option:router" in conf and "dhcp-option=option:dns-server" in conf          # empty: no default route, no DNS
    assert "dhcp-range=10.42.0.10,10.42.0.60,255.255.255.0,1h" in conf and "no-resolv" in conf
    assert not any(x.startswith(("address=", "server=", "dhcp-option=114", "dhcp-option=option:captive")) for x in conf)


def test_networkd_ethernet_dhcp_and_auto_ipv6():
    files = R.networkd_files(CFG, ["eth0", "end0"], "wlan0", "off")
    assert list(files) == ["10-hifi-ethernet.network"]
    eth = files["10-hifi-ethernet.network"]
    assert "Name=eth0 end0" in eth and "DHCP=ipv4" in eth and "IPv6AcceptRA=yes" in eth and "SendHostname=yes" in eth
    assert "[IPv6AcceptRA]" in eth and "LinkLocalAddressing=ipv6" in eth


def test_networkd_static_and_off():
    cfg = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1", "dns": ["192.168.1.1"]},
                                   "ipv6": {"method": "off"}},
                      "country": "DE", "wifi": {"ssid": "Home", "psk": "12345678",
                                                "ipv4": {"method": "dhcp"},
                                                "ipv6": {"method": "static", "address": "2001:db8::9/64", "gateway": "fe80::1", "dns": ["2001:db8::53"]}}})
    files = R.networkd_files(cfg, ["eth0"], "wlan0", "client")
    eth, wifi = files["10-hifi-ethernet.network"], files["11-hifi-wifi.network"]
    for line in ("DHCP=no", "LinkLocalAddressing=no", "IPv6AcceptRA=no", "Address=192.168.1.20/24", "Gateway=192.168.1.1", "DNS=192.168.1.1"):
        assert line in eth.splitlines(), line
    assert "[DHCPv4]" not in eth
    for line in ("Name=wlan0", "DHCP=ipv4", "IPv6AcceptRA=no", "Address=2001:db8::9/64", "Gateway=fe80::1", "DNS=2001:db8::53"):
        assert line in wifi.splitlines(), line
    no_gw = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24"}}})
    assert "Gateway=" not in R.networkd_files(no_gw, ["eth0"], None, "off")["10-hifi-ethernet.network"]


def test_networkd_hotspot_and_no_interfaces():
    files = R.networkd_files(CFG, [], "wlan0", "hotspot")
    assert list(files) == ["11-hifi-wifi.network"]
    ap = files["11-hifi-wifi.network"].splitlines()
    assert "Address=10.42.0.1/24" in ap and "DHCP=no" in ap and "IPv6AcceptRA=no" in ap and "RequiredForOnline=no" in ap
    assert R.networkd_files(CFG, [], None, "client") == {}
    assert R.networkd_files(N.defaults(), [], "wlan0", "client") == {}                 # client role without Wi-Fi config: nothing


def test_nftables_lan_policy():
    rs = R.nftables(80, 443, 8090)
    assert "policy drop;" in rs and "hook forward" in rs and "jump hotspot" not in rs and "chain hotspot" not in rs
    assert "tcp dport { 80, 443, 8090 } accept" in rs and "udp dport 5353 accept" in rs and "udp sport 67 udp dport 68 accept" in rs
    assert rs.index("delete table inet hifi") < rs.index("table inet hifi {")       # atomic replace in one transaction
    assert "nd-neighbor-solicit" in rs and "ct state established,related accept" in rs


def test_nftables_hotspot_clients_reach_only_dhcp_and_webui():
    rs = R.nftables(80, 443, 8090, hotspot_iface="wlan0")
    hot = rs.split("chain hotspot {")[1].split("\n\t}")[0]
    assert "udp dport 67 accept" in hot and "tcp dport { 80, 443 } accept" in hot and hot.strip().endswith("drop")
    assert "8090" not in hot
    assert rs.index('iifname "wlan0" jump hotspot') < rs.index("tcp dport { 80, 443, 8090 } accept")
    assert rs.index('iifname "wlan0" jump hotspot') < rs.index("udp sport 3483")
