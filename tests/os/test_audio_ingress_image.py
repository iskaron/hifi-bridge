"""STATIC: the appliance image carries what the phone's audio needs to reach the bridge (real-hardware finding G1, 2026-11-24):
PipeWire's BlueZ plugin, pw-cat + pw-dump, the WirePlumber rule `bluez5.media-source-role = input` and the 44.1 kHz clock - the same
files the test Pi gets from hwkit/pi-setup. Each image check fails on a planted defect. No image is booted (hardware gate G1/G2)."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "os", "tools"))
import image_check  # noqa: E402

OVERLAY = os.path.join(ROOT, "os", "br2-external", "board", "hifi-bridge", "overlay")
KIT = os.path.join(ROOT, "hwkit", "pi-setup")
PAIRS = [("etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf", "50-hifi-bridge-a2dp-input.conf"),
         ("etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf", "10-hifi-bridge-rates.conf")]


@pytest.mark.parametrize("image_path,kit_name", PAIRS)
def test_image_overlay_and_test_pi_use_the_same_pipewire_configuration(image_path, kit_name):
    with open(os.path.join(OVERLAY, image_path)) as a, open(os.path.join(KIT, kit_name)) as b:
        assert a.read() == b.read()


def tree(tmp_path, drop=(), role_text=None):
    root = tmp_path / "rootfs"
    files = {
        "usr/lib/spa-0.2/bluez5/libspa-bluez5.so": "\x7fELF",
        "usr/bin/pw-cat": "#!", "usr/bin/pw-dump": "#!",
        "usr/share/wireplumber/wireplumber.conf": "mixin.systemwide-session = {\n  support.logind = disabled\n"
                                                  "  monitor.bluez.seat-monitoring = disabled\n}\n",
    }
    for image_path, kit_name in PAIRS:
        with open(os.path.join(OVERLAY, image_path)) as fh:
            files[image_path] = fh.read()
    if role_text is not None:
        files[PAIRS[0][0]] = role_text
    for rel, text in files.items():
        if rel in drop:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    r = image_check.Report()
    image_check.check_audio_ingress(r, str(root))
    return {c["id"]: c for c in r.checks}


def test_a_complete_tree_passes(tmp_path):
    res = tree(tmp_path)
    assert all(c["status"] in ("PASS", "INFO") for c in res.values()), res
    assert "yes" in res["audio.seat-monitoring"]["detail"]


@pytest.mark.parametrize("missing,check", [
    ("usr/lib/spa-0.2/bluez5/libspa-bluez5.so", "audio.bluez5-spa-plugin"),
    ("usr/bin/pw-dump", "audio.capture-tools"),
    ("usr/bin/pw-cat", "audio.capture-tools"),
    (PAIRS[0][0], "audio.a2dp-input-role"),
    (PAIRS[1][0], "audio.allowed-rates"),
])
def test_each_missing_piece_fails_its_check(tmp_path, missing, check):
    res = tree(tmp_path, drop=(missing,))
    assert res[check]["status"] == "FAIL", res[check]


def test_a_rule_with_the_playback_role_or_another_match_fails(tmp_path):
    playback = open(os.path.join(KIT, PAIRS[0][1])).read().replace('"input"', '"playback"')
    assert tree(tmp_path / "a", role_text=playback)["audio.a2dp-input-role"]["status"] == "FAIL"
    other = open(os.path.join(KIT, PAIRS[0][1])).read().replace("~bluez_input.*", "~alsa_input.*")
    assert tree(tmp_path / "b", role_text=other)["audio.a2dp-input-role"]["status"] == "FAIL"


def test_the_buildroot_configuration_builds_the_pieces():
    frag = open(os.path.join(ROOT, "os", "br2-external", "fragments", "common.fragment")).read()
    for opt in ("BR2_PACKAGE_PIPEWIRE=y", "BR2_PACKAGE_WIREPLUMBER=y", "BR2_PACKAGE_BLUEZ5_UTILS=y", "BR2_PACKAGE_SBC=y"):
        assert opt in frag, opt
