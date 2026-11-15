"""Exploratory endless synthetic live stream server (NOT the bridge).

GET /live.wav[?burst=MS]   endless WAV (size fields 0xFFFFFFFF), paced in real time
GET /live.flac[?burst=MS]  endless FLAC via the `flac` encoder
Every connection starts at the current live edge (minus optional burst lead-in).
Each request is logged with headers so LMS / player behaviour can be inspected.
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import signal as sig

T0 = time.time()
LOCK = threading.Lock()


def wav_header(rate=sig.RATE, ch=2, bits=16, size=0xFFFFFFFF) -> bytes:
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
            + b"data" + struct.pack("<I", size))


def log(**kw):
    kw["t"] = round(time.time(), 3)
    with LOCK:
        print("SYNTH " + json.dumps(kw), flush=True)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        burst_ms = int(q.get("burst", ["0"])[0])
        fmt = "flac" if u.path.endswith(".flac") else "wav"
        log(ev="request", method=self.command, path=u.path, peer=self.client_address[0], headers={k: v for k, v in self.headers.items()})
        if u.path not in ("/live.wav", "/live.flac", "/radio.wav"):
            self.send_error(404)
            return
        rng = self.headers.get("Range", "")
        range_start = int(rng.split("=")[1].split("-")[0]) if rng.startswith("bytes=") else 0
        self.send_response(206 if range_start else 200)
        if range_start:
            self.send_header("Content-Range", "bytes %d-4294967294/4294967295" % range_start)
        self.send_header("Content-Type", "audio/x-flac" if fmt == "flac" else "audio/x-wav")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("icy-name", "Synthetic live")
        icy = "icy" in q and self.headers.get("Icy-MetaData", "") == "1" and fmt == "wav"
        METAINT = 16384
        if icy:
            self.send_header("icy-metaint", str(METAINT))
        self.end_headers()
        if head:
            return
        chunk = 882  # 20 ms
        now_f = int((time.time() - T0) * sig.RATE)
        pos = max(0, now_f - burst_ms * sig.RATE // 1000)
        enc = None
        if fmt == "flac":
            enc = subprocess.Popen(["flac", "-s", "-c", "--force-raw-format", "--endian=little", "--sign=signed", "--channels=2",
                                    "--bps=16", "--sample-rate=44100", "-0", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            threading.Thread(target=self._pump, args=(enc,), daemon=True).start()
        elif range_start == 0:
            self.wfile.write(wav_header())
        log(ev="stream_start", fmt=fmt, start_frame=pos, burst_ms=burst_ms)
        sent = 0
        sent_audio = 0 if range_start else -0
        stall_at = float(q.get("stall_at", ["-1"])[0]); stall_for = float(q.get("stall_for", ["0"])[0])
        conn_t0 = time.time(); stalled = False
        try:
            while True:
                if stall_at >= 0 and not stalled and time.time() - conn_t0 >= stall_at:
                    stalled = True
                    log(ev="stall_begin", for_s=stall_for)
                    time.sleep(stall_for)
                    pos = int((time.time() - T0) * sig.RATE)   # resume at live edge (stalled audio is lost)
                    log(ev="stall_end")
                target = int((time.time() - T0) * sig.RATE)
                while pos + chunk <= target:
                    data = sig.frames_bytes(pos, chunk)
                    pos += chunk
                    if icy and fmt == "wav":
                        # insert an ICY metadata block every METAINT audio bytes (title changes every ~5 s)
                        buf = data
                        while buf:
                            room = METAINT - (sent_audio % METAINT)
                            part, buf = buf[:room], buf[room:]
                            self.wfile.write(part); sent_audio += len(part)
                            if sent_audio % METAINT == 0:
                                title = ("StreamTitle='Song %d - Artist %d';" % (int(time.time() - T0) // 5, 7)).encode()
                                title += b"\0" * (-len(title) % 16)
                                self.wfile.write(bytes([len(title) // 16]) + title)
                    else:
                        (enc.stdin if enc else self.wfile).write(data)
                    sent += len(data)
                time.sleep(0.005)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            log(ev="stream_end", peer=self.client_address[0], bytes=sent)
            if enc:
                enc.kill()

    def _pump(self, enc):
        try:
            while True:
                d = enc.stdout.read1(4096)
                if not d:
                    break
                self.wfile.write(d)
                self.wfile.flush()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    ThreadingHTTPServer.daemon_threads = True
    print("synth server on", a.port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
