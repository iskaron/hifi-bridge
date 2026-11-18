"""Minimal MQTT 3.1.1 client (stdlib): CONNECT with last will, PUBLISH QoS 0 (+ retain), SUBSCRIBE QoS 0, keepalive PINGREQ,
automatic reconnect with exponential back-off, optional TLS and username/password.  Non-blocking for callers: ``publish`` returns
False instead of waiting when the broker is not connected, so the audio path can never stall on MQTT.

Verified against a real Eclipse Mosquitto broker in Docker (tests/integration/test_11_mqtt_mosquitto.py)."""
from __future__ import annotations

import socket
import ssl
import struct
import threading
import time
from typing import Callable

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14
CONNACK_ERRORS = {1: "unacceptable protocol version", 2: "identifier rejected", 3: "server unavailable",
                  4: "bad user name or password", 5: "not authorized"}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str | bytes) -> bytes:
    b = s.encode("utf-8") if isinstance(s, str) else s
    if len(b) > 65535:
        raise ValueError("MQTT string too long")
    return struct.pack("!H", len(b)) + b


def packet(ptype: int, flags: int, body: bytes) -> bytes:
    return bytes([(ptype << 4) | flags]) + _varint(len(body)) + body


def connect_packet(client_id: str, keepalive: int, username: str | None, password: str | None,
                   will: tuple[str, bytes, bool] | None) -> bytes:
    flags = 0x02                                                # clean session
    payload = _str(client_id)
    if will:
        flags |= 0x04 | (0x20 if will[2] else 0)
        payload += _str(will[0]) + _str(will[1])
    if username:
        flags |= 0x80
        payload += _str(username)
        if password:
            flags |= 0x40
            payload += _str(password)
    return packet(CONNECT, 0, _str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive) + payload)


def publish_packet(topic: str, payload: bytes, retain: bool) -> bytes:
    return packet(PUBLISH, 0x01 if retain else 0, _str(topic) + payload)


def subscribe_packet(pid: int, topics: list[str]) -> bytes:
    return packet(SUBSCRIBE, 0x02, struct.pack("!H", pid) + b"".join(_str(t) + b"\x00" for t in topics))


class MqttClient:
    def __init__(self, host: str, port: int = 1883, client_id: str = "btbridge", username: str | None = None,
                 password: str | None = None, tls: bool = False, keepalive: int = 60,
                 will: tuple[str, bytes, bool] | None = None, subscriptions: list[str] | None = None,
                 on_message: Callable[[str, bytes], None] | None = None, on_connect: Callable[[], None] | None = None,
                 log: Callable[[str], None] = lambda m: None, tls_context: ssl.SSLContext | None = None,
                 backoff: tuple[float, float] = (1.0, 60.0)):
        self.host, self.port, self.client_id = host, port, client_id
        self.username, self.password, self.tls, self.keepalive = username, password, tls, keepalive
        self.will, self.subscriptions = will, list(subscriptions or [])
        self.on_message, self.on_connect, self.log = on_message, on_connect, log
        self.tls_context, self.backoff = tls_context, backoff
        self._sock: socket.socket | None = None
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error: str | None = None
        self.connects = 0
        self.published = 0
        self.dropped = 0

    # ---- public API ----
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mqtt")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        s = self._sock
        if s is not None and self.connected:
            try:
                with self._wlock:
                    s.sendall(packet(DISCONNECT, 0, b""))      # clean disconnect: the broker does NOT publish the last will
            except OSError:
                pass
        self._close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def publish(self, topic: str, payload: str | bytes, retain: bool = False) -> bool:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        s = self._sock
        if not self.connected or s is None:
            self.dropped += 1
            return False
        try:
            with self._wlock:
                s.sendall(publish_packet(topic, data, retain))
            self.published += 1
            return True
        except OSError as e:
            self.last_error = f"publish: {e}"
            self._close()
            return False

    def state(self) -> dict:
        return {"connected": self.connected, "last_error": self.last_error, "connects": self.connects,
                "published": self.published, "dropped": self.dropped}

    # ---- internals ----
    def _close(self) -> None:
        self.connected = False
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _open(self) -> socket.socket:
        raw = socket.create_connection((self.host, self.port), timeout=10)
        if self.tls:
            ctx = self.tls_context or ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(1.0)
        return raw

    def _recv_exact(self, s: socket.socket, n: int, deadline: float | None = None) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                if self._stop.is_set() or (deadline and time.monotonic() > deadline):
                    raise
                if not buf:                                   # idle: let the caller do keepalive work
                    raise
                continue
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return buf

    def _read_packet(self, s: socket.socket) -> tuple[int, int, bytes]:
        h = self._recv_exact(s, 1)[0]
        mult, length = 1, 0
        for _ in range(4):
            b = self._recv_exact(s, 1, deadline=time.monotonic() + 10)[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        body = self._recv_exact(s, length, deadline=time.monotonic() + 10) if length else b""
        return h >> 4, h & 0x0F, body

    def _session(self) -> None:
        s = self._open()
        if self._stop.is_set():                          # stopped while connecting: never send CONNECT with our client id,
            s.close()                                    # the replacement client already uses it (review L1)
            return
        self._sock = s
        with self._wlock:
            s.sendall(connect_packet(self.client_id, self.keepalive, self.username, self.password, self.will))
        s.settimeout(10)
        ptype, _, body = self._read_packet(s)
        s.settimeout(1.0)
        if ptype != CONNACK or len(body) < 2:
            raise ConnectionError("no CONNACK")
        if body[1] != 0:
            raise PermissionError(CONNACK_ERRORS.get(body[1], f"refused ({body[1]})"))
        self.connected, self.last_error = True, None
        self.connects += 1
        if self.subscriptions:
            with self._wlock:
                s.sendall(subscribe_packet(1, self.subscriptions))
        if self.on_connect:
            try:
                self.on_connect()
            except Exception as e:
                self.log(f"mqtt on_connect error: {type(e).__name__}: {e}")
        last_tx = time.monotonic()
        last_rx = time.monotonic()
        while not self._stop.is_set():
            try:
                ptype, flags, body = self._read_packet(s)
                last_rx = time.monotonic()
            except socket.timeout:
                now = time.monotonic()
                if now - last_tx > self.keepalive / 2:
                    with self._wlock:
                        s.sendall(packet(PINGREQ, 0, b""))
                    last_tx = now
                if now - last_rx > self.keepalive * 1.5:
                    raise ConnectionError("keepalive timeout")
                continue
            if ptype == PUBLISH:
                tlen = struct.unpack("!H", body[:2])[0]
                topic = body[2:2 + tlen].decode("utf-8", "replace")
                qos = (flags >> 1) & 3
                rest = body[2 + tlen:]
                if qos:
                    pid, rest = rest[:2], rest[2:]
                    with self._wlock:
                        s.sendall(packet(PUBACK, 0, pid))
                if self.on_message:
                    try:
                        self.on_message(topic, rest)
                    except Exception as e:
                        self.log(f"mqtt message handler error: {type(e).__name__}: {e}")

    def _run(self) -> None:
        delay = self.backoff[0]
        while not self._stop.is_set():
            try:
                self._session()
                delay = self.backoff[0]
            except PermissionError as e:                      # bad credentials: back off hard, report clearly
                self.last_error = f"refused: {e}"
                delay = self.backoff[1]
            except (OSError, ConnectionError, ValueError) as e:
                if not self._stop.is_set():
                    self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                self._close()
            if self._stop.wait(delay):
                return
            delay = min(self.backoff[1], delay * 2)
