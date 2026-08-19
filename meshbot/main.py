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
from .handlers import az as h_az
from .handlers import dx as h_dx
from .handlers import geo as h_geo
from .handlers import iss as h_iss
from .handlers import lawine as h_lawine
from .handlers import mond as h_mond
from .handlers import melde as h_melde
from .handlers import netz as h_netz
from .handlers import relais as h_relais
from .handlers import sonne as h_sonne
from .handlers import qth as h_qth
from .handlers import spot as h_spot
from .handlers import wo as h_wo
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
        # Platz fuer die Landesuebersicht und die zuletzt abgefragten Positionen.
        self.cache_uwz: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_uwz_s)
        self.cache_sota: TTLCache = TTLCache(maxsize=256, ttl=settings.cache_ttl_sota_s)
        self.cache_spot: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_spot_s)
        self.cache_lawine: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_lawine_s)
        self.cache_fc: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_forecast_s)
        self.cache_netz: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_netz_s)
        self.cache_dx: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_dx_s)
        self.cache_tle: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_tle_s)
        self.cache_gelaende: TTLCache = TTLCache(maxsize=128, ttl=settings.cache_ttl_gelaende_s)
        # Zonenpolygone aendern sich nur, wenn SOTLAS sie neu rechnet.
        self.cache_az: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_az_s)
        self.stale: dict[str, Any] = {}          # letzte gute Antwort je Schlüssel
        self.router = Router(settings, {
            "wx": self.cmd_wx, "uwz": self.cmd_uwz, "sota": self.cmd_sota,
            "relais": self.cmd_relais, "ping": self.cmd_ping, "help": self.cmd_help,
            "sonne": self.cmd_sonne, "spot": self.cmd_spot, "lawine": self.cmd_lawine,
            "netz": self.cmd_netz, "vorhersage": self.cmd_vorhersage, "zeit": self.cmd_zeit,
            "wo": self.cmd_wo, "melde": self.cmd_melde, "qth": self.cmd_qth,
            "sicht": self.cmd_sicht, "hoehe": self.cmd_hoehe, "dist": self.cmd_dist,
            "dx": self.cmd_dx, "mond": self.cmd_mond, "iss": self.cmd_iss,
            "az": self.cmd_az,
        })
        self.mqtt = MqttClient(settings, on_message=self.on_message, on_admin=self.on_admin)

    # --- Befehle ---------------------------------------------------------

    async def cmd_wx(self, arg: str, sender: str) -> str | None:
        treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
        if treffer is None:
            return h_wx.render_unbekannt(arg)
        ort, station = treffer
        # Der Cache haengt an der Station, nicht am Ortsnamen: dreitausend Orte
        # teilen sich 34 Stationen, Knappenberg und Friesach sind dieselbe
        # Messung. Am Ortsnamen gecacht holt jeder Weiler die Werte neu.
        sid = station["station_id"]
        name = station.get("station")
        if sid in self.cache_wx:
            return h_wx.render(ort, self.cache_wx[sid], station=name)
        try:
            werte = await self._mit_retry(h_wx.fetch, self.settings, sid)
        except Exception:
            alt = self.stale.get(f"wx:{sid}")
            if alt is None:
                return "WX: Quelle nicht erreichbar"
            return h_wx.render(ort, alt, stale=True, station=name)
        self.cache_wx[sid] = werte
        self.stale[f"wx:{sid}"] = werte
        return h_wx.render(ort, werte, station=name)

    async def cmd_uwz(self, arg: str, sender: str) -> str | None:
        # Mit Position: genau die Gemeinde, in der man steht. Die vier festen
        # Punkte sind eine Landesuebersicht — sie sagen, dass irgendwo im
        # Gailtal gewarnt wird, nicht ob es das eigene Tal trifft.
        koord = h_sota.parse_coords(arg)
        if koord is not None:
            return await self._uwz_punkt(*koord)

        # Ortsname ueber dasselbe Verzeichnis wie !wx. Genommen wird die
        # Ortskoordinate, nicht die der Wetterstation: Noetsch misst in Bad
        # Bleiberg, aber gewarnt wird die Gemeinde, in der man wirklich steht.
        if arg.strip():
            treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
            if treffer is None:
                return h_uwz.render_unbekannt(arg)
            ort, eintrag = treffer
            return await self._uwz_punkt(eintrag["lat"], eintrag["lon"], gefragt=ort)

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

    def _uwz_kopf(self, gemeinde: str, gefragt: str | None) -> str:
        """Gemeinde dazuschreiben, wenn sie anders heisst als der gefragte Ort.

        Waidegg liegt in der Gemeinde Kirchbach — gewarnt wird immer die
        Gemeinde. Beides zu nennen ist dieselbe Ehrlichkeit wie bei `!wx`, wo
        die fremde Messstation in der Klammer steht.

        Steckt der gefragte Name schon vorne in der Gemeinde, faellt die
        Klammer weg: "Noetsch (Noetsch im Gailtal)" sagt nichts und kostet
        zwanzig Zeichen Sendezeit.
        """
        if not gefragt:
            return gemeinde
        a, b = h_wx.normalisiere(gefragt), h_wx.normalisiere(gemeinde)
        if b.startswith(a):
            return gemeinde
        return f"{gefragt.title()} ({gemeinde})"

    async def _uwz_punkt(self, lat: float, lon: float, gefragt: str | None = None) -> str:
        """Warnungen fuer eine Position, gecacht wie die Landesuebersicht.

        Der Cacheschluessel ist auf zwei Stellen gerundet: Die API antwortet
        gemeindeweise, ein Kilometer Unterschied fragt dieselbe Gemeinde ab.
        Ohne das Runden legt jede Handposition einen eigenen Eintrag an.
        """
        key = f"{lat:.2f},{lon:.2f}"
        if key in self.cache_uwz:
            ort, warnungen = self.cache_uwz[key]
            return h_uwz.render(warnungen, ort=self._uwz_kopf(ort, gefragt))
        try:
            ort, warnungen = await self._mit_retry(h_uwz.fetch_punkt, self.settings.warn_url, lat, lon)
        except Exception:
            alt = self.stale.get(f"uwz:{key}")
            if alt is None:
                return "UWZ: Quelle nicht erreichbar"
            return h_uwz.render(alt[1], stale=True, ort=self._uwz_kopf(alt[0], gefragt))
        self.cache_uwz[key] = (ort, warnungen)
        self.stale[f"uwz:{key}"] = (ort, warnungen)
        return h_uwz.render(warnungen, ort=self._uwz_kopf(ort, gefragt))

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

    async def cmd_az(self, arg: str, sender: str) -> str:
        """Liegt die Position in der SOTA-Aktivierungszone?

        Geprueft werden bis zu drei Gipfel, naechster zuerst: Zwischen zwei
        Gipfeln kann der naechstgelegene der falsche sein, und die Frage
        lautet "bin ich in *einer* Zone", nicht "in der des naechsten".
        """
        koord = h_sota.parse_coords(arg)
        if koord is None:
            return "!az <lat lon> — liegt die Position in der SOTA-Zone?"

        nah = [g for g in h_sota.nearest(self.summits, *koord, limit=h_az.MAX_GIPFEL)
               if g["_d"] <= h_az.MAX_ENTFERNUNG_KM]
        if not nah:
            weit = h_sota.nearest(self.summits, *koord, limit=1)
            return h_az.render_kein_gipfel(weit[0]["_d"] if weit else None)

        letzte: str | None = None
        for gipfel in nah:
            ref = gipfel["ref"]
            if ref in self.cache_az:
                ringe = self.cache_az[ref]
            else:
                try:
                    ringe = await h_az.fetch_zone(self.http, ref)
                except h_az.KeineZone:
                    letzte = letzte or h_az.render_keine_zone(gipfel)
                    continue
                except Exception:
                    return "AZ: SOTLAS nicht erreichbar"
                self.cache_az[ref] = ringe
            urteil = h_az.bewerte(gipfel, koord, ringe)
            if urteil["drin"]:
                return h_az.render(urteil)
            # Kein Treffer: das naechstgelegene NEIN ist die beste Auskunft,
            # falls auch die weiteren Gipfel nichts liefern.
            letzte = letzte or h_az.render(urteil)
        return letzte or h_az.render_kein_gipfel(None)

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

    async def cmd_wo(self, arg: str, sender: str) -> str | None:
        if not arg.strip():
            return "!wo <name> — Zustand eines Knotens"
        jetzt = datetime.now(timezone.utc)
        if "nodes" not in self.cache_netz:
            try:
                self.cache_netz["nodes"] = await self._mit_retry(h_wo.fetch, self.settings.map_url)
            except Exception:
                alt = self.stale.get("nodes")
                if not alt:
                    return "Node: Karte nicht erreichbar"
                self.cache_netz["nodes"] = alt
        nodes = self.cache_netz["nodes"]
        self.stale["nodes"] = nodes
        return h_wo.render(arg, h_wo.suche(nodes, arg), jetzt)

    async def cmd_melde(self, arg: str, sender: str) -> str | None:
        if len(arg.strip()) < 4:
            return "!melde <was, wo> — Luecke oder Stoerung melden"
        meldung = h_melde.erfassen(arg, sender, datetime.now(timezone.utc))
        nummer = h_melde.speichern(meldung, self.settings.meldungen_datei)
        # Auch auf MQTT, damit andere Dienste daraus etwas machen koennen.
        self.mqtt.publish(self.settings.topic_meldung, json.dumps({**meldung, "nr": nummer}, ensure_ascii=False))
        log.info("meldung", nr=nummer, von=sender, text=meldung["text"][:60])
        return h_melde.render(meldung, nummer)

    async def cmd_qth(self, arg: str, sender: str) -> str:
        koord = h_sota.parse_coords(arg)
        if koord is not None:
            return h_qth.render_koord(*koord)
        loc = arg.strip()
        if not loc:
            return "!qth <locator|lat lon> — Locator umrechnen"
        return h_qth.render_locator(loc, h_qth.from_locator(loc))

    async def cmd_zeit(self, arg: str, sender: str) -> str:
        jetzt = datetime.now(timezone.utc)
        return f"UTC {jetzt:%d.%m.%Y %H:%M:%S} (Epoch {int(jetzt.timestamp())})"

    async def cmd_ping(self, arg: str, sender: str) -> str:
        return f"{self.settings.bot_name} OK, up {self.router.uptime()}, {self.router.served} cmds"

    # --- Standort und Gelaende ----------------------------------------

    async def cmd_sicht(self, arg: str, sender: str) -> str:
        """Funkstrecke zwischen zwei Punkten pruefen.

        Der teuerste Befehl im Bot: eine Hoehenabfrage ueber 85 Punkte. Das
        Ergebnis wird eine Woche lang behalten -- das Gelaende aendert sich
        nicht, und dieselbe Strecke wird erfahrungsgemaess mehrfach gefragt.
        """
        punkte = h_geo.parse_punkte(arg, 2)
        if punkte is None:
            return "!sicht <lat,lon> <lat,lon> — zwei Positionen noetig"
        a, b = punkte
        dist = h_geo.distanz_km(a, b)
        if dist < 0.2:
            return "Sicht: die beiden Punkte sind praktisch derselbe"
        if dist > 200:
            return f"Sicht: {dist:.0f}km ist zu weit fuer eine sinnvolle Rechnung"

        schluessel = f"{a[0]:.4f},{a[1]:.4f}>{b[0]:.4f},{b[1]:.4f}"
        if schluessel in self.cache_gelaende:
            return h_geo.render_sicht(self.cache_gelaende[schluessel])
        n = self.settings.sicht_punkte
        strecke = [h_geo.zwischenpunkt(a, b, i / (n - 1)) for i in range(n)]
        try:
            hoehen = await h_geo.hoehen(self.http, self.settings.topo_url, strecke)
        except Exception:
            return "Sicht: Hoehenmodell nicht erreichbar"
        mast = self.settings.sicht_mast_m
        eng = h_geo.bewerte_profil(hoehen, dist, mast, mast)
        self.cache_gelaende[schluessel] = eng
        return h_geo.render_sicht(eng)

    async def cmd_hoehe(self, arg: str, sender: str) -> str:
        punkte = h_geo.parse_punkte(arg, 1)
        if punkte is None:
            return "!hoehe <lat,lon> — Gelaendehoehe an einer Position"
        p = punkte[0]
        schluessel = f"h:{p[0]:.4f},{p[1]:.4f}"
        if schluessel in self.cache_gelaende:
            return h_geo.render_hoehe(p, self.cache_gelaende[schluessel])
        try:
            meter = (await h_geo.hoehen(self.http, self.settings.topo_url, [p]))[0]
        except Exception:
            return "Hoehe: Hoehenmodell nicht erreichbar"
        self.cache_gelaende[schluessel] = meter
        return h_geo.render_hoehe(p, meter)

    async def cmd_dist(self, arg: str, sender: str) -> str:
        """Reine Rechnung, keine Quelle, keine Wartezeit."""
        punkte = h_geo.parse_punkte(arg, 2)
        if punkte is None:
            return "!dist <lat,lon> <lat,lon> — Entfernung und Peilung"
        return h_geo.render_dist(*punkte)

    # --- Himmel ---------------------------------------------------------

    async def cmd_dx(self, arg: str, sender: str) -> str:
        if "aktuell" in self.cache_dx:
            return h_dx.render(self.cache_dx["aktuell"])
        try:
            werte = await self._mit_retry(h_dx.fetch, self.settings.hamqsl_url)
        except Exception:
            alt = self.stale.get("dx")
            return h_dx.render(alt) if alt else "DX: Quelle nicht erreichbar"
        self.cache_dx["aktuell"] = werte
        self.stale["dx"] = werte
        return h_dx.render(werte)

    async def cmd_mond(self, arg: str, sender: str) -> str:
        koord = h_sota.parse_coords(arg)
        if koord is None:
            treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
            koord = (treffer[1]["lat"], treffer[1]["lon"]) if treffer else (46.61, 13.86)
        jetzt = datetime.now(timezone.utc)
        werte = h_mond.ereignisse(jetzt.date(), *koord)
        return h_mond.render(werte, self.settings.tz_offset_h)

    async def cmd_iss(self, arg: str, sender: str) -> str:
        koord = h_sota.parse_coords(arg)
        if koord is None:
            koord = (46.61, 13.86)
        alt = False
        if "tle" in self.cache_tle:
            tle = self.cache_tle["tle"]
        else:
            try:
                tle = await self._mit_retry(h_iss.fetch_tle, self.settings.tle_url)
                self.cache_tle["tle"] = tle
                self.stale["tle"] = tle
            except Exception:
                tle = self.stale.get("tle")
                if tle is None:
                    return "ISS: Bahndaten nicht erreichbar"
                alt = True                     # gealterte TLE, Zeiten ungenauer
        jetzt = datetime.now(timezone.utc)
        # Die Bahnrechnung ist reine CPU-Arbeit und blockiert sonst die Schleife.
        ueberflug = await asyncio.to_thread(h_iss.naechster_ueberflug, tle, *koord, jetzt)
        return h_iss.render(ueberflug, self.settings.tz_offset_h, alt)

    HILFE = {
        "wx": "!wx <ort|lat lon> Wetter der naechsten Station. Tippfehler egal",
        "vorhersage": "!vorhersage <ort|lat lon> Spanne, Regen und Boeen der naechsten 24h",
        "uwz": "!uwz [ort|lat lon] amtliche Warnungen der Gemeinde. Ohne Angabe ganz Kaernten",
        "sota": "!sota <ref> Gipfeldaten. !sota <lat lon> naechster Gipfel. !spot wer ist QRV",
        "az": "!az <lat lon> stehst du in der SOTA-Aktivierungszone? Polygon von SOTLAS",
        "spot": "!spot [assoc] wer gerade auf einem Gipfel funkt, Vorgabe OE",
        "relais": "!relais <2m|70cm|23cm> [ort|lat lon] naechste Relais",
        "sonne": "!sonne [ort|lat lon] Auf-, Untergang, Daemmerung",
        "lawine": "!lawine Lawinenwarnstufe Kaernten (nur in der Saison)",
        "netz": "!netz Zustand des Mesh: aktive Repeater und Verkehr",
        "zeit": "!zeit UTC und Epoch-Sekunden, fuer Uhren am Node",
        "ping": "!ping Lebenszeichen des Bots, taugt auch als Reichweitentest",
        "help": "!help zeigt alle Befehle, !help <cmd> die Einzelheiten",
        "wo": "!wo <name> Position, Verkehr und letzter Empfang eines Knotens",
        "melde": "!melde <was, wo> Luecke oder Stoerung melden, Position mitschicken",
        "qth": "!qth <locator|lat lon> Maidenhead in Koordinaten und zurueck",
        "sicht": "!sicht <lat,lon> <lat,lon> Funkstrecke pruefen: frei, knapp oder blockiert",
        "hoehe": "!hoehe <lat,lon> Gelaendehoehe aus dem 25m-Modell",
        "dist": "!dist <lat,lon> <lat,lon> Entfernung, Peilung und Gegenpeilung",
        "dx": "!dx Kurzwellenbedingungen: Sonnenfluss, A- und K-Index",
        "mond": "!mond [ort|lat lon] Auf-, Untergang und Phase",
        "iss": "!iss [lat lon] naechster Ueberflug der Raumstation ueber 10 Grad",
    }

    # Gruppen fuer die zweite Hilfestufe. Die Reihenfolge ist die der Uebersicht.
    GRUPPEN = {
        "wetter": ["wx", "vorhersage", "uwz", "lawine"],
        "berg": ["sota", "az", "spot", "sonne", "mond"],
        "standort": ["sicht", "hoehe", "dist", "qth"],
        "netz": ["netz", "wo", "relais", "ping"],
        "sonst": ["dx", "iss", "zeit", "melde"],
    }

    async def cmd_help(self, arg: str, sender: str) -> str:
        """Dreistufig: Einzelbefehl, Gruppe, Uebersicht."""
        thema = arg.strip().lstrip("!").lower()
        if thema in self.HILFE:
            return self.HILFE[thema]
        if thema in self.GRUPPEN:
            return f"{thema.title()}: " + " ".join("!" + c for c in self.GRUPPEN[thema])
        return self._uebersicht()

    def _uebersicht(self) -> str:
        """Alle Befehle in eine Nachricht — solange sie hineinpassen.

        Die flache Liste ist die bessere Antwort: Wer !help tippt, will sehen
        was es gibt, nicht erst ein Menue durchklicken. Sie waechst aber mit
        jedem Befehl. Passt sie nicht mehr, faellt die Antwort automatisch auf
        die Gruppennamen zurueck, statt am Zeichenlimit abgeschnitten zu werden.
        """
        alle = [c for gruppe in self.GRUPPEN.values() for c in gruppe]
        grenze = self.settings.nutzlimit
        # Von der schoensten zur kuerzesten Form, erste die passt gewinnt.
        for kandidat in (" ".join("!" + c for c in alle) + " | !help <cmd>",
                         " ".join(alle) + " !help <cmd>",
                         " ".join(alle),
                         "Themen: " + " ".join(self.GRUPPEN) + " | !help <thema>"):
            if len(kandidat) <= grenze:
                return kandidat
        return "!help <thema>: " + " ".join(self.GRUPPEN)

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
                 orte=len(self.stations.get("orte", {})),
                 stationen=len(self.stations.get("stationen", [])),
                 gipfel=len(self.summits))
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
