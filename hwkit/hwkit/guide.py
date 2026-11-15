"""Guided hardware session: walks the operator through the catalogue test by test, runs the automatic parts (environment, trace,
PipeWire snapshots, capture) and records the operator's verdict in results.json. All results are REAL-HARDWARE by construction, because
this runs on the test Pi with real phones; the kit refuses to store anything else (results.py)."""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable

from . import results as R

HERE = os.path.dirname(os.path.abspath(__file__))


def procedures() -> dict:
    with open(os.path.join(HERE, "procedures.json"), encoding="utf-8") as fh:
        return json.load(fh)["procedures"]


class Actions:
    """Default automatic actions (on the Pi). Tests replace them with fakes."""
    def __init__(self, address: str | None, mic: str | None, ask: Callable[[str], str], say: Callable[[str], None]):
        self.address, self.mic, self.ask, self.say = address, mic, ask, say

    def env(self, d):
        from .env import main
        main(os.path.join(d, "env.json")); return ["env.json"]

    def nodes(self, d):
        for name, argv in (("pw-dump.json", ["pw-dump"]), ("sources.txt", ["pactl", "list", "sources"])):
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout
            open(os.path.join(d, name), "w").write(out)
        return ["pw-dump.json", "sources.txt"]

    def trace(self, d, body: Callable[[Callable[[str], None]], None]):
        from btbridge.adapters.bluez.jeepney_bus import JeepneyBus
        from .trace import TraceRecorder
        rec = TraceRecorder(JeepneyBus(bus="SYSTEM"), os.path.join(d, "trace.jsonl"))
        rec.start()
        try:
            body(rec.marker)
        finally:
            rec.stop()
        return ["trace.jsonl"]

    def capture(self, d, seconds=60, mic=False):
        from .capture import record
        record(os.path.join(d, "capture"), self.address, seconds, mic_node=self.mic if mic else None)
        return ["capture/capture.json", "capture/phone.wav"] + (["capture/mic.wav"] if mic else [])


def run(results_file: str, ask: Callable[[str], str] = input, say: Callable[[str], None] = print, only: list[str] | None = None,
        actions: Actions | None = None) -> dict:
    base = os.path.dirname(os.path.abspath(results_file))
    doc = R.load(results_file)
    procs = procedures()
    act = actions or Actions(None, None, ask, say)
    done = {}
    for t in doc["tests"]:
        tid = t["id"]
        if (only and tid not in only) or (not only and t["status"] in ("PASS", "FAIL")):
            continue
        p = procs[tid]
        cat = next(c for c in R.catalog() if c["id"] == tid)
        say(f"\n=== {tid}: {cat['title']}\nPass criterion: {cat['criterion']}")
        for i, s in enumerate(p["steps"], 1):
            say(f"  {i}. {s}")
        choice = ask(f"{tid}: run now? [y]es / [s]kip / [q]uit: ").strip().lower()[:1]
        if choice == "q":
            break
        if choice != "y":
            continue
        d = os.path.join(base, "sessions", tid, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(d, exist_ok=True)
        evidence = []
        for a in p["auto"]:
            if a == "env":
                evidence += act.env(d)
            elif a == "nodes":
                ask("connect the phone and start music, then press Enter: ")
                evidence += act.nodes(d)
            elif a == "trace":
                def body(marker):
                    say("Trace running. Type markers (e.g. 'play', 'expect:media'); an empty line ends the trace.")
                    while True:
                        line = ask("marker> ").strip()
                        if not line:
                            return
                        marker(line)
                evidence += act.trace(d, body)
            elif a in ("capture", "capture_long", "capture_mic"):
                secs = {"capture": 60, "capture_long": 1800, "capture_mic": 300}[a]
                ask(f"start the test track on the phone, then press Enter (records {secs} s): ")
                evidence += act.capture(d, secs, mic=a == "capture_mic")
        status = ""
        while status not in R.STATUSES:
            status = ask("result [PASS/FAIL/PARTIAL/BLOCKED/NOT_RUN]: ").strip().upper()
        obs = ask("observations (one line): ").strip()
        device = ask("devices (phone/OS/app, board, player+firmware): ").strip()
        rel = [os.path.relpath(os.path.join(d, e), base) for e in evidence]
        R.set_result(results_file, tid, status, obs, rel, [device] if device else [])
        done[tid] = status
    return done
