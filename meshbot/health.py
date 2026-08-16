"""Minimaler Healthcheck ohne Zusatzabhaengigkeit.

Prueft absichtlich nur den eigenen Prozess und die MQTT-Verbindung — nicht die
externen APIs. Eine kurze Stoerung bei der GeoSphere ist kein Grund, den
Container neu zu starten.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .config import Settings


async def serve_health(settings: Settings, bot: Any) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readline()
            body = json.dumps({
                "ok": True,
                "mqtt": bot.mqtt.connected,
                "enabled": bot.router.enabled,
                "served": bot.router.served,
                "uptime": bot.router.uptime(),
            }).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", settings.health_port)
    async with server:
        await server.serve_forever()
