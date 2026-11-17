"""Gateway: the demo's only container with a published port; plain TCP forwarding to the internal network (TLS stays end to end)."""
import asyncio
import socket
import threading
import time

import pytest

from hifidemo import gateway as G


def test_parse_map():
    assert G.parse_map("8080=hifi-bridge:8080, 9000=lms:9000") == [(8080, "hifi-bridge", 8080), (9000, "lms", 9000)]
    with pytest.raises(ValueError):
        G.parse_map("8080=lms")
    with pytest.raises(ValueError):
        G.parse_map("80=lms:9000")                      # privileged ports are not used (the container runs unprivileged)


def echo_server():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen()

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            def serve(c=c):
                with c:
                    while (d := c.recv(65536)):
                        c.sendall(d.upper())
            threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=run, daemon=True).start()
    return srv


def start_gateway(mapping):
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    servers = []

    def run():
        asyncio.set_event_loop(loop)
        servers.extend(loop.run_until_complete(G.start(mapping, host="127.0.0.1")))
        ready.set()
        loop.run_forever()
    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    return loop, servers


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_bytes_flow_both_ways_and_large_transfers_survive():
    srv = echo_server()
    port = free_port()
    loop, _ = start_gateway([(port, "127.0.0.1", srv.getsockname()[1])])
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    payload = b"hifi" * 250_000                         # 1 MB
    threading.Thread(target=c.sendall, args=(payload,), daemon=True).start()
    got = b""
    while len(got) < len(payload):
        got += c.recv(65536)
    assert got == payload.upper()
    c.close(); srv.close(); loop.call_soon_threadsafe(loop.stop)


def test_unreachable_target_closes_the_client_quickly():
    port = free_port()
    loop, _ = start_gateway([(port, "127.0.0.1", free_port())])   # nothing listens there (e.g. Home Assistant not started)
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    t0 = time.time()
    assert c.recv(10) == b""
    assert time.time() - t0 < 3
    loop.call_soon_threadsafe(loop.stop)
