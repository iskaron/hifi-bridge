"""UNIT (post-build.sh on a fake target tree, btbridge-os): the image pieces of update management (docs/OTA.md section 5) - the health
check and the restart path are enabled, upstream's unconditional rauc-mark-good is masked, the bridge runs with --updates rauc and may
write only the restart request outside its state directory, and the D-Bus policy is well-formed. The policy's EFFECT is tested with a
real dbus-daemon and the real RAUC service in tests/ota (scripts/run_ota.sh)."""
import os
import xml.etree.ElementTree as ET

from oshelpers import BOARD, REPO, make_ca
from test_update_trust import post_build


def test_post_build_enables_the_health_check_and_masks_upstream_mark_good(tmp_path, tools):
    dev = make_ca(tmp_path, "dev")
    r, target = post_build(tmp_path, "OWN", own=dev["ca"])
    assert r.returncode == 0, r.stderr
    wants = target / "etc/systemd/system/multi-user.target.wants"
    assert os.readlink(wants / "hifi-bridge-health.service") == "/usr/lib/systemd/system/hifi-bridge-health.service"
    assert os.readlink(wants / "hifi-bridge-reboot.path") == "/usr/lib/systemd/system/hifi-bridge-reboot.path"
    assert os.readlink(target / "etc/systemd/system/rauc-mark-good.service") == "/dev/null"


def test_units_and_tmpfiles_fit_together():
    units = os.path.join(BOARD, "overlay", "usr", "lib", "systemd", "system")
    health = open(os.path.join(units, "hifi-bridge-health.service")).read()
    assert "User=hifi-bridge" in health and "FailureAction=reboot" in health and "btbridge.ota.health" in health
    assert "CapabilityBoundingSet=\n" in health and "IPAddressAllow=localhost" in health
    path = open(os.path.join(units, "hifi-bridge-reboot.path")).read()
    assert "PathExists=/run/hifi-bridge-ota/reboot-request" in path
    reboot = open(os.path.join(units, "hifi-bridge-reboot.service")).read()
    assert "rm -f /run/hifi-bridge-ota/reboot-request" in reboot and "systemctl --no-block reboot" in reboot
    tmpfiles = open(os.path.join(BOARD, "overlay", "usr", "lib", "tmpfiles.d", "hifi-bridge-ota.conf")).read()
    assert "d /run/hifi-bridge-ota 0750 hifi-bridge hifi-bridge -" in tmpfiles
    bridge = open(os.path.join(REPO, "packaging", "systemd", "hifi-bridge.service")).read()
    assert "--updates rauc" in bridge and "ReadWritePaths=-/run/hifi-bridge-ota" in bridge
    from btbridge.ota.manager import RebootRequest
    assert RebootRequest().path == "/run/hifi-bridge-ota/reboot-request"


def test_dbus_policy_is_well_formed_and_closes_the_default():
    p = os.path.join(BOARD, "overlay", "etc", "dbus-1", "system.d", "de.pengutronix.rauc-hifi-bridge.conf")
    root = ET.parse(p).getroot()
    pol = {(x.get("context") or "user:" + x.get("user")): x for x in root.findall("policy")}
    assert [d.attrib for d in pol["default"]] == [{"send_destination": "de.pengutronix.rauc"}] and pol["default"][0].tag == "deny"
    assert {a.get("send_interface") for a in pol["user:hifi-bridge"]} == {"de.pengutronix.rauc.Installer", "org.freedesktop.DBus.Properties",
                                                                          "org.freedesktop.DBus.Introspectable"}
    assert all(a.tag == "allow" for a in pol["user:hifi-bridge"]) and "Set" not in {a.get("send_member") for a in pol["user:hifi-bridge"]}
