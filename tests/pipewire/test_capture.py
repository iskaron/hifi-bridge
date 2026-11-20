"""Verify the bridge's PipeWire capture command lines against REAL PipeWire tools (see conftest.py for the label)."""
import subprocess
import time

import pytest
from conftest import RESULTS
from pcm_probe import analyse

from btbridge.adapters.bluez.pcm import PipeWireCapture, parec_argv, record_argv, source_present

CAPTURE_S = 5.0


def capture_with(argv, seconds=CAPTURE_S) -> bytes:
    """Use the bridge's own supervisor (real code path), collect what on_pcm delivers."""
    got = []
    cap = PipeWireCapture(lambda addr: argv, retry_s=0.2)
    cap.start("AA:BB:CC:DD:EE:01", got.append)
    time.sleep(seconds)
    cap.stop()
    return b"".join(got)


def pwcat_supports_raw() -> bool:
    return "--raw" in subprocess.run(["pw-cat", "--help"], capture_output=True, text=True).stdout


def record(pw, name, res, argv):
    RESULTS["_version"] = pw["version"]
    RESULTS[name] = {"argv": argv, **res, "label": "INTEGRATION real PipeWire tools + SIMULATED source node (module-pipe-source); not BlueZ"}


def test_parec_raw_is_headerless_and_bit_exact(pw, feeder):
    argv = parec_argv(pw["node"])
    res = analyse(capture_with(argv)); record(pw, "parec", res, argv)
    assert res["headerless"] and res["bit_exact"], res
    assert res["payload_frames"] > 44100 * (CAPTURE_S - 2), res


def test_pw_cat_dash_output_is_not_raw_pcm_on_every_version(pw, feeder):
    """Documents what `pw-cat --record ... -` WITHOUT --raw writes (finding: a Sun-AU stream on PipeWire 1.4, see docs)."""
    argv = record_argv(pw["node"], raw=False)
    res = analyse(capture_with(argv)); record(pw, "pw_cat_no_raw", res, argv)
    assert res["frames"] > 0
    if pwcat_supports_raw():                 # PipeWire >= 1.x: '-' is a sound-file stream (AU), NOT raw PCM -> bridge must pass --raw
        assert res["header"] is not None and not res["bit_exact"], res
    else:                                    # 0.3.65: '-' is raw PCM
        assert res["bit_exact"], res


@pytest.mark.skipif(not pwcat_supports_raw(), reason="this pw-cat has no --raw option")
def test_pw_cat_with_raw_is_headerless_and_bit_exact(pw, feeder):
    argv = record_argv(pw["node"], raw=True)
    res = analyse(capture_with(argv)); record(pw, "pw_cat_raw", res, argv)
    assert res["headerless"] and res["bit_exact"], res


def mic_frames(data: bytes, value: int) -> int:
    import struct
    n = len(data) // 4
    fr = struct.unpack("<%dh" % (2 * n), data[:4 * n])
    return sum(1 for k in range(n) if fr[2 * k] == value and fr[2 * k + 1] == value)


MISSING = "bluez_input.00_00_00_00_00_00"


@pytest.mark.parametrize("tool", ["parec", "pw-cat"])
def test_without_no_fallback_a_missing_node_silently_captures_the_default_source(pw, feeder, tool):
    """Documents the PRIVACY RISK the bridge guards against: target is only a hint; PipeWire falls back to the default source."""
    argv = (["parec", f"--device={MISSING}", "--format=s16le", "--rate=44100", "--channels=2", "--raw"] if tool == "parec" else
            ["pw-cat", "--record", "--target", MISSING, "--rate", "44100", "--channels", "2", "--format", "s16", "-"])
    data = capture_with(argv, 3.0)
    RESULTS[f"risk_fallback_{tool}"] = {"argv": argv, "bytes": len(data), "mic_frames": mic_frames(data, pw["mic_value"])}
    assert mic_frames(data, pw["mic_value"]) > 10000        # it really captured the 'microphone'


@pytest.mark.parametrize("which", ["parec", "pw-cat"])
def test_bridge_command_lines_never_fall_back_to_another_source(pw, feeder, which):
    argv = parec_argv(MISSING) if which == "parec" else record_argv(MISSING, raw=pwcat_supports_raw())
    got = []
    cap = PipeWireCapture(lambda a: argv, retry_s=0.2, stall_s=1.0); cap.start("x", got.append); time.sleep(3.0); cap.stop()
    data = b"".join(got)
    RESULTS[f"no_fallback_{which}"] = {"argv": argv, "bytes": len(data), "mic_frames": mic_frames(data, pw["mic_value"]), "starts": cap.starts}
    assert mic_frames(data, pw["mic_value"]) == 0 and len(data) == 0
    assert cap.starts >= 2                                   # child exited or watchdog fired -> supervisor keeps retrying


def test_node_presence_check(pw):
    assert source_present(pw["node"]) is True
    assert source_present(MISSING) is False


def test_node_disappearing_mid_capture_does_not_move_to_the_microphone(pw, feeder):
    """Phone disconnects -> its node vanishes. With dont-reconnect the stream must not be moved to the default source."""
    import subprocess as sp
    from conftest import NODE, bring_up_node, reload_node
    got = []
    cap = PipeWireCapture(lambda a: parec_argv(NODE), retry_s=0.2, stall_s=1.0); cap.start("x", got.append)
    time.sleep(2.0)
    before = len(b"".join(got))
    from conftest import vanish_node
    vanish_node(NODE, pw["module"])
    time.sleep(3.0)
    cap.stop()
    data = b"".join(got)
    RESULTS["node_vanishes"] = {"bytes_before": before, "bytes_total": len(data), "mic_frames": mic_frames(data, pw["mic_value"])}
    reload_node(pw, unload=False)                             # restore for later tests: node + keep-alive consumer,
    RESULTS["_stand_in_reloads"] = RESULTS.get("_stand_in_reloads", 0) + bring_up_node(pw)   # and real frames before the next test
    assert before > 44100 * 4 and mic_frames(data, pw["mic_value"]) == 0


def test_automatic_tool_choice_is_bit_exact_and_never_falls_back(pw, feeder):
    """The tool the appliance would pick on THIS PipeWire version (choose_capture) must be bit-exact and must not capture the microphone."""
    from btbridge.adapters.bluez.pcm import choose_capture
    name, argv_for = choose_capture()
    res = analyse(capture_with(argv_for(pw["node"])))
    got = []
    cap = PipeWireCapture(lambda a: argv_for(MISSING), retry_s=0.2, stall_s=1.0); cap.start("x", got.append); time.sleep(3.0); cap.stop()
    data = b"".join(got)
    RESULTS["auto_choice"] = {"tool": name, "bit_exact": res["bit_exact"], "missing_node_bytes": len(data),
                              "missing_node_mic_frames": mic_frames(data, pw["mic_value"])}
    assert res["bit_exact"], (name, res)
    assert mic_frames(data, pw["mic_value"]) == 0 and len(data) == 0, name
