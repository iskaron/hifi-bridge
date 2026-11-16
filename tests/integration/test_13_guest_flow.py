"""INTEGRATION: the complete guest experience against the REAL LMS/Squeezelite lab, driven like the real product: the owner uses the WebUI
(HTTP), the guest only uses the (SIMULATED) phone - pair inside the window, press Play, hear music, pause, leave. The guest never touches
Lyrion or Home Assistant. Real Bluetooth pairing/AVRCP remain hardware items (plan B1/C1)."""
import json
import time

import pytest

from btbridge.service import ApplianceService
from btbridge.webui.server import WebUI
from btkit import analyze as A
from btkit.lab import my_ip, wait_for
from btkit.lms_rpc import PLAYERS
from btkit.sim_a2dp import SimulatedA2dpSource
from test_webui import PW, Client  # noqa: E402

pytestmark = pytest.mark.integration


def test_guest_pairs_in_the_window_presses_play_and_hears_music(lab, tmp_path):
    guest = SimulatedA2dpSource(device="AA:BB:CC:00:00:42", paired=False)
    svc = ApplianceService(str(tmp_path / "data"), source=guest, stream_port=8095, advertise_host=my_ip(), echo=None, auth_iterations=1000)
    svc.start()
    ui = WebUI(svc, "127.0.0.1", 0); port = ui.start()
    owner = Client(port)
    try:
        code = open(svc.auth.code_file).read().strip()
        owner.post("/setup", {"code": code, "password": PW, "confirm": PW}, csrf=False)
        for path, form in (("/setup/bluetooth", {"device_name": "Wohnzimmer"}), ("/setup/lyrion", {"lms_host": "lms", "lms_port": "9000"}),
                           ("/setup/target", {"target_player": PLAYERS["player1"]}), ("/setup/finish", {})):
            owner.post(path, form)
        dash = owner.get("/").body
        assert "Wohnzimmer" in dash and "Allow pairing for 5 minutes" in dash               # the owner sees what to do and the name to tell
        # 1. the guest tries before the window is open -> refused, nothing happens at Lyrion
        assert guest.connect() is False and guest.refused == 1 and lab.mode() == "stop"
        # 2. the owner opens the pairing window on the dashboard
        owner.post("/pairing/open")
        assert json.loads(owner.get("/api/status").body)["bluetooth"]["pairing_open"] is True
        # 3. the guest pairs + connects: no Lyrion action yet (connecting alone never plays)
        assert guest.connect() is True
        time.sleep(1.0)
        assert lab.mode() == "stop" and "AA:BB:CC:00:00:42" in owner.get("/pairing").body
        # 4. the guest presses Play in any app -> music on the selected Lyrion player
        t_play = time.time(); guest.play()
        assert wait_for(lambda: lab.mode() == "play" and lab.url() == svc.runtime.controller.session.url, 8)
        time.sleep(4)
        cap = lab.capture(since=t_play)
        assert A.audible_fraction(cap, t_play + 2.0, time.time() - 0.3) > 0.9                # audio really reached the player
        assert json.loads(owner.get("/api/status").body)["headline"] == "playing"
        # 5. pause on the phone -> silence quickly (drop_restart), 6. the guest leaves -> released
        guest.pause()
        assert wait_for(lambda: lab.mode() == "stop", 3)
        guest.disconnect()
        assert wait_for(lambda: json.loads(owner.get("/api/status").body)["bluetooth"]["connected_device"] is None, 5)
        assert {c[0] for c in svc.runtime.lms.commands_sent} <= {"play", "release", "power_on", "volume_delta"}
    finally:
        ui.stop(); svc.stop()
