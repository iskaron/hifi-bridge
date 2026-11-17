"""The demo's only door to the host: forwards published TCP ports to services on the INTERNAL demo network.

All demo services sit on an ``internal`` Docker network (no route to the LAN or the internet). This process is attached to that network
and to a normal one so that Docker can publish its ports - bound to 127.0.0.1 by default. It forwards raw TCP: HTTPS stays end-to-end
between the browser and the bridge (the gateway never sees plaintext or certificates).

  python3 -m hifidemo.gateway "8080=hifi-bridge:8080,8443=hifi-bridge:8443,9000=lms:9000,8100=console:8100"
"""
from __future__ import annotations

import asyncio
import sys


def parse_map(spec: str) -> list[tuple[int, str, int]]:
    out = []
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        try:
            listen, target = item.split("=", 1)
            host, port = target.rsplit(":", 1)
            lp, tp = int(listen), int(port)
        except ValueError as e:
            raise ValueError(f"bad mapping {item!r} (expected LISTEN=HOST:PORT)") from e
        if not (1024 <= lp <= 65535 and 1 <= tp <= 65535) or not host:
            raise ValueError(f"bad mapping {item!r} (listen port 1024-65535)")
        out.append((lp, host, tp))
    return out


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # nosec B110 - closing a broken connection
            pass


def _handler(host: str, port: int):
    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        try:
            tr, tw = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
        except (OSError, asyncio.TimeoutError):
            cw.close()                                    # service not running (e.g. Home Assistant profile off)
            return
        await asyncio.gather(_pipe(cr, tw), _pipe(tr, cw))
    return handle


async def start(mapping, host: str = "0.0.0.0"):  # nosec B104 - inside the gateway container; Docker publishes to 127.0.0.1 by default
    return [await asyncio.start_server(_handler(h, tp), host, lp) for lp, h, tp in mapping]


def main(argv=None) -> int:
    spec = (argv if argv is not None else sys.argv[1:])[0]
    mapping = parse_map(spec)

    async def run():
        await start(mapping)
        print("[gateway] " + ", ".join(f":{lp} -> {h}:{tp}" for lp, h, tp in mapping), flush=True)
        await asyncio.Event().wait()
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
