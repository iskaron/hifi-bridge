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


POWER_OFF = __import__("re").compile(r"_(?:rpc|timed)\([^)]*[\"']power[\"']\s*,\s*(?:0|[\"']0[\"']|False)")


def test_decision_16_lms_client_has_no_power_off_path():
    """Static guard: no JSON-RPC call in the client sends 'power 0'; the only power method is power_on (reading the power state is fine)."""
    src = inspect.getsource(lms_client)
    assert not POWER_OFF.search(src) and "power_off" not in src
    assert [n for n in dir(lms_client.LmsClient) if "power" in n] == ["power_on"]


def test_power_off_guard_itself_detects_a_power_off_call():
    for bad in ('self._timed(player, "power", 0)', "self._rpc(p, 'power', '0')", 'self._rpc(p, "power",False)'):
        assert POWER_OFF.search(bad), bad
    assert not POWER_OFF.search('bool(int(p.get("power", 0)))') and not POWER_OFF.search('self._timed(player, "power", 1)')
