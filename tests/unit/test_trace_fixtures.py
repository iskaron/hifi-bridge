"""UNIT: replay every committed trace fixture through the bridge's translator + fusion and require all operator expectations to hold.
Fixtures carry their own label: today only a SIMULATED example exists; REAL-HARDWARE traces from plan C1/C2 will be added here."""
import glob
import os

import pytest

from hwkit.replay import replay
from hwkit.trace import load

FIX = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "fixtures", "traces", "*.jsonl")))


@pytest.mark.parametrize("path", FIX, ids=[os.path.basename(p) for p in FIX])
def test_fixture_expectations_hold(path):
    records = load(path)
    assert records[0]["type"] == "Header" and records[0]["label"]
    res = replay(records)
    assert res["checks"] and res["all_checks_ok"], res["checks"]


def test_at_least_one_fixture_exists():
    assert FIX
