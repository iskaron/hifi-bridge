"""UNIT: network.json schema (docs/NETWORK.md) - strict validation by field and reason (never values), compatibility with the provisioning
output, static IPv4/IPv6 rules, redaction, strict reading by the root agent (symlinks, owner, permissions, size), change analysis, reset."""
import json
import os

import pytest

from btbridge import provisioning
from btbridge.net import netconfig as N

WIFI = {"ssid": "Home", "psk": "wifi-secret-123"}
HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}


def test_defaults_are_valid_and_dhcp_everywhere():
    d = N.defaults()
    assert N.validate(d) == d
    assert d["ethernet"] == {"ipv4": {"method": "dhcp"}, "ipv6": {"method": "auto"}} and d["wifi"] is None and d["hotspot"] is None


def test_provisioning_output_is_valid_input_and_gets_defaults():
    doc = {"format": N.FORMAT, "country": "DE", "hotspot": dict(HOTSPOT), "wifi": dict(WIFI)}
    cfg = N.validate(doc)
    assert cfg["wifi"] == {**WIFI, "hidden": False, "ipv4": {"method": "dhcp"}, "ipv6": {"method": "auto"}}
    assert cfg["hotspot"] == HOTSPOT and cfg["country"] == "DE"
    assert N.HOTSPOT_PSK_MIN == provisioning.HOTSPOT_PSK_MIN       # one rule for the hotspot password everywhere


@pytest.mark.parametrize("doc,problem", [
    ([], "document:not_an_object"),
    ({"bogus": 1}, "bogus:unknown_field"),
    ({"format": "other/1"}, "format:unsupported"),
    ({"wifi": dict(WIFI)}, "country:required_iso_3166_alpha2"),
    ({"hotspot": dict(HOTSPOT), "country": "de"}, "country:required_iso_3166_alpha2"),
    ({"country": "00"}, "country:required_iso_3166_alpha2"),
    ({"country": "DE", "wifi": {"ssid": "", "psk": "12345678"}}, "wifi.ssid:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x" * 33, "psk": "12345678"}}, "wifi.ssid:invalid"),
    ({"country": "DE", "wifi": {"ssid": "a\x00b", "psk": "12345678"}}, "wifi.ssid:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x", "psk": "1234567"}}, "wifi.psk:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x", "psk": "ü" * 10}}, "wifi.psk:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x", "psk": "g" * 64}}, "wifi.psk:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x", "psk": "12345678", "hidden": "yes"}}, "wifi.hidden:invalid"),
    ({"country": "DE", "wifi": {"ssid": "x", "psk": "12345678", "eap": 1}}, "wifi.eap:unknown_field"),
    ({"country": "DE", "wifi": "Home"}, "wifi:not_an_object"),
    ({"country": "DE", "hotspot": {"ssid": "x", "psk": "short-11ch"}}, "hotspot.psk:required_12_to_63_printable_ascii"),
    ({"country": "DE", "hotspot": {"ssid": "x"}}, "hotspot.psk:required_12_to_63_printable_ascii"),
    ({"country": "DE", "hotspot": {"ssid": "x", "psk": "p" * 12, "open": True}}, "hotspot.open:unknown_field"),
    ({"country": "DE", "hotspot": []}, "hotspot:not_an_object"),
    ({"country": "DE", "hotspot": {"ssid": "", "psk": "p" * 12}}, "hotspot.ssid:invalid"),
    ({"country": "DE", "wifi": dict(WIFI), "hotspot": {"ssid": "Home", "psk": "p" * 12}}, "hotspot.ssid:same_as_wifi"),
    ({"ethernet": []}, "ethernet:not_an_object"),
    ({"ethernet": {"ipv5": {}}}, "ethernet.ipv5:unknown_field"),
    ({"ethernet": {"ipv4": "dhcp"}}, "ethernet.ipv4:not_an_object"),
    ({"ethernet": {"ipv6": "auto"}}, "ethernet.ipv6:not_an_object"),
    ({"ethernet": {"ipv4": {"method": "bootp"}}}, "ethernet.ipv4.method:invalid"),
    ({"ethernet": {"ipv6": {"method": "dhcp"}}}, "ethernet.ipv6.method:invalid"),
    ({"ethernet": {"ipv4": {"method": "dhcp", "mtu": 1}}}, "ethernet.ipv4.mtu:unknown_field"),
    ({"ethernet": {"ipv6": {"method": "auto", "mtu": 1}}}, "ethernet.ipv6.mtu:unknown_field"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20"}}}, "ethernet.ipv4.address:invalid"),     # prefix required
    ({"ethernet": {"ipv4": {"method": "static", "address": "nonsense/24"}}}, "ethernet.ipv4.address:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.0/24"}}}, "ethernet.ipv4.address:invalid"),   # network address
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.255/24"}}}, "ethernet.ipv4.address:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "127.0.0.2/8"}}}, "ethernet.ipv4.address:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "169.254.3.4/16"}}}, "ethernet.ipv4.address:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.2/31"}}}, "ethernet.ipv4.address:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "10.42.0.7/24"}}}, "ethernet.ipv4.address:conflicts_with_hotspot_network"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "10.0.0.7/8"}}}, "ethernet.ipv4.address:conflicts_with_hotspot_network"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "192.168.2.1"}}}, "ethernet.ipv4.gateway:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.20"}}}, "ethernet.ipv4.gateway:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "x"}}}, "ethernet.ipv4.gateway:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "dns": "1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4"}}},
     "ethernet.ipv4.dns:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "dns": ["2001:db8::1"]}}}, "ethernet.ipv4.dns:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "dns": ["dns.example"]}}}, "ethernet.ipv4.dns:invalid"),
    ({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "dns": 5}}}, "ethernet.ipv4.dns:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5"}}}, "ethernet.ipv6.address:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "fe80::5/64"}}}, "ethernet.ipv6.address:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "nonsense/64"}}}, "ethernet.ipv6.address:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5/8"}}}, "ethernet.ipv6.address:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5/64", "gateway": "ff02::1"}}}, "ethernet.ipv6.gateway:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5/64", "gateway": "2001:db8::5"}}}, "ethernet.ipv6.gateway:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5/64", "dns": ["192.168.1.1"]}}}, "ethernet.ipv6.dns:invalid"),
    ({"ethernet": {"ipv6": {"method": "static", "address": "2001:db8::5/64", "dns": ["::"]}}}, "ethernet.ipv6.dns:invalid"),
])
def test_invalid_documents_report_field_and_reason_only(doc, problem):
    cfg, problems = N.check(doc)
    assert problem in problems, problems
    with pytest.raises(N.NetConfigError) as e:
        N.validate(doc)
    assert not [v for v in leaves(doc) if len(v) >= 4 and v in " ".join(e.value.problems)]      # values never appear in problems


