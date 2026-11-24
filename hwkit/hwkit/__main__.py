"""``python3 -m hwkit <command>`` - see hwkit/README.md for the full runbook."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _cmd_env(a):
    from .env import main
    main(a.output); print(f"wrote {a.output}")


def _cmd_trace(a):
    from btbridge.adapters.bluez.jeepney_bus import JeepneyBus
    from .trace import TraceRecorder
    rec = TraceRecorder(JeepneyBus(bus=a.bus), a.output, label=a.label, anonymize=not a.no_anonymize,
                        meta={"operator_note": a.note or ""})
    rec.start()
    print("recording BlueZ events. Type a marker + Enter (e.g. 'android spotify play', 'expect:media', 'expect:no-media'); Ctrl-D ends.",
          flush=True)
    try:
        for line in sys.stdin:
            if line.strip():
                rec.marker(line.strip()); print(f"  marker #{rec.count}", flush=True)
    except KeyboardInterrupt:
        pass
    rec.stop(); print(f"wrote {a.output} ({rec.count} records)")


def _cmd_capture(a):
    from .capture import record
    try:
        meta = record(a.output, a.address, a.seconds, node=a.node, mic_node=a.mic, label=a.label)
    except RuntimeError as e:                       # e.g. the phone's audio is only a playback stream (G1): say why, no traceback
        print(f"capture: {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(meta, indent=1))


def _cmd_tracks(a):
    from .signals import make_phone_tracks
    print(json.dumps(make_phone_tracks(a.output), indent=1))


def _cmd_analyze(a):
    from . import analyze as A
    if os.path.isdir(a.path):                          # a capture directory: phone.wav (+ mic.wav) + capture.json
        meta = json.load(open(os.path.join(a.path, "capture.json")))
        out, blocks = {"label": meta.get("label")}, {}
        for name, r in meta["recordings"].items():
            x, rate = A.read_wav(os.path.join(a.path, r["file"]))
            blocks[name] = (A.find_blocks(x, rate), r["first_sample_wall"])
            out[name] = A.continuity(blocks[name][0])
        if "phone" in blocks and "mic" in blocks and all(blocks[k][1] for k in ("phone", "mic")):
            out["latency_phone_input_to_mic"] = A.latency(blocks["phone"][0], blocks["phone"][1], blocks["mic"][0], blocks["mic"][1])
    else:
        x, rate = A.read_wav(a.path)
        out = A.continuity(A.find_blocks(x, rate))
    print(json.dumps(out, indent=1))


def _cmd_replay(a):
    from .replay import replay
    from .trace import load
    res = replay(load(a.trace), settle_s=a.settle)
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["all_checks_ok"] else 1)


def _cmd_results(a):
    from . import results as R
    if a.action == "new":
        R.new_session(a.file, a.board, a.operator, a.phone or [], a.player or []); print(f"created {a.file}")
    elif a.action == "set":
        R.set_result(a.file, a.id, a.status, a.observations, a.evidence or [], a.device or [],
                     json.loads(a.measurements) if a.measurements else None); print(f"{a.id} -> {a.status}")
    elif a.action == "check":
        p = R.check(a.file); print("\n".join(p) if p else "no problems"); sys.exit(1 if p else 0)
    else:
        print(R.report(a.file))


def _cmd_preflight(a):
    from .preflight import checks, report
    text, rc = report(checks())
    print(text)
    sys.exit(rc)


def _cmd_latency(a):
    from . import analyze as A
    from .latency import measure
    from .trace import load
    meta = json.load(open(os.path.join(a.capture, "capture.json")))
    rec = meta["recordings"]["mic"]
    x, rate = A.read_wav(os.path.join(a.capture, rec["file"]))
    print(json.dumps(measure(load(a.trace), A.find_blocks(x, rate), rec["first_sample_wall"]), indent=1))


def _cmd_guide(a):
    from .guide import Actions, run
    act = Actions(a.address, a.mic, input, print)
    print(json.dumps(run(a.results, only=a.only.split(",") if a.only else None, actions=act), indent=1))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="hwkit", description="Hi-Fi Bridge hardware validation kit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("env", help="record versions of the device under test"); p.add_argument("-o", "--output", default="env.json"); p.set_defaults(f=_cmd_env)
    p = sub.add_parser("trace", help="record BlueZ D-Bus events + operator markers")
    p.add_argument("-o", "--output", required=True); p.add_argument("--bus", default="SYSTEM"); p.add_argument("--note")
    p.add_argument("--no-anonymize", action="store_true"); p.add_argument("--label", default="REAL-HARDWARE"); p.set_defaults(f=_cmd_trace)
    p = sub.add_parser("capture", help="record the phone's PCM (and optionally a microphone) to WAV")
    p.add_argument("-o", "--output", required=True); p.add_argument("--address"); p.add_argument("--node"); p.add_argument("--mic")
    p.add_argument("--seconds", type=float, default=60); p.add_argument("--label", default="REAL-HARDWARE"); p.set_defaults(f=_cmd_capture)
    p = sub.add_parser("tracks", help="generate the phone test tracks (needs numpy)"); p.add_argument("-o", "--output", default="tracks"); p.set_defaults(f=_cmd_tracks)
    p = sub.add_parser("analyze", help="decode a WAV or a capture directory (needs numpy)"); p.add_argument("path"); p.set_defaults(f=_cmd_analyze)
    p = sub.add_parser("replay", help="replay a trace through the bridge translator"); p.add_argument("trace"); p.add_argument("--settle", type=float, default=2.5)
    p.set_defaults(f=_cmd_replay)
    p = sub.add_parser("results", help="structured results file"); p.add_argument("action", choices=["new", "set", "check", "report"]); p.add_argument("file")
    p.add_argument("--board"); p.add_argument("--operator"); p.add_argument("--phone", action="append"); p.add_argument("--player", action="append")
    p.add_argument("--id"); p.add_argument("--status"); p.add_argument("--observations", default=""); p.add_argument("--evidence", action="append")
    p.add_argument("--device", action="append"); p.add_argument("--measurements"); p.set_defaults(f=_cmd_results)
    p = sub.add_parser("preflight", help="read-only check of tools and configuration on the test Pi"); p.set_defaults(f=_cmd_preflight)
    p = sub.add_parser("latency", help="E2: AVRCP events (trace) vs. sound at the microphone (capture dir)")
    p.add_argument("trace"); p.add_argument("capture"); p.set_defaults(f=_cmd_latency)
    p = sub.add_parser("guide", help="guided session through the catalogue (records results)")
    p.add_argument("results"); p.add_argument("--address"); p.add_argument("--mic"); p.add_argument("--only"); p.set_defaults(f=_cmd_guide)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
