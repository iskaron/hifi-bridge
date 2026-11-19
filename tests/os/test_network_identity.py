"""STATIC (overlay files, nothing built or booted): the DHCP identity must not depend on /etc/machine-id. On the read-only root
/etc/machine-id is empty, so systemd uses a new transient id on every boot; networkd's default DUID is derived from it, which would
give the device a new DHCPv4 client id / DHCPv6 DUID - and possibly a new address - after every reboot (OPEN_QUESTIONS #77)."""
import configparser

from oshelpers import BOARD

DROPIN = f"{BOARD}/overlay/etc/systemd/networkd.conf.d/hifi-bridge.conf"


def test_dhcp_duid_is_derived_from_the_mac_not_the_transient_machine_id():
    c = configparser.ConfigParser()
    assert c.read(DROPIN), DROPIN
    for section in ("DHCPv4", "DHCPv6"):
        assert c.get(section, "DUIDType") == "link-layer", section
