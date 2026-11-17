"""Shared helpers for the demo stack tests (run by scripts/test_demo.sh inside containers on the TEST project's internal network)."""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.request

BRIDGE = os.environ.get("BASE_URL", "http://hifi-bridge.local:28080")
CONSOLE = os.environ.get("CONSOLE_URL", "http://console:8100")
LMS = os.environ.get("LMS_URL", "http://lms:9000")
STATE_DIR = os.environ.get("DEMO_TEST_STATE", "/work/.lab/demo-test")
SHOTS = os.environ.get("SHOTS", "/work/reports/screenshots")
PASSWORD = "a good long demo passphrase"
ROOM_MAC = {"Wohnzimmer": "02:00:00:00:01:01", "Schlafzimmer": "02:00:00:00:01:02", "Arbeitszimmer": "02:00:00:00:01:03"}


def setup_code() -> str:
    text = open("/owner/owner-sheet.txt").read()
    return re.search(r"(?:Einrichtungscode|Setup code): (\S+)", text).group(1)


def http_json(url: str, body=None, timeout=15, host=None):
    h = {"Content-Type": "application/json"} if body is not None else {}
    if host:
        h["Host"] = host
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def console(path: str, body=None):
    return http_json(CONSOLE + path, body)


def phone(pid: str, action: str, **body):
    return console(f"/api/phones/{pid}/{action}", body)


def phone_state(pid: str) -> dict:
    return next(p for p in console("/api/state")["phones"] if p["id"] == pid)


def lms(player: str, *cmd):
    body = {"id": 1, "method": "slim.request", "params": [player, [str(c) for c in cmd]]}
    return http_json(LMS + "/jsonrpc.js", body).get("result") or {}


def lms_status(room: str) -> dict:
    st = lms(ROOM_MAC[room], "status", "-", 1, "tags:u")
    loop = st.get("playlist_loop") or [{}]
    st["url"] = loop[0].get("url", "")
    return st


def wait(cond, timeout=20.0, step=0.25, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = cond()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"timeout waiting for {what} (last: {last!r})")


def capture(room: str, seconds: float):
    """Frames (t_dac, pcm) from a room's virtual DAC, straight from the player's monitor tap."""
    from hifidemo import monitor as MON
    s = socket.create_connection((room.lower(), 9100), timeout=5)
    f = s.makefile("rb")
    f.readline()
    p, frames, end = MON.FrameParser(), [], time.time() + seconds
    while time.time() < end:
        frames += p.feed(f.read1(65536))
    s.close()
    return frames


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
