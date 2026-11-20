"""UNIT (FAKE broker: a scripted MQTT 3.1.1 peer on a loopback socket; the REAL broker is tests/integration/test_11_mqtt_mosquitto.py and the
real Home Assistant suite): how the bridge's own MQTT client behaves when the broker misbehaves - refuses the login, answers with something
else than CONNACK, sends QoS 1 publishes, stalls, drops the socket, presents an untrusted certificate. MQTT is optional: none of this may
block the audio path or kill the thread. Coverage package 7b-3."""
import socket
import ssl
import struct
import threading
import time

import pytest

from btbridge.mqtt.client import CONNACK, PINGREQ, PUBACK, MqttClient, _str, connect_packet, packet


def read_packet(conn, timeout=5.0):
    conn.settimeout(timeout)
    first = conn.recv(1)
    if not first:
        return None
    mult, length = 1, 0
    while True:
        b = conn.recv(1)[0]
        length += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    body = b""
    while len(body) < length:
        body += conn.recv(length - len(body))
    return first[0] >> 4, first[0] & 0x0F, body


class Broker:
    """Accepts connections one after the other and hands each to ``script(conn, n)`` (n = 1, 2, ...)."""
    def __init__(self, script, ctx=None):
        self.ls = socket.socket(); self.ls.bind(("127.0.0.1", 0)); self.ls.listen(4)
        self.port, self.script, self.ctx, self.conns, self.seen = self.ls.getsockname()[1], script, ctx, 0, []
        self.t = threading.Thread(target=self._serve, daemon=True); self.t.start()

    def _serve(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            self.conns += 1
            try:
                if self.ctx:
                    c = self.ctx.wrap_socket(c, server_side=True)
                self.script(c, self.conns)
            except (OSError, ssl.SSLError, IndexError):
                pass
            finally:
                try:
                    c.close()
                except OSError:
                    pass

    def close(self):
        self.ls.close()


@pytest.fixture
def make():
    made = []

    def mk(script, ctx=None, **kw):
        b = Broker(script, ctx)
        c = MqttClient("127.0.0.1", b.port, client_id="cov", backoff=(0.05, 0.1), **kw)
        made.append((b, c))
        return b, c
    yield mk
    for b, c in made:
        c.stop(); b.close()


def until(cond, t=8.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


CONNACK_OK = packet(CONNACK, 0, b"\x00\x00")


def test_a_refused_login_is_reported_with_the_brokers_reason_and_retried_slowly(make):
    def script(c, n):
        read_packet(c); c.sendall(packet(CONNACK, 0, b"\x00\x05")); time.sleep(0.5)
    b, c = make(script, username="u", password="wrong")
    c.start()
    assert until(lambda: (c.last_error or "").startswith("refused: not authorized"))
    assert not c.connected and c.connects == 0


def test_a_broker_that_does_not_answer_with_connack_is_an_error_not_a_hang(make):
    def script(c, n):
        read_packet(c); c.sendall(b"\xd0\x00"); time.sleep(0.5)                 # PINGRESP instead of CONNACK
    b, c = make(script)
    c.start()
    assert until(lambda: (c.last_error or "") == "ConnectionError: no CONNACK")


def test_qos1_publishes_are_acknowledged_and_a_failing_handler_does_not_stop_the_stream(make):
    got, logs, acks = [], [], []

    def handler(topic, payload):
        got.append((topic, payload))
        if payload == b"boom":
            raise ValueError("handler bug")

    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK)
        for pid, payload in ((7, b"boom"), (8, b"fine")):
            c.sendall(packet(3, 0x02, _str("cmd/pairing") + struct.pack("!H", pid) + payload))     # PUBLISH, QoS 1
        for _ in range(2):
            acks.append(read_packet(c))
        time.sleep(0.3)
    b, c = make(script, on_message=handler, log=logs.append)
    c.start()
    assert until(lambda: len(acks) == 2 and got == [("cmd/pairing", b"boom"), ("cmd/pairing", b"fine")])
    assert acks == [(PUBACK, 0, b"\x00\x07"), (PUBACK, 0, b"\x00\x08")]
    assert any("mqtt message handler error: ValueError: handler bug" in m for m in logs)


def test_an_on_connect_hook_that_fails_is_logged_and_the_connection_stays_up(make):
    logs = []

    def boom():
        raise RuntimeError("discovery publish failed")

    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK); time.sleep(1.0)
    b, c = make(script, on_connect=boom, log=logs.append)
    c.start()
    assert until(lambda: any("mqtt on_connect error: RuntimeError: discovery publish failed" in m for m in logs))
    assert c.connected


