"""Minimal health check with no extra dependency.

Deliberately covers only our own process and the MQTT connection — not the
external APIs. A brief GeoSphere hiccup is no reason to restart the container.

**A bot that cannot reach the broker is not healthy.** Until 2026-08-28 this
endpoint returned `200` unconditionally and merely put the connection state in
the body, which nobody read: the Docker health check only looks at the status
code. During the power cut that morning `docker ps` reported `healthy` for
twenty minutes while the bot had no broker and could not answer a single
command. A status that is right by construction is worth nothing.

Two details keep it from becoming a nuisance:

* **Grace period.** A reconnect takes seconds; reporting unhealthy for that
  would make the container flap. Only an outage lasting longer than
  `health_mqtt_grace_s` counts.
* **A restart does not fix a dead broker.** paho reconnects by itself once the
  broker returns, so the restart this health check eventually triggers repairs
  nothing. That is not the point — the point is that the outage becomes
  *visible* instead of hiding behind a green `healthy`. Fixing the cause is the
  watchdog's job.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .config import Settings


def zustand(bot: Any, grace_s: float) -> tuple[bool, dict[str, Any]]:
    """(healthy?, body). Split out so a test does not need a socket."""
    getrennt_seit = getattr(bot.mqtt, "getrennt_seit", None)
    dauer = None if getrennt_seit is None else time.monotonic() - getrennt_seit
    # Connected, or gone for less time than the grace period allows.
    ok = bool(bot.mqtt.connected) or (dauer is not None and dauer < grace_s)
    body: dict[str, Any] = {
        "ok": ok,
        "mqtt": bot.mqtt.connected,
        "enabled": bot.router.enabled,
        "served": bot.router.served,
        "uptime": bot.router.uptime(),
    }
    if dauer is not None:
        body["mqtt_weg_s"] = int(dauer)
    return ok, body


async def serve_health(settings: Settings, bot: Any) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readline()
            ok, daten = zustand(bot, settings.health_mqtt_grace_s)
            body = json.dumps(daten).encode()
            kopf = b"HTTP/1.1 200 OK\r\n" if ok else b"HTTP/1.1 503 Service Unavailable\r\n"
            writer.write(kopf + b"Content-Type: application/json\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", settings.health_port)
    async with server:
        await server.serve_forever()
