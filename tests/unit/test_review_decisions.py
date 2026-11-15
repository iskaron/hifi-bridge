"""UNIT tests that pin the owner decisions of the phase-1 review (docs/DECISIONS.md #15-#18).
If one of these fails, a product decision was changed by code: update docs/DECISIONS.md first."""
import inspect

from fakes import PLAYER, Rig

from btbridge.app import RuntimeConfig
from btbridge.core.controller import Config
from btbridge.lms import client as lms_client


def test_decision_15_default_pause_strategy_is_drop_restart():
    assert Config(target_player="x").pause_strategy == "drop_restart"


def test_decision_17_icy_metadata_is_off_by_default():
    assert RuntimeConfig().icy_metadata is False


def test_decision_16_bridge_never_issues_power_off():
    """A target woken by the bridge stays on after pause-grace expiry, disconnect and audio toggle."""
    for ending in ("grace", "disconnect", "audio_off", "target_change"):
        r = Rig(); r.lms.players[PLAYER]["power"] = False; r.connect_and_play(); r.run(3)
        assert r.lms.players[PLAYER]["power"] is True
        if ending == "grace":
            r.ctl.on_media(False); r.run(40)
        elif ending == "disconnect":
            r.ctl.on_disconnected("phone")
        elif ending == "audio_off":
            r.ctl.set_audio_allowed(False)
        else:
            r.ctl.set_target("02:00:00:00:00:99")
        r.run(2)
        assert r.lms.players[PLAYER]["power"] is True, ending
        assert all("power" not in c[0] or c[0] == "power_on" for c in r.lms.commands), ending


def test_decision_16_lms_client_has_no_power_off_path():
    """Static guard: the only power command the client can build is 'power 1'."""
    src = inspect.getsource(lms_client)
    assert '"power", 0' not in src and "power_off" not in src and "'power', 0" not in src
    assert [n for n in dir(lms_client.LmsClient) if "power" in n] == ["power_on"]
