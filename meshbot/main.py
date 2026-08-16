"""Einstiegspunkt: MQTT anbinden, Handler verdrahten, sauber beenden."""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from cachetools import TTLCache

from .config import Settings, load_settings
from .handlers import lawine as h_lawine
from .handlers import netz as h_netz
from .handlers import relais as h_relais
from .handlers import sonne as h_sonne
from .handlers import spot as h_spot
from .handlers import vorhersage as h_fc
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
        self.summits = h_sota.load_summits(settings.summits_file)
        self.cache_wx: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_wx_s)
        self.cache_uwz: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_uwz_s)
        self.cache_sota: TTLCache = TTLCache(maxsize=256, ttl=settings.cache_ttl_sota_s)
        self.cache_spot: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_spot_s)
        self.cache_lawine: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_lawine_s)
        self.cache_fc: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_forecast_s)
        self.cache_netz: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_netz_s)
        self.stale: dict[str, Any] = {}          # letzte gute Antwort je Schlüssel
        self.router = Router(settings, {
            "wx": self.cmd_wx, "uwz": self.cmd_uwz, "sota": self.cmd_sota,
            "relais": self.cmd_relais, "ping": self.cmd_ping, "help": self.cmd_help,
            "sonne": self.cmd_sonne, "spot": self.cmd_spot, "lawine": self.cmd_lawine,
            "netz": self.cmd_netz, "vorhersage": self.cmd_vorhersage, "zeit": self.cmd_zeit,
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
        # Position statt Referenz: am Gipfel kennt man die Referenz selten,
        # das Geraet aber die Koordinaten.
        koord = h_sota.parse_coords(arg)
        if koord is not None:
            return h_sota.render_nearest(h_sota.nearest(self.summits, *koord))

        ref = h_sota.normalise(arg, self.settings.sota_default_assoc)
        if ref is None:
            return f"SOTA: {arg[:16]} nicht gefunden"
        if ref in self.cache_sota:
            return h_sota.render(ref, self.cache_sota[ref])
        try:
            gipfel = await self._mit_retry(h_sota.fetch, self.settings.sota_url, ref)
        except Exception:
            alt = self.stale.get(f"sota:{ref}")
            if alt:
                return h_sota.render(ref, alt, stale=True)
            lokal = next((s for s in self.summits if s["ref"] == ref), None)
            if lokal:                       # eigener Bestand statt Fehlermeldung
                return h_sota.render(ref, {"name": lokal["name"], "altM": lokal["alt"],
                                           "points": lokal["pts"], "activationCount": lokal["akt"]},
                                     stale=True)
            return "SOTA: Quelle nicht erreichbar"
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

        # Bei einer Position braucht es keinen Ortsnamen — "hier" ist kuerzer
        # und ehrlicher als der Name der naechsten Wetterstation.
        koord = h_sota.parse_coords(ort_arg)
        if koord is not None:
            return h_relais.render(band, "hier", h_relais.suche(self.relais, band, *koord))

        treffer = h_wx.resolve_place(ort_arg, self.stations, self.settings.default_location)
        if treffer is None:
            return f"Relais: {ort_arg[:16]} unbekannt"
        ort, station = treffer
        gefunden = h_relais.suche(self.relais, band, station["lat"], station["lon"])
        return h_relais.render(band, ort, gefunden)

    async def cmd_sonne(self, arg: str, sender: str) -> str:
        koord = h_sota.parse_coords(arg)
        if koord is None:                       # ohne Position: Standardort
            treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
            koord = (treffer[1]["lat"], treffer[1]["lon"]) if treffer else (46.61, 13.86)
        jetzt = datetime.now(timezone.utc)
        return h_sonne.render(h_sonne.berechne(*koord, jetzt), jetzt, self.settings.tz_offset_h)

    async def cmd_spot(self, arg: str, sender: str) -> str | None:
        assoc = (arg.strip() or "OE").upper()
        jetzt = datetime.now(timezone.utc)
        if assoc in self.cache_spot:
            return h_spot.render(self.cache_spot[assoc], jetzt, assoc)
        try:
            alle = await self._mit_retry(h_spot.fetch, self.settings.sota_spots_url)
        except Exception:
            alt = self.stale.get(f"spot:{assoc}")
            return h_spot.render(alt, jetzt, assoc) if alt else "SOTA: Quelle nicht erreichbar"
        spots = h_spot.filtern(alle, assoc)
        self.cache_spot[assoc] = spots
        self.stale[f"spot:{assoc}"] = spots
        return h_spot.render(spots, jetzt, assoc)

    async def cmd_lawine(self, arg: str, sender: str) -> str | None:
        heute = datetime.now(timezone.utc).date()
        if "heute" in self.cache_lawine:
            return h_lawine.render(self.cache_lawine["heute"])
        try:
            bulletins = await self._mit_retry(h_lawine.fetch, heute, self.settings.lawine_region)
        except Exception:
            alt = self.stale.get("lawine")
            return h_lawine.render(alt) if alt else "Lawine: Quelle nicht erreichbar"
        self.cache_lawine["heute"] = bulletins
        if bulletins:
            self.stale["lawine"] = bulletins
        return h_lawine.render(bulletins)

    async def cmd_netz(self, arg: str, sender: str) -> str | None:
        if "aktuell" in self.cache_netz:
            return h_netz.render(self.cache_netz["aktuell"])
        try:
            werte = await self._mit_retry(h_netz.fetch, self.settings.map_url)
        except Exception:
            alt = self.stale.get("netz")
            return h_netz.render(alt, stale=True) if alt else "Netz: Karte nicht erreichbar"
        self.cache_netz["aktuell"] = werte
        self.stale["netz"] = werte
        return h_netz.render(werte)

    async def cmd_vorhersage(self, arg: str, sender: str) -> str | None:
        koord = h_sota.parse_coords(arg)
        ort = "hier"
        if koord is None:
            treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
            if treffer is None:
                return f"Vorhersage: {arg[:16]} unbekannt"
            ort, station = treffer
            koord = (station["lat"], station["lon"])
        schluessel = f"{koord[0]:.2f},{koord[1]:.2f}"
        if schluessel in self.cache_fc:
            return h_fc.render(ort, self.cache_fc[schluessel])
        try:
            werte = await self._mit_retry(h_fc.fetch, self.settings.forecast_url, *koord)
        except Exception:
            alt = self.stale.get(f"fc:{schluessel}")
            return h_fc.render(ort, alt, stale=True) if alt else "Vorhersage: Quelle nicht erreichbar"
        self.cache_fc[schluessel] = werte
        self.stale[f"fc:{schluessel}"] = werte
        return h_fc.render(ort, werte)

    async def cmd_zeit(self, arg: str, sender: str) -> str:
        jetzt = datetime.now(timezone.utc)
        return f"UTC {jetzt:%d.%m.%Y %H:%M:%S} (Epoch {int(jetzt.timestamp())})"

    async def cmd_ping(self, arg: str, sender: str) -> str:
        return f"{self.settings.bot_name} OK, up {self.router.uptime()}, {self.router.served} cmds"

    HILFE = {
        "wx": "!wx <ort|lat lon> Wetter der naechsten Station. Tippfehler egal",
        "vorhersage": "!vorhersage <ort|lat lon> Spanne, Regen und Boeen der naechsten 24h",
        "uwz": "!uwz amtliche Warnungen fuer Kaernten",
        "sota": "!sota <ref> Gipfeldaten. !sota <lat lon> naechster Gipfel. !spot wer ist QRV",
        "spot": "!spot [assoc] wer gerade auf einem Gipfel funkt, Vorgabe OE",
        "relais": "!relais <2m|70cm|23cm> [ort|lat lon] naechste Relais",
        "sonne": "!sonne [ort|lat lon] Auf-, Untergang, Daemmerung",
        "lawine": "!lawine Lawinenwarnstufe Kaernten (nur in der Saison)",
        "netz": "!netz Zustand des Mesh: aktive Repeater und Verkehr",
        "zeit": "!zeit UTC und Epoch-Sekunden, fuer Uhren am Node",
        "ping": "!ping Lebenszeichen des Bots, taugt auch als Reichweitentest",
        "help": "!help zeigt alle Befehle, !help <cmd> die Einzelheiten",
    }

    async def cmd_help(self, arg: str, sender: str) -> str:
        """Zweistufig, weil eine Zeile fuer zehn Befehle nicht reicht."""
        thema = arg.strip().lstrip("!").lower()
        if thema in self.HILFE:
            return self.HILFE[thema]
        return ("Cmds: !wx !vorhersage !uwz !lawine !sota !spot !relais !sonne !netz !zeit "
                "!ping. Ort immer auch als lat lon. Details: !help <cmd>")

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
                 enabled=self.router.enabled, relais=len(self.relais),
                 orte=len(self.stations), gipfel=len(self.summits))
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
