"""The health check has to tell the truth about the MQTT connection.

Until 2026-08-28 `/healthz` answered `200` unconditionally. The connection state
was in the body, but the Docker health check only reads the status code — so
during the power cut that morning `docker ps` showed `healthy` for twenty
minutes while the bot had no broker and answered nothing.

A status that is right by construction is worth nothing. These tests pin down
that it can go wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.health import serve_health, zustand  # noqa: E402


class _Mqtt:
    def __init__(self, connected: bool, weg_seit_s: float | None = None) -> None:
        self.connected = connected
        self.getrennt_seit = None if weg_seit_s is None else time.monotonic() - weg_seit_s


class _Router:
    enabled = True
    served = 7

    def uptime(self) -> str:
        return "1h2m"


class _Bot:
    def __init__(self, mqtt: _Mqtt) -> None:
        self.mqtt = mqtt
        self.router = _Router()


def test_verbunden_ist_gesund():
    ok, body = zustand(_Bot(_Mqtt(True)), grace_s=120)
    assert ok and body["mqtt"] is True
    assert "mqtt_weg_s" not in body


def test_kurzer_ausfall_bleibt_gesund():
    """A reconnect takes seconds. Flapping the container for that helps nobody."""
    ok, body = zustand(_Bot(_Mqtt(False, weg_seit_s=5)), grace_s=120)
    assert ok is True
    assert body["mqtt"] is False           # honest in the body all the same
    assert body["mqtt_weg_s"] == 5


def test_langer_ausfall_ist_ungesund():
    """The case from 2026-08-28: broker dead, bot answering nothing."""
    ok, body = zustand(_Bot(_Mqtt(False, weg_seit_s=1200)), grace_s=120)
    assert ok is False
    assert body["mqtt"] is False
    assert body["mqtt_weg_s"] == 1200


def test_ohne_verbindung_seit_dem_start():
    """Never connected at all -- the broker was already down at startup."""
    bot = _Bot(_Mqtt(False, weg_seit_s=600))
    assert zustand(bot, grace_s=120)[0] is False


def test_alte_mqtt_klasse_ohne_zeitstempel_bricht_nicht():
    """Defensive: a client without `getrennt_seit` must not raise."""
    class Alt:
        connected = False
    ok, body = zustand(_Bot(Alt()), grace_s=120)  # type: ignore[arg-type]
    assert ok is False and body["mqtt"] is False


# --- The status code itself, over a real socket --------------------------
#
# The unit above checks the decision, this checks that the decision reaches the
# wire. That is precisely the step that was missing: the state was computed
# correctly and then sent as 200 regardless.

def test_status_code_folgt_dem_zustand():
    """Over a real socket: 200 when connected, 503 on a long outage."""
    async def lauf(bot: _Bot, port: int) -> int:
        s = Settings(health_port=port, health_mqtt_grace_s=120)
        task = asyncio.ensure_future(serve_health(s, bot))
        await asyncio.sleep(0.25)
        try:
            def abrufen() -> int:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                                timeout=3) as r:
                        json.loads(r.read())
                        return r.status
                except urllib.error.HTTPError as e:
                    return e.code
            return await asyncio.get_running_loop().run_in_executor(None, abrufen)
        finally:
            task.cancel()

    assert asyncio.run(lauf(_Bot(_Mqtt(True)), 18081)) == 200
    assert asyncio.run(lauf(_Bot(_Mqtt(False, weg_seit_s=1200)), 18082)) == 503
