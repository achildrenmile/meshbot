"""Einstiegspunkt: MQTT anbinden, Handler verdrahten, sauber beenden."""

from __future__ import annotations

import asyncio
import json
import signal
from typing import Any

import httpx
import structlog
from cachetools import TTLCache

from .config import Settings, load_settings
from .handlers import relais as h_relais
from .handlers import sota as h_sota
from .handlers import uwz as h_uwz
from .handlers import wx as h_wx
from .health import serve_health
from .mqtt_client import MqttClient
from .router import Router

log = structlog.get_logger(__name__)


class Bot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=settings.http_timeout_s, headers={"User-Agent": "MeshBot/1.0 (CarinthiaMesh)"})
        self.stations = h_wx.load_stations(settings)
        self.relais = h_relais.load_relais(settings.relais_file)
        self.cache_wx: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_wx_s)
        self.cache_uwz: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_uwz_s)
        self.cache_sota: TTLCache = TTLCache(maxsize=256, ttl=settings.cache_ttl_sota_s)
        self.stale: dict[str, Any] = {}          # letzte gute Antwort je Schlüssel
        self.router = Router(settings, {
            "wx": self.cmd_wx, "uwz": self.cmd_uwz, "sota": self.cmd_sota,
            "relais": self.cmd_relais, "ping": self.cmd_ping, "help": self.cmd_help,
        })
        self.mqtt = MqttClient(settings, on_message=self.on_message, on_admin=self.on_admin)

    # --- Befehle ---------------------------------------------------------

    async def cmd_wx(self, arg: str, sender: str) -> str | None:
        treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
        if treffer is None:
            return f"WX: {arg[:20]} unbekannt"
        ort, station = treffer
        if ort in self.cache_wx:
            return h_wx.render(ort, self.cache_wx[ort])
        try:
            werte = await self._mit_retry(h_wx.fetch, self.settings, station["station_id"])
        except Exception:
            alt = self.stale.get(f"wx:{ort}")
            return h_wx.render(ort, alt, stale=True) if alt else "WX: Quelle nicht erreichbar"
        self.cache_wx[ort] = werte
        self.stale[f"wx:{ort}"] = werte
        return h_wx.render(ort, werte)

    async def cmd_uwz(self, arg: str, sender: str) -> str | None:
        if "aktuell" in self.cache_uwz:
            return h_uwz.render(self.cache_uwz["aktuell"])
        try:
            warnungen = await h_uwz.fetch(self.http, self.settings.warn_url)
        except Exception:
            alt = self.stale.get("uwz")
            return h_uwz.render(alt, stale=True) if alt is not None else "UWZ: Quelle nicht erreichbar"
        self.cache_uwz["aktuell"] = warnungen
        self.stale["uwz"] = warnungen
        return h_uwz.render(warnungen)

    async def cmd_sota(self, arg: str, sender: str) -> str | None:
        ref = h_sota.normalise(arg, self.settings.sota_default_assoc)
        if ref is None:
            return f"SOTA: {arg[:16]} nicht gefunden"
        if ref in self.cache_sota:
            return h_sota.render(ref, self.cache_sota[ref])
        try:
            gipfel = await self._mit_retry(h_sota.fetch, self.settings.sota_url, ref)
        except Exception:
            alt = self.stale.get(f"sota:{ref}")
            return h_sota.render(ref, alt, stale=True) if alt else "SOTA: Quelle nicht erreichbar"
        self.cache_sota[ref] = gipfel
        if gipfel:
            self.stale[f"sota:{ref}"] = gipfel
        return h_sota.render(ref, gipfel)

    async def cmd_relais(self, arg: str, sender: str) -> str | None:
        teile = arg.split(maxsplit=1)
        band = (teile[0] if teile else "2m").lower()
        if band not in h_relais.BAENDER:
            return "Relais: Band 2m, 70cm oder 23cm"
        ort_arg = teile[1] if len(teile) > 1 else self.settings.default_location
        treffer = h_wx.resolve_place(ort_arg, self.stations, self.settings.default_location)
        if treffer is None:
            return f"Relais: {ort_arg[:16]} unbekannt"
        ort, koord = treffer
        gefunden = h_relais.suche(self.relais, band, koord["lat"], koord["lon"])
        return h_relais.render(band, ort, gefunden)

    async def cmd_ping(self, arg: str, sender: str) -> str:
        return f"{self.settings.bot_name} OK, up {self.router.uptime()}, {self.router.served} cmds"

    async def cmd_help(self, arg: str, sender: str) -> str:
        return "Cmds: !wx <ort> !uwz !sota <ref> !relais <band> [ort] !ping"

    # --- Infrastruktur ---------------------------------------------------

    async def _mit_retry(self, fn: Any, *args: Any) -> Any:
        letzter: Exception | None = None
        for versuch in range(self.settings.http_retries + 1):
            try:
                return await fn(self.http, *args)
            except Exception as exc:
                letzter = exc
                if versuch < self.settings.http_retries:
                    await asyncio.sleep(0.5)
        raise letzter  # type: ignore[misc]

    async def on_message(self, raw: bytes) -> None:
        antwort = await self.router.handle(raw)
        if antwort is None:
            return
        payload = self.settings.tx_template.format(
            channel=self.settings.tx_channel,
            text=antwort.replace('"', "'"),
        )
        log.info("antwort", text=antwort, laenge=len(antwort))
        self.mqtt.publish(self.settings.topic_tx, payload)

    def on_admin(self, raw: bytes) -> None:
        befehl = raw.decode("utf-8", errors="replace").strip().lower()
        if befehl in ("pause", "stop", "off"):
            self.router.enabled = False
            log.warning("bot_pausiert")
        elif befehl in ("resume", "start", "on"):
            self.router.enabled = True
            log.warning("bot_fortgesetzt")

    async def run(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        self.mqtt.start(loop)
        health = asyncio.create_task(serve_health(self.settings, self))
        log.info("gestartet", rx=self.settings.topic_rx, tx=self.settings.topic_tx,
                 enabled=self.router.enabled, relais=len(self.relais), orte=len(self.stations))
        await stop.wait()
        log.info("beende")
        health.cancel()
        self.mqtt.stop()
        await self.http.aclose()


def main() -> None:
    settings = load_settings()
    structlog.configure(processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ])
    asyncio.run(Bot(settings).run())


if __name__ == "__main__":
    main()
