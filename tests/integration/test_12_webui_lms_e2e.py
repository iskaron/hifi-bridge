"""INTEGRATION: owner WebUI over HTTP + appliance service + SIMULATED phone against the REAL LMS 9.1.2 / Squeezelite lab:
real UDP discovery in the wizard, real players and an existing sync group as targets, the test sound measured at the real player output,
and playback control from the dashboard. Not a browser test (see tests/browser) and not hardware."""
import json
import re
import time
import urllib.parse

import pytest

from btbridge.service import ApplianceService
from btbridge.webui.server import WebUI
from btkit import analyze as A
from btkit.lab import my_ip, wait_for
from btkit.lms_rpc import PLAYERS
from btkit.sim_a2dp import SimulatedA2dpSource
from test_webui import PW, Client  # noqa: E402  (tests/unit is on the pythonpath)

pytestmark = pytest.mark.integration


@pytest.fixture
def app(lab, tmp_path):
    sim = SimulatedA2dpSource()
    svc = ApplianceService(str(tmp_path / "data"), source=sim, stream_port=8094, advertise_host=my_ip(), echo=None, auth_iterations=1000)
    svc.start()
    ui = WebUI(svc, "127.0.0.1", 0); port = ui.start()
    yield type("App", (), dict(svc=svc, sim=sim, c=Client(port), lab=lab, code=open(svc.auth.code_file).read().strip()))
    ui.stop(); svc.stop()


def flash(c):
    return urllib.parse.unquote(c.cookies.get("bab_flash", ""))


def test_wizard_against_real_lms_and_test_sound_on_the_real_player(app, lab):
    lab.lms.req(PLAYERS["player2"], "sync", PLAYERS["player3"])             # an EXISTING group made by the 'owner' beforehand
    c = app.c
    c.post("/setup", {"code": app.code, "password": PW, "confirm": PW}, csrf=False)
    c.post("/setup/bluetooth", {"device_name": "Lab Bridge"})
    page = c.get("/setup/lyrion").body
    assert re.search(r'name="lms_host" value="[0-9.]+"', page), "real UDP discovery should offer the lab LMS"
    c.post("/setup/lyrion", {"lms_host": "lms", "lms_port": "9000"})
    assert "Lyrion 9.1.2" in flash(c) or "9.1" in flash(c)
    targets = c.get("/setup/target").body
    for name in ("lab-player1", "lab-player2", "lab-player3"):
        assert name in targets
    assert "Synchronised group" in targets and "lab-player2 + lab-player3" in targets.replace("lab-player3 + lab-player2", "lab-player2 + lab-player3")
    syncs_before = lab.lms.req("", "syncgroups", "?")
    c.post("/setup/target", {"target_player": PLAYERS["player1"]})
    assert app.svc.store.get("target_label") == "lab-player1"
    t0 = time.time()
    c.post("/diagnostics/test", {"back": "/setup/finish"})
    assert "Test sound started" in flash(c)
    time.sleep(6)
    cap = lab.capture(since=t0)
    audible = A.audible_fraction(cap, t0 + 2.0, t0 + 4.0)
    assert audible > 0.9, audible                                            # the 440 Hz tone really reached the real player
    assert wait_for(lambda: lab.mode() == "stop", 6)                         # and the diagnostic released only its own session
    assert lab.lms.req("", "syncgroups", "?") == syncs_before                # the wizard never touched groups
    c.post("/setup/finish")
    assert app.svc.store.get("setup_complete")


def test_dashboard_controls_real_playback_and_never_touches_foreign_music(app, lab, radio):
    c = app.c
    c.post("/setup", {"code": app.code, "password": PW, "confirm": PW}, csrf=False)
    for path, form in (("/setup/bluetooth", {"device_name": "Lab"}), ("/setup/lyrion", {"lms_host": "lms", "lms_port": "9000"}),
                       ("/setup/target", {"target_player": PLAYERS["player1"]}), ("/setup/finish", {})):
        c.post(path, form)
    app.sim.connect(); app.sim.play()
    assert wait_for(lambda: lab.mode() == "play" and lab.url() == app.svc.runtime.controller.session.url, 8)
    assert wait_for(lambda: json.loads(c.get("/api/status").body)["headline"] == "playing", 5)
    assert "Playing" in c.get("/").body
    c.post("/audio", {"value": "off"})
    assert wait_for(lambda: lab.mode() == "stop", 5)
    lab.lms.req(PLAYERS["player1"], "playlist", "play", radio.url, "Radio")
    assert wait_for(lambda: lab.url() == radio.url and lab.mode() == "play", 5)
    n = len(app.svc.runtime.lms.commands_sent)
    c.post("/diagnostics/test", {"back": "/"})
    assert "refused" in flash(c)
    c.post("/settings/target", {"target_player": PLAYERS["player2"]})        # target change while radio plays on player1
    c.post("/settings/playback", {"pause_strategy": "keepalive_silence", "stream_format": "flac", "override_lms": "on"})
    time.sleep(2)
    assert lab.url() == radio.url and lab.mode() == "play"                    # foreign music untouched by every owner action
    assert all(cmd[0] in ("play", "release", "power_on", "volume_delta") for cmd in app.svc.runtime.lms.commands_sent[n:])
    assert not any(cmd[1] == PLAYERS["player1"] for cmd in app.svc.runtime.lms.commands_sent[n:])
