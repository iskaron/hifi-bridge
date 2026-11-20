"""UNIT (scripted BlueZ bus; hand-written from the BlueZ D-Bus documentation, NOT recorded from a phone or a Pi - label SIMULATED): the model's
less usual events - a device known only by its object path, a refused phone whose Disconnect call fails, a transport volume that is
garbage, objects vanishing while a phone is playing. The invariants: the active phone is only ever ended by its own events, and bad input
from the radio stack never reaches the controller as an exception. Coverage package 7b-3."""
import pytest
from fakes_bluez import addr, dev_path
from test_bluez_adapter import model

from btbridge.adapters.bluez.model import device_id


def test_a_device_without_an_address_property_is_identified_by_its_object_path():
    assert device_id({}, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01") == "AA:BB:CC:DD:EE:01"
    assert device_id({"Address": "aa:bb:cc:dd:ee:02"}, "/org/bluez/hci0/dev_X") == "AA:BB:CC:DD:EE:02"
    assert device_id({}, "/org/bluez/hci0/something_else") == "/org/bluez/hci0/something_else"    # nothing to derive: the path itself


def test_a_refused_phone_whose_disconnect_call_fails_is_still_ignored_and_logged():
    logs = []
    m, bus, rec = model()
    m.log = logs.append
    bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_device(2)
    bus.fail_calls = True                                                      # bluetoothd refuses the Disconnect call as well
    bus.connect(2)
    assert any("Disconnect failed" in line for line in logs) and any("refusing" in line for line in logs)
    assert m.active == dev_path(1) and dev_path(2) in m.ignored and rec.events == [("connect", addr(1))]


@pytest.mark.parametrize("raw,expected", [(64, 64), (999, 127), (-5, 0), ("80", 80)])
def test_the_transport_volume_is_clamped_to_the_avrcp_range(raw, expected):
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "active")
    bus.transport(1, Volume=raw)
    assert ("volume", expected) in rec.events


def test_a_volume_value_that_is_not_a_number_is_ignored_and_the_state_change_still_arrives():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "idle")
    bus.transport(1, Volume="loud", State="active")
    assert [e for e in rec.events if e[0] == "volume"] == [] and ("transport", "active") in rec.events


def test_the_active_phones_transport_and_player_vanishing_end_its_audio_but_not_its_connection():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "active"); bus.add_player(1, "playing")
    n = len(rec.events)
    bus.removed(f"{dev_path(1)}/fd0", "org.bluez.MediaTransport1")
    bus.removed(f"{dev_path(1)}/player0", "org.bluez.MediaPlayer1")
    assert rec.events[n:] == [("transport", "idle"), ("avrcp", None)]
    assert m.active == dev_path(1) and "disconnect" not in rec.kinds()


def test_objects_of_another_device_vanishing_are_not_the_active_phones_business():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_device(2)
    bus.add_transport(2, "active"); bus.add_player(2, "playing")               # a second phone's objects exist but it never connected
    n = len(rec.events)
    bus.removed(f"{dev_path(2)}/fd0", "org.bluez.MediaTransport1")
    bus.removed(f"{dev_path(2)}/player0", "org.bluez.MediaPlayer1")
    assert rec.events[n:] == []


def test_bluetoothd_going_away_ends_the_active_phones_session_like_a_disconnect():
    from btbridge.adapters.bluez.bus import ServiceVanished
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True); bus.add_transport(1, "active")
    bus.emit(ServiceVanished())
    assert rec.events[-1] == ("disconnect", addr(1)) and m.active is None and m.devices == {}


def test_a_connected_phone_that_is_removed_from_bluez_is_disconnected_first():
    m, bus, rec = model(); bus.handler = m.handle
    bus.add_device(1, connected=True)
    bus.removed(dev_path(1), "org.bluez.Device1")                               # unpaired on the phone
    assert rec.events[-1] == ("disconnect", addr(1)) and m.active is None and dev_path(1) not in m.devices
