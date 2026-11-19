"""STATIC (file content): OS integration of the network management - the committed base ruleset equals the renderer, the radio units
conflict with each other (systemd enforces client Wi-Fi XOR hotspot), the agent's privileges are bounded, and everything is enabled."""
import os
import re

from btbridge.net import netrender

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOARD = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge")
UNITS = os.path.join(BOARD, "overlay", "usr", "lib", "systemd", "system")


def read(*p):
    return open(os.path.join(*p), encoding="utf-8").read()


def unit(name):
    return read(UNITS, name)


def test_base_ruleset_is_the_rendered_lan_ruleset():
    base = read(BOARD, "overlay", "usr", "share", "hifi-bridge", "nftables-base.nft").splitlines()
    rendered = netrender.nftables(80, 443, 8090).splitlines()
    assert [x for x in base if not x.startswith("#")] == [x for x in rendered if not x.startswith("#")]


def test_radio_units_conflict_so_client_and_hotspot_never_run_together():
    assert re.search(r"^Conflicts=.*hifi-net-hostapd@%i\.service", unit("hifi-net-wpa@.service"), re.M)
    assert re.search(r"^Conflicts=.*hifi-net-wpa@%i\.service", unit("hifi-net-hostapd@.service"), re.M)
    assert re.search(r"^Conflicts=.*hifi-net-wpa@%i\.service", unit("hifi-net-dhcp@.service"), re.M)
    assert "BindsTo=hifi-net-hostapd@%i.service" in unit("hifi-net-dhcp@.service")
    for u in ("hifi-net-wpa@.service", "hifi-net-hostapd@.service", "hifi-net-dhcp@.service"):
        assert "/run/hifi-net/" in unit(u) and "NoNewPrivileges=yes" in unit(u) and "ProtectSystem=strict" in unit(u)


def test_agent_unit_privileges_are_bounded():
    u = unit("hifi-netd.service")
    caps = re.search(r"^CapabilityBoundingSet=(.*)$", u, re.M).group(1).split()
    assert sorted(caps) == ["CAP_CHOWN", "CAP_DAC_READ_SEARCH", "CAP_NET_ADMIN", "CAP_NET_RAW"]      # no SYS_ADMIN, no DAC_OVERRIDE
    assert "ReadOnlyPaths=/var/lib/hifi-bridge" in u and "ReadWritePaths=/run/hifi-net /run/systemd/network" in u
    assert "RuntimeDirectoryPreserve=yes" in u and "Type=notify" in u and "--bridge-user hifi-bridge" in u
    assert "Before=avahi-daemon.service hifi-bridge.service" in u


def test_base_firewall_comes_before_the_network():
    u = unit("hifi-firewall.service")
    assert "Before=network-pre.target" in u and "Wants=network-pre.target" in u and "DefaultDependencies=no" in u
    assert "ExecStart=/usr/sbin/nft -f /usr/share/hifi-bridge/nftables-base.nft" in u


def test_units_packages_and_mounts_are_wired():
    pb = read(BOARD, "post-build.sh")
    assert re.search(r"^for u in .*hifi-firewall\.service hifi-netd\.service; do", pb, re.M)
    assert "/data/timesync /var/lib/systemd/timesync none bind" in pb
    assert "/data/timesync" in unit("hifi-bridge-data.service")
    frag = read(REPO, "os", "br2-external", "fragments", "common.fragment")
    for line in ("BR2_PACKAGE_NFTABLES=y", "BR2_PACKAGE_WPA_SUPPLICANT_CTRL_IFACE=y", "BR2_PACKAGE_SYSTEMD_NETWORKD=y",
                 "BR2_PACKAGE_SYSTEMD_TIMESYNCD=y", "BR2_PACKAGE_SYSTEMD_HOSTNAMED=y", "BR2_PACKAGE_HOSTAPD=y", "BR2_PACKAGE_DNSMASQ=y"):
        assert line in frag.splitlines(), line
    for b in ("rpi3_64", "rpi4_64", "rpizero2w_64"):
        d = read(REPO, "os", "br2-external", "configs", f"hifi_bridge_{b}_defconfig")
        assert "BR2_PACKAGE_NFTABLES=y" in d and "BR2_PACKAGE_WPA_SUPPLICANT_CTRL_IFACE=y" in d
    wait = read(UNITS, "systemd-networkd-wait-online.service.d", "hifi-bridge.conf")
    assert "--any --timeout=15" in wait
    avahi = read(BOARD, "overlay", "etc", "avahi", "avahi-daemon.conf")
    assert "publish-workstation=no" in avahi and "enable-reflector=no" in avahi and "host-name=" not in avahi
    assert "FallbackNTP=" in read(BOARD, "overlay", "etc", "systemd", "timesyncd.conf.d", "hifi-bridge.conf")


def test_snapshot_restore_runs_in_the_boot_step_before_the_network_agent():
    """Review N-4: hifi-netd reads network.json and local_hostname only at its start, so an owner-requested snapshot restore is applied
    by hifi-bridge-boot.service (bootfiles), which the agent is ordered after; the boot step must see rauc.slot= in /proc/cmdline."""
    netd = unit("hifi-netd.service")
    assert re.search(r"^After=.*\bhifi-bridge-boot\.service\b", netd, re.M)
    boot = read(REPO, "packaging", "systemd", "hifi-bridge-boot.service")
    assert re.search(r"^Before=.*\bhifi-bridge\.service\b", boot, re.M) and "ProtectProc=invisible" in boot
    assert not re.search(r"^ProcSubset=pid", boot, re.M)                # would hide /proc/cmdline
