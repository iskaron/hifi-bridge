"""DEMO unit tests (demo image): the demo's 'Pi' wires the product to the SIMULATED network agent and the SIMULATED RAUC.
The bridge product itself is unchanged; what these tests prove is the wiring - that the options handed to ``btbridge.appliance`` point at
back-ends that exist and answer on the demo's private bus."""
import os

import pytest

from btkit.fake_bluez import PrivateBus
from hifidemo import node


@pytest.fixture
def bus():
    b = PrivateBus()
    yield b
    b.close()


def test_the_appliance_gets_the_simulated_network_agent_and_the_simulated_rauc(bus, monkeypatch, tmp_path):
    monkeypatch.setattr(node, "RUN", str(tmp_path / "run"))
    extra = node.sim_backend_options(bus)
    argv = node.appliance_argv(bus.address, extra)
    assert argv[argv.index("--net-agent") + 1] == str(tmp_path / "run" / "net" / "agent.sock")       # where btkit.sim_net binds it
    assert argv[argv.index("--updates") + 1] == "rauc" and "--update-trust" in argv
    assert argv[argv.index("--source") + 1] == "bluez" and argv.count("--dbus") >= 1                # the radio stack is still the real adapter
    assert os.path.isfile(argv[argv.index("--update-trust") + 1])                                    # the (SIMULATED) trust record exists


def test_the_simulated_rauc_answers_the_products_rauc_client_on_the_shared_bus(bus, monkeypatch, tmp_path):
    from btbridge.ota.rauc import RaucClient
    monkeypatch.setattr(node, "RUN", str(tmp_path / "run"))
    node.sim_backend_options(bus)
    props = RaucClient(bus=bus.address).properties()
    assert props["BootSlot"] == "A" and props["Operation"] == "idle"


def test_the_network_agent_is_started_before_the_bridge_and_runs_the_simulated_radio(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "DATA", str(tmp_path / "data")); monkeypatch.setattr(node, "RUN", str(tmp_path / "run"))
    argv = node.sim_net_argv()
    assert argv[1:3] == ["-m", "btkit.sim_net"]                                                      # the lab entry of the REAL agent
    assert argv[argv.index("--bridge-data") + 1] == str(tmp_path / "data" / "bridge")
    assert argv[argv.index("--run-dir") + 1] == str(tmp_path / "run" / "net")