def leaves(x):
    if isinstance(x, dict):
        return [s for v in x.values() for s in leaves(v)]
    if isinstance(x, list):
        return [s for v in x for s in leaves(v)]
    return [x] if isinstance(x, str) else []


def test_static_configuration_is_normalised():
    cfg = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1",
                                            "dns": "192.168.1.1, 9.9.9.9 192.168.1.1"},
                                   "ipv6": {"method": "static", "address": "2001:DB8::20/64", "gateway": "FE80::1", "dns": ["2001:db8::53"]}}})
    assert cfg["ethernet"]["ipv4"] == {"method": "static", "address": "192.168.1.20/24", "gateway": "192.168.1.1",
                                       "dns": ["192.168.1.1", "9.9.9.9"]}
    assert cfg["ethernet"]["ipv6"] == {"method": "static", "address": "2001:db8::20/64", "gateway": "fe80::1", "dns": ["2001:db8::53"]}
    no_gw = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.1.20/24", "gateway": "", "dns": ""},
                                     "ipv6": {"method": "off"}}})
    assert no_gw["ethernet"]["ipv4"]["gateway"] == "" and no_gw["ethernet"]["ipv4"]["dns"] == [] and no_gw["ethernet"]["ipv6"] == {"method": "off"}
    assert N.validate({"ethernet": {"ipv4": {"method": "dhcp", "address": "ignored/24"}}})["ethernet"]["ipv4"] == {"method": "dhcp"}


def test_wifi_with_hex_psk_hidden_flag_and_own_ip_settings():
    cfg = N.validate({"country": "AT", "wifi": {"ssid": "Wohnzimmer ☕", "psk": "a" * 64, "hidden": True,
                                                "ipv4": {"method": "static", "address": "192.168.5.9/24"}, "ipv6": {"method": "off"}}})
    assert cfg["wifi"]["hidden"] is True and cfg["wifi"]["ipv4"]["address"] == "192.168.5.9/24" and cfg["wifi"]["ipv6"] == {"method": "off"}


