"""Audio monitor tap: frames carry the virtual DAC's wall-clock time; slow listeners never slow the DAC; framing round-trips."""
import socket
import threading
import time

from hifidemo import monitor as MON


def test_frame_roundtrip_and_parser_handles_split_input():
    frames = [MON.frame(1000.25, b"\x01\x02\x03\x04" * 3), MON.frame(1000.5, b"\x05\x06\x07\x08")]
    blob = b"".join(frames)
    p = MON.FrameParser()
    out = []
    for i in range(0, len(blob), 5):                    # arbitrary TCP segmentation
        out += p.feed(blob[i:i + 5])
    assert out == [(1000.25, b"\x01\x02\x03\x04" * 3), (1000.5, b"\x05\x06\x07\x08")]


def test_parser_rejects_garbage():
    p = MON.FrameParser()
    try:
        p.feed(b"XXXXXXXXXXXXXXXXXXXX")
    except ValueError:
        return
    raise AssertionError("garbage accepted")


def test_broadcaster_delivers_to_every_listener_and_drops_for_slow_ones():
    b = MON.Broadcaster(max_queue_s=0.1)
    fast, slow = b.subscribe(), b.subscribe()
    chunk = b"\0\0\0\0" * 441                           # 10 ms
    for i in range(50):                                 # 0.5 s
        b.publish(1000 + i / 100, chunk)
        fast.get_nowait()
    assert slow.qsize() <= 11                           # bounded: ~0.1 s, the DAC never waits for a listener
    assert b.dropped >= 39
    b.unsubscribe(slow)
    assert len(b.listeners) == 1


def test_level_meter_reports_rms_and_peak_in_dbfs():
    import numpy as np
    loud = (np.full(882, 16384, dtype="<i2")).tobytes()
    silent = bytes(882 * 2)
    assert MON.level(loud)["peak_dbfs"] > -7 and MON.level(loud)["rms_dbfs"] > -7
    assert MON.level(silent)["peak_dbfs"] == MON.FLOOR_DBFS


def test_tcp_server_sends_header_then_frames():
    b = MON.Broadcaster()
    srv = MON.serve(b, 0, host="127.0.0.1", info={"player": "Wohnzimmer"})
    port = srv.server_address[1]
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    f = s.makefile("rb")
    header = f.readline()
    assert b'"player": "Wohnzimmer"' in header and b'"rate": 44100' in header
    for _ in range(50):                                 # the subscription may take a moment
        if b.listeners:
            break
        time.sleep(0.02)
    t = time.time()
    b.publish(t, b"\x10\x00\x20\x00")
    p = MON.FrameParser()
    got = []
    while not got:
        got = p.feed(f.read1(4096))
    assert got[0] == (t, b"\x10\x00\x20\x00")
    s.close(); srv.shutdown()


def test_dac_tap_publishes_paced_chunks():
    b = MON.Broadcaster()
    q = b.subscribe()
    tap = MON.DacTap(b)
    r, w = __import__("os").pipe()
    th = threading.Thread(target=MON.run_dac, args=(r, tap, 0.01), daemon=True)
    th.start()
    payload = b"\x01\x00\x01\x00" * 4410                # 0.1 s
    t0 = time.monotonic()
    __import__("os").write(w, payload)
    __import__("os").close(w)
    th.join(3)
    elapsed = time.monotonic() - t0
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    assert b"".join(pcm for _, pcm in frames) == payload
    assert elapsed >= 0.08                              # consumed in real time, not at memory speed