def test_an_idle_connection_sends_pingreq_and_a_silent_broker_ends_it_with_a_reconnect(make):
    pings = []

    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK)
        if n == 1:
            try:
                while True:                                                     # never answers: the client must notice
                    p = read_packet(c, timeout=6)
                    if p is None:
                        return
                    if p[0] == PINGREQ:
                        pings.append(time.monotonic())
            except (socket.timeout, OSError):
                return
        else:
            time.sleep(1.0)
    b, c = make(script, keepalive=2)
    c.start()
    assert until(lambda: b.conns >= 2, 15), (b.conns, c.last_error)               # reconnected after "keepalive timeout"
    assert pings, "no PINGREQ was sent while the connection was idle"
    assert c.connects >= 2


def test_a_publish_on_a_broken_socket_reports_false_and_drops_the_connection(make):
    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK); time.sleep(2.0)
    b, c = make(script)
    c.start()
    assert until(lambda: c.connected)
    sock = c._sock
    sock.shutdown(socket.SHUT_RDWR)                                              # the network went away under our feet
    assert c.publish("a/b", "x") is False                                        # never raises, never blocks
    # whichever noticed first - the failing send or the reader thread seeing the broker's EOF - the connection is dropped and says why
    assert until(lambda: not c.connected and (c.last_error or "").startswith(("publish: ", "ConnectionError", "OSError")))
    assert c.publish("a/b", "x") is False                                       # and later calls never block either


def test_stop_without_a_connection_and_a_connection_that_vanishes_before_the_goodbye(make):
    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK); time.sleep(1.0)
    b, c = make(script)
    c.stop()                                                                     # never started
    c.start()
    assert until(lambda: c.connected)
    c._sock.shutdown(socket.SHUT_RDWR)                                           # DISCONNECT cannot be sent any more
    c.stop()
    assert not c.connected


def test_a_malformed_remaining_length_from_the_broker_is_a_connection_error(make):
    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK)
        if n == 1:
            c.sendall(b"\x30\xff\xff\xff\xff\x7f")                              # a fifth length byte: not valid MQTT
        time.sleep(1.0)
    b, c = make(script)
    c.start()
    assert until(lambda: b.conns >= 2, 10)                                       # the stream was abandoned and re-opened


# ---------------------------------------------------------------- TLS
def tls_ctx(tmp_path):
    from btbridge.net.certs import CertManager
    cm = CertManager(str(tmp_path), "mqtt0001"); cm.issue_server(["127.0.0.1"])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(*cm.server_paths())
    return ctx, cm


def test_tls_connects_with_the_device_ca_and_refuses_an_untrusted_certificate_by_default(make, tmp_path):
    ctx, cm = tls_ctx(tmp_path)

    def script(c, n):
        read_packet(c); c.sendall(CONNACK_OK); time.sleep(1.0)
    b, trusted = make(script, ctx=ctx, tls=True, tls_context=ssl.create_default_context(cafile=cm.p["ca_crt"]))
    trusted.start()
    assert until(lambda: trusted.connected)                                       # the CA vouches for 127.0.0.1
    b2, default = make(script, ctx=ctx, tls=True)                                 # no CA given: the system roots do not know it
    default.start()
    assert until(lambda: "CERTIFICATE_VERIFY_FAILED" in (default.last_error or "") or "certificate verify failed" in (default.last_error or ""))
    assert not default.connected


def test_oversized_strings_and_credentials_without_a_password_are_encoded_correctly():
    with pytest.raises(ValueError, match="too long"):
        connect_packet("x" * 70000, 60, None, None, None)
    p = connect_packet("cid", 60, "user-only", None, None)
    assert p[2 + 6] == 4 and p[2 + 7] & 0x80 and not p[2 + 7] & 0x40               # user name flag without password flag