def test_redact_and_secrets():
    cfg = N.validate({"country": "DE", "wifi": dict(WIFI), "hotspot": dict(HOTSPOT)})
    red = N.redact(cfg)
    text = json.dumps(red)
    assert WIFI["psk"] not in text and HOTSPOT["psk"] not in text
    assert red["wifi"]["password_set"] is True and red["hotspot"]["password_set"] is True and red["wifi"]["ssid"] == "Home"
    assert cfg["wifi"]["psk"] == WIFI["psk"]                                       # the original is untouched
    assert sorted(N.secrets_of(cfg)) == sorted([WIFI["psk"], HOTSPOT["psk"]])
    assert N.secrets_of(N.defaults()) == [] and N.redact(N.defaults())["wifi"] is None


def test_changed_sections_and_reset():
    a = N.validate({"country": "DE", "wifi": dict(WIFI), "hotspot": dict(HOTSPOT)})
    b = json.loads(json.dumps(a)); b["wifi"]["ipv4"] = {"method": "static", "address": "192.168.1.9/24"}
    assert N.changed_sections(a, N.validate(b)) == {"wifi"}
    assert N.changed_sections(a, a) == set()
    assert N.changed_sections(N.defaults(), a) == {"country", "wifi", "hotspot"}
    c = json.loads(json.dumps(a)); c["ethernet"]["ipv4"] = {"method": "static", "address": "192.168.1.9/24"}
    r = N.reset_network(N.validate(c))
    assert r["wifi"] is None and r["hotspot"] == HOTSPOT and r["country"] == "DE" and r["ethernet"] == N.default_ip()
    assert N.validate(r) == r
    assert N.reset_network(N.defaults()) == N.defaults()


def test_write_private_and_load_round_trip(tmp_path):
    p = str(tmp_path / N.FILE_NAME)
    assert N.load(p) == N.defaults() and N.parse(None) == N.defaults()
    cfg = N.validate({"country": "DE", "wifi": dict(WIFI)})
    raw = N.write_private(p, cfg)
    assert os.stat(p).st_mode & 0o777 == 0o600 and N.load(p) == cfg and N.parse(raw) == cfg
    assert N.digest(raw) == N.digest(open(p, "rb").read()) and len(N.digest(raw)) == 64
    with pytest.raises(N.NetConfigError):
        N.write_private(p, {"wifi": dict(WIFI)})                                      # invalid: nothing written
    assert N.load(p) == cfg and not [f for f in os.listdir(tmp_path) if ".tmp" in f]
    open(p, "wb").write(b"{not json")
    with pytest.raises(N.NetConfigError) as e:
        N.load(p)
    assert e.value.problems == ["document:not_json"]
    open(p, "wb").write(b" " * (N.MAX_BYTES + 1))
    with pytest.raises(N.NetConfigError):
        N.load(p)


def test_write_private_cleans_up_when_the_write_fails(tmp_path, monkeypatch):
    p = str(tmp_path / N.FILE_NAME)
    monkeypatch.setattr(N.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        N.write_private(p, N.defaults())
    assert os.listdir(tmp_path) == []


def test_read_strict_rejects_symlinks_foreign_owner_open_permissions_and_huge_files(tmp_path):
    p = str(tmp_path / "network.json")
    assert N.read_strict(p) is None
    N.write_private(p, N.defaults())
    assert N.parse(N.read_strict(p, owner_uid=os.getuid())) == N.defaults()
    if os.getuid() == 0:                                                              # root-owned files are always accepted
        os.chown(p, 4242, 4242)
        assert N.read_strict(p, owner_uid=4242) is not None
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(p, owner_uid=os.getuid() + 12345)
    assert e.value.problems == ["file:wrong_owner"]
    os.chmod(p, 0o644)
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(p)
    assert e.value.problems == ["file:permissions_too_open"]
    os.chmod(p, 0o600)
    link = str(tmp_path / "link.json"); os.symlink(p, link)
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(link)
    assert e.value.problems == ["file:unreadable"]                                    # O_NOFOLLOW refuses the symlink
    d = str(tmp_path / "dir.json"); os.mkdir(d, 0o700)
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(d)
    assert e.value.problems == ["file:not_a_regular_file"]
    big = str(tmp_path / "big.json"); fd = os.open(big, os.O_WRONLY | os.O_CREAT, 0o600); os.write(fd, b" " * (N.MAX_BYTES + 1)); os.close(fd)
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(big)
    assert e.value.problems == ["file:too_large"]


def test_read_strict_stops_reading_a_file_that_grows(tmp_path, monkeypatch):
    p = str(tmp_path / "network.json"); N.write_private(p, N.defaults())
    monkeypatch.setattr(N.os, "read", lambda fd, n: b"x" * 65536)                      # a file growing while it is read
    with pytest.raises(N.NetConfigError) as e:
        N.read_strict(p)
    assert e.value.problems == ["file:too_large"]
