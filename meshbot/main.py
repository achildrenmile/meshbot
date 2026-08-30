"""Entry point: connect MQTT, wire up handlers, shut down cleanly."""

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
from .handlers import frag as h_frag
from .handlers import geo as h_geo
from .handlers import ortsuche as h_ort
from .handlers import iss as h_iss
from .handlers import quota as h_quota
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
from .handlers import warn as h_warn
from .handlers import wx as h_wx
from .handlers import wxberg as h_berg
from .health import serve_health
from .mqtt_client import MqttClient
from .ratelimit import SenderLimiter, TokenBucket
from .router import ALIASES, Router
from .version import VERSION
from . import version as v_mod

log = structlog.get_logger(__name__)


class Bot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=settings.http_timeout_s, headers={"User-Agent": f"MeshBot/{VERSION} (CarinthiaMesh)"})
        self.stations = h_wx.load_stations(settings)
        self.relais = h_relais.load_relais(settings.relais_file)
        self.summits = h_sota.load_summits(settings.summits_file)
        # Name directory of the summits, built once at startup.
        self.gipfel_index = h_berg.index(self.summits)
        self.cache_wx: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_wx_s)
        self.cache_berg: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_wx_s)
        # Room for the state overview and the most recently queried positions.
        self.cache_warn: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_warn_s)
        self.cache_sota: TTLCache = TTLCache(maxsize=256, ttl=settings.cache_ttl_sota_s)
        self.cache_spot: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_spot_s)
        self.cache_lawine: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl_lawine_s)
        self.cache_fc: TTLCache = TTLCache(maxsize=32, ttl=settings.cache_ttl_forecast_s)
        self.cache_netz: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_netz_s)
        self.cache_dx: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_dx_s)
        self.cache_tle: TTLCache = TTLCache(maxsize=2, ttl=settings.cache_ttl_tle_s)
        self.cache_gelaende: TTLCache = TTLCache(maxsize=128, ttl=settings.cache_ttl_gelaende_s)
        # Place lookup: once found, stays found -- places do not move. The cache
        # holds the candidate list, not the selection: the same list serves both
        # the exact and the guessed stage.
        self.cache_geo: TTLCache = TTLCache(maxsize=256, ttl=settings.cache_ttl_geo_s)
        # Zone polygons only change when SOTLAS recomputes them.
        self.cache_az: TTLCache = TTLCache(maxsize=64, ttl=settings.cache_ttl_az_s)
        # Keyed on the normalised question. On a channel the same thing gets
        # asked repeatedly, and a cached answer costs neither CPU on rag-node-01
        # nor a slot in the daily allowance.
        self.cache_frag: TTLCache = TTLCache(maxsize=128, ttl=settings.cache_ttl_frag_s)
        # Brakes of !frag's own, on top of the ones in the router that every
        # command passes. Reason for the second set: a weather lookup is one HTTP
        # request, an AI answer is seconds of CPU on a machine that also runs
        # k3s. The same allowance for both would be the wrong trade.
        self.frag_sender = SenderLimiter(settings.frag_sender_limit,
                                         settings.frag_sender_window_s)
        # The daily ceiling. A token bucket rather than a counter, so it refills
        # gradually instead of releasing a hundred grants at midnight.
        self.frag_tag = TokenBucket(settings.frag_tageslimit, 86400)
        self.frag_enabled = settings.frag_enabled
        # None = not probed yet. Purely informational: it is reported on
        # /healthz and logged on change, but it never makes the container
        # unhealthy. A dead inference host is not a reason to restart the bot --
        # every other command still works.
        self.frag_erreichbar: bool | None = None
        self.stale: dict[str, Any] = {}          # letzte gute Antwort je Schlüssel
        self.router = Router(settings, {
            "wx": self.cmd_wx, "warn": self.cmd_warn, "sota": self.cmd_sota,
            "relais": self.cmd_relais, "ping": self.cmd_ping, "help": self.cmd_help,
            "sonne": self.cmd_sonne, "spot": self.cmd_spot, "lawine": self.cmd_lawine,
            "netz": self.cmd_netz, "vorhersage": self.cmd_vorhersage, "zeit": self.cmd_zeit,
            "wo": self.cmd_wo, "melde": self.cmd_melde, "qth": self.cmd_qth,
            "sicht": self.cmd_sicht, "hoehe": self.cmd_hoehe, "dist": self.cmd_dist,
            "dx": self.cmd_dx, "mond": self.cmd_mond, "iss": self.cmd_iss,
            "az": self.cmd_az, "quota": self.cmd_quota, "gipfel": self.cmd_gipfel,
            "version": self.cmd_version, "frag": self.cmd_frag,
        })
        # The gate's last quota state. Arrives retained on subscribe.
        self.quota: dict[str, Any] | None = None
        self.mqtt = MqttClient(settings, on_message=self.on_message,
                               on_admin=self.on_admin, on_quota=self.on_quota)

    # --- Commands --------------------------------------------------------

    async def cmd_wx(self, arg: str, sender: str) -> str | None:
        """Places worldwide -- measured before computed, certain before guessed.

        Five stages, and the order is the whole trick:

        1. **Carinthia exact** -- one of the 34 stations measures. Nothing beats that.
        2. **Certain summit** -- `!wx goldeck` was announced that way.
        3. **Place lookup exact** -- Lienz, Hamburg, Ljubljana. Model value.
        4. **Carinthia guessed** -- the typo `vilach`, with a question mark.
        5. **Place lookup guessed** -- last attempt, marked as well.

        Why 3 before 4: an exact match should beat a guessed one, across the
        border too. Otherwise "Hamburg" turns back into "Haimburg". Why 4 before
        5: the same in reverse -- without it the typo "vilach" becomes the
        Spanish Vilachá, population five.

        What is deliberately **missing** here is the similarity search across
        9442 summits. It turned "Lienz" into "Sandegg - Lienzer". Whoever wants
        a mountain uses !gipfel.
        """
        treffer = h_wx.resolve_place_stufe(arg, self.stations,
                                           self.settings.default_location, fuzzy=False)
        if treffer is None:
            berg, _ = h_berg.suche_stufe(self.gipfel_index, arg, fuzzy=False)
            if berg is not None:
                return await self._wx_gipfel(berg)
            fremd = await self._wx_fremd(arg, nur_exakt=True)
            if fremd is not None:
                return fremd
            treffer = h_wx.resolve_place_stufe(arg, self.stations,
                                               self.settings.default_location)
        if treffer is None:
            fremd = await self._wx_fremd(arg, nur_exakt=False)
            return fremd if fremd is not None else h_wx.render_unbekannt(arg)
        ort, station, stufe = treffer
        # The cache hangs off the station, not the place name: three thousand
        # places share 34 stations, and Knappenberg and Friesach are the same
        # measurement. Cached by place name, every hamlet refetches the values.
        sid = station["station_id"]
        name = station.get("station")
        anzeige = station.get("anzeige")
        geraten = stufe == "geraten"
        if sid in self.cache_wx:
            return h_wx.render(ort, self.cache_wx[sid], station=name, geraten=geraten,
                                anzeige=anzeige)
        try:
            werte = await self._mit_retry(h_wx.fetch, self.settings, sid)
        except Exception:
            alt = self.stale.get(f"wx:{sid}")
            if alt is None:
                return "WX: Quelle nicht erreichbar"
            return h_wx.render(ort, alt, stale=True, station=name, geraten=geraten,
                                anzeige=anzeige)
        self.cache_wx[sid] = werte
        self.stale[f"wx:{sid}"] = werte
        return h_wx.render(ort, werte, station=name, geraten=geraten, anzeige=anzeige)

    async def _wx_fremd(self, arg: str, nur_exakt: bool) -> str | None:
        """Place outside Carinthia: look up the position, fetch the model value.

        Returns `None` when nothing was found **or** the source was unreachable
        -- `cmd_wx` then runs its next stage. An outage of the place lookup must
        not leave a Carinthian typo unanswered.
        """
        if not arg.strip() or h_sota.parse_coords(arg) is not None:
            return None
        schluessel = h_wx.normalisiere(arg)
        kandidaten = self.cache_geo.get(schluessel)
        if kandidaten is None:
            try:
                kandidaten = await self._mit_retry(h_ort.suche_ort,
                                                   self.settings.geocode_url, arg)
            except Exception:
                return None
            self.cache_geo[schluessel] = kandidaten
        ort = h_ort.waehle(kandidaten, arg, nur_exakt=nur_exakt)
        if ort is None:
            return None

        # Values cached per place, not per query: "Wien" and "wien " are the
        # same place, and the lookup has already merged them.
        ref = f"{ort['latitude']:.3f},{ort['longitude']:.3f}"
        geraten = not nur_exakt
        if ref in self.cache_berg:
            return h_ort.render(ort, self.cache_berg[ref], geraten=geraten)
        try:
            werte = await self._mit_retry(h_ort.fetch, self.settings.berg_url, ort)
        except Exception:
            alt = self.stale.get(f"geo:{ref}")
            if alt is None:
                return None
            return h_ort.render(ort, alt, stale=True, geraten=geraten)
        self.cache_berg[ref] = werte
        self.stale[f"geo:{ref}"] = werte
        return h_ort.render(ort, werte, geraten=geraten)

    async def cmd_version(self, arg: str, sender: str) -> str | None:
        """Which build is on the air -- and what is new about it.

        Two lines would be nicer, but there is only one. Hence the number and
        **one** change: otherwise whoever reports "this stopped working" and
        whoever answers "works for me" are talking about two different bots.
        Everything else is in the wiki.
        """
        return v_mod.render()

    async def cmd_gipfel(self, arg: str, sender: str) -> str | None:
        """Summit weather, without the detour through the place directory.

        The separate command is the whole point: in `!wx`, places and mountains
        had to share one search, and every loosening that found a mountain bent
        a place name out of shape -- or the other way round. Kept apart,
        `!gipfel` may guess, because here it is established that a mountain is
        meant. And when it guesses, it says so.
        """
        if not arg.strip():
            return self.usage("gipfel")
        berg, _ = h_berg.suche_stufe(self.gipfel_index, arg, fuzzy=False)
        if berg is not None:
            return await self._wx_gipfel(berg)
        # What the SOTA list does not carry, the place lookup often does: the
        # Petzen is there as "Peca" (2125 m), the Koschuta as "Koschutnikturm".
        # Only after that comes guessing -- a foreign summit out of the
        # similarity search is the worst of all answers.
        fremd = await self._wx_fremd(arg, nur_exakt=True)
        if fremd is not None:
            return fremd
        berg, stufe = h_berg.suche_stufe(self.gipfel_index, arg)
        if berg is None:
            return h_berg.render_unbekannt(arg)
        return await self._wx_gipfel(berg, geraten=stufe == "geraten")

    async def _wx_gipfel(self, berg: dict[str, Any], geraten: bool = False) -> str:
        """Model weather for a summit.

        Cached separately from the station values: both live ten minutes, but
        the key is the SOTA reference and not the station id.
        """
        # On a few mountains somebody really measures. Then the measurement
        # wins, not the model -- and the station name is named, as everywhere.
        st = h_berg.station_am_gipfel(self.stations.get("stationen", []), berg)
        if st is not None:
            name = h_berg.kurzname(berg["name"])
            sid = st["id"]
            if sid in self.cache_wx:
                return h_wx.render(name, self.cache_wx[sid], station=st["name"],
                                   geraten=geraten)
            try:
                werte = await self._mit_retry(h_wx.fetch, self.settings, sid)
            except Exception:
                alt = self.stale.get(f"wx:{sid}")
                if alt is not None:
                    return h_wx.render(name, alt, stale=True, station=st["name"],
                                       geraten=geraten)
            else:
                self.cache_wx[sid] = werte
                self.stale[f"wx:{sid}"] = werte
                return h_wx.render(name, werte, station=st["name"], geraten=geraten)

        ref = berg["ref"]
        if ref in self.cache_berg:
            return h_berg.render(berg, self.cache_berg[ref], geraten=geraten)
        try:
            werte = await self._mit_retry(h_berg.fetch, self.settings.berg_url, berg)
        except Exception:
            alt = self.stale.get(f"berg:{ref}")
            if alt is None:
                return f"WX {berg['name'][:20]}: Modell nicht erreichbar"
            return h_berg.render(berg, alt, stale=True, geraten=geraten)
        self.cache_berg[ref] = werte
        self.stale[f"berg:{ref}"] = werte
        return h_berg.render(berg, werte, geraten=geraten)

    async def cmd_warn(self, arg: str, sender: str) -> str | None:
        # With a position: exactly the municipality you are standing in. The
        # four fixed points are a state overview — they say that a warning is
        # out somewhere in the Gailtal, not whether it hits your own valley.
        koord = h_sota.parse_coords(arg)
        if koord is not None:
            return await self._warn_punkt(*koord)

        # Place name via the same directory as !wx. The place coordinate is
        # used, not the station's: Nötsch measures in Bad Bleiberg, but the
        # warning applies to the municipality you are actually standing in.
        if arg.strip():
            treffer = h_wx.resolve_place(arg, self.stations, self.settings.default_location)
            if treffer is None:
                return h_warn.render_unbekannt(arg)
            ort, eintrag = treffer
            return await self._warn_punkt(eintrag["lat"], eintrag["lon"], gefragt=ort)

        if "aktuell" in self.cache_warn:
            return h_warn.render(self.cache_warn["aktuell"])
        try:
            warnungen = await h_warn.fetch(self.http, self.settings.warn_url)
        except Exception:
            alt = self.stale.get("warn")
            return h_warn.render(alt, stale=True) if alt is not None else "WARN: Quelle nicht erreichbar"
        self.cache_warn["aktuell"] = warnungen
        self.stale["warn"] = warnungen
        return h_warn.render(warnungen)

    def _warn_kopf(self, gemeinde: str, gefragt: str | None) -> str:
        """Name the municipality when it differs from the place asked for.

        Waidegg lies in the municipality of Kirchbach — warnings always apply to
        the municipality. Naming both is the same honesty as in `!wx`, where the
        foreign measuring station goes in the parentheses.

        If the name asked for is already the start of the municipality, the
        parentheses are dropped: "Nötsch (Nötsch im Gailtal)" says nothing and
        costs twenty characters of airtime.
        """
        if not gefragt:
            return gemeinde
        a, b = h_wx.normalisiere(gefragt), h_wx.normalisiere(gemeinde)
        if b.startswith(a):
            return gemeinde
        return f"{gefragt.title()} ({gemeinde})"

    async def _warn_punkt(self, lat: float, lon: float, gefragt: str | None = None) -> str:
        """Warnings for a position, cached like the state overview.

        The cache key is rounded to two decimals: the API answers per
        municipality, and a kilometre of difference queries the same one.
        Without the rounding every handheld position creates its own entry.
        """
        key = f"{lat:.2f},{lon:.2f}"
        if key in self.cache_warn:
            ort, warnungen = self.cache_warn[key]
            return h_warn.render(warnungen, ort=self._warn_kopf(ort, gefragt))
        try:
            ort, warnungen = await self._mit_retry(h_warn.fetch_punkt, self.settings.warn_url, lat, lon)
        except Exception:
            alt = self.stale.get(f"warn:{key}")
            if alt is None:
                return "WARN: Quelle nicht erreichbar"
            return h_warn.render(alt[1], stale=True, ort=self._warn_kopf(alt[0], gefragt))
        self.cache_warn[key] = (ort, warnungen)
        self.stale[f"warn:{key}"] = (ort, warnungen)
        return h_warn.render(warnungen, ort=self._warn_kopf(ort, gefragt))

    async def cmd_sota(self, arg: str, sender: str) -> str | None:
        if not arg.strip():
            return self.usage("sota")
        # Position instead of reference: on a summit one rarely knows the
        # reference, but the device knows the coordinates.
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
        """Is the position inside the SOTA activation zone?

        Up to three summits are checked, nearest first: between two summits the
        nearest one can be the wrong one, and the question is "am I in *a*
        zone", not "in the nearest one's".
        """
        koord = h_sota.parse_coords(arg)
        if koord is None:
            return self.usage("az")

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
            # No hit: the nearest NO is the best information available, in case
            # the remaining summits yield nothing either.
            letzte = letzte or h_az.render(urteil)
        return letzte or h_az.render_kein_gipfel(None)

    async def cmd_quota(self, arg: str, sender: str) -> str:
        """How many transmissions are left — gate and bot side by side.

        `verfuegbar()` instead of `allow()`: checking must not spend anything.
        The answer itself still costs one transmission, and says so.
        """
        return h_quota.render(
            self.quota,
            self.router.global_bucket.verfuegbar(),
            self.settings.global_limit,
            self.settings.global_window_s,
        )

    async def cmd_relais(self, arg: str, sender: str) -> str | None:
        teile = arg.split(maxsplit=1)
        band = (teile[0] if teile else "2m").lower()
        if band not in h_relais.BAENDER:
            return self.usage("relais")
        ort_arg = teile[1] if len(teile) > 1 else self.settings.default_location

        # With a position no place name is needed — "hier" is shorter and more
        # honest than the name of the nearest weather station.
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
            return self.usage("wo")
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
        return h_wo.antwort(nodes, arg, jetzt)

    async def cmd_melde(self, arg: str, sender: str) -> str | None:
        if len(arg.strip()) < 4:
            return self.usage("melde")
        meldung = h_melde.erfassen(arg, sender, datetime.now(timezone.utc))
        nummer = h_melde.speichern(meldung, self.settings.meldungen_datei)
        # On MQTT as well, so other services can do something with it.
        self.mqtt.publish(self.settings.topic_meldung, json.dumps({**meldung, "nr": nummer}, ensure_ascii=False))
        log.info("meldung", nr=nummer, von=sender, text=meldung["text"][:60])
        return h_melde.render(meldung, nummer)

    async def cmd_qth(self, arg: str, sender: str) -> str:
        koord = h_sota.parse_coords(arg)
        if koord is not None:
            return h_qth.render_koord(*koord)
        loc = arg.strip()
        if not loc:
            return self.usage("qth")
        return h_qth.render_locator(loc, h_qth.from_locator(loc))

    async def cmd_zeit(self, arg: str, sender: str) -> str:
        jetzt = datetime.now(timezone.utc)
        return f"UTC {jetzt:%d.%m.%Y %H:%M:%S} (Epoch {int(jetzt.timestamp())})"

    async def cmd_ping(self, arg: str, sender: str) -> str:
        return f"{self.settings.bot_name} OK, up {self.router.uptime()}, {self.router.served} cmds"

    # --- Location and terrain --------------------------------------------

    async def cmd_sicht(self, arg: str, sender: str) -> str:
        """Check the radio path between two points.

        The most expensive command in the bot: an elevation query over 85
        points. The result is kept for a week -- the terrain does not change,
        and experience shows the same path gets asked about repeatedly.
        """
        punkte = h_geo.parse_punkte(arg, 2)
        if punkte is None:
            return self.usage("sicht")
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
            return self.usage("hoehe")
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
        """Pure arithmetic, no source, no waiting."""
        punkte = h_geo.parse_punkte(arg, 2)
        if punkte is None:
            return self.usage("dist")
        return h_geo.render_dist(*punkte)

    # --- Sky -------------------------------------------------------------

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
        # The orbit computation is pure CPU work and would block the loop.
        ueberflug = await asyncio.to_thread(h_iss.naechster_ueberflug, tle, *koord, jetzt)
        return h_iss.render(ueberflug, self.settings.tz_offset_h, alt)

    async def cmd_frag(self, arg: str, sender: str) -> str | None:
        """A free question, answered by the model on rag-node-01.

        Order of the gates is the point. The cache comes **before** the
        allowances: a repeat of a question already answered costs neither CPU
        nor a slot, so charging for it would only make the command feel broken.
        Everything after that is paid for.

        Every path out of here that is not an answer is silence -- there is no
        "the AI is down" on the air. A refusal costs the same airtime as an
        answer, and this bot spends that on !wx instead.
        """
        if not self.frag_enabled:
            return None
        frage = " ".join(arg.split())[: self.settings.frag_frage_max]
        if not frage:
            return self.usage("frag")

        schluessel = frage.lower()
        if schluessel in self.cache_frag:
            return self.cache_frag[schluessel]

        if not self.frag_sender.allow(sender):
            log.info("frag_absenderlimit", sender=sender)
            return None
        if not self.frag_tag.allow():
            log.info("frag_tageslimit", sender=sender)
            return None

        try:
            roh = await h_frag.fetch(self.http, self.settings, frage)
            antwort = h_frag.render(roh, self.settings.frag_praefix,
                                    h_frag.budget(self.settings))
        except Exception as exc:
            log.warning("frag_fehler", sender=sender, frage=frage, error=str(exc))
            return None

        # Logged in full, unlike every other command. The router's line truncates
        # the argument to 24 characters, which is right for `!wx villach` and
        # wrong here: this went out over the operator's callsign, and afterwards
        # somebody has to be able to say what was asked and what was answered.
        #
        # `modellantwort` is what came back, **before** the router clamps it --
        # calling it "antwort" would be a lie, because on a long answer the two
        # differ. What actually went on the air is logged by `on_message` under
        # exactly that name; the pair of lines is the complete record.
        log.info("frag", sender=sender, frage=frage, modellantwort=antwort)

        echt = await self._frag_ausfuehren(antwort, sender)
        if echt is not None:
            # Bewusst **nicht** zwischengespeichert: das ist ein Messwert, und
            # der ist in einer Stunde ein anderer. Gecacht wird nur, was das
            # Modell aus sich heraus gesagt hat.
            return echt

        self.cache_frag[schluessel] = antwort
        return antwort

    # Befehle, deren Argument in Klartext ankommen darf. !dist und !hoehe
    # fehlen mit Absicht: die wollen Koordinaten, und "!dist villach
    # klagenfurt" liefert nur die Verwendungszeile -- schlechter als der
    # Verweis, den das Modell ohnehin geschrieben hat.
    FRAG_AUSFUEHRBAR = {"netz", "gipfel", "wx"}

    async def _frag_ausfuehren(self, antwort: str, sender: str) -> str | None:
        """Nennt die Modellantwort einen Befehl, fuehre ihn aus.

        Das Modell weiss, welches Werkzeug gefragt waere -- es schreibt "Frag
        !netz, das zaehlt nach" -- kann es aber nicht bedienen. Der Bot kann.
        Statt den Fragenden auf eine zweite Nachricht zu schicken, kommt die
        gemessene Antwort gleich zurueck.

        Und zwar **ohne** das KI:-Praefix: was hier hinausgeht, hat eine
        Messstation oder die Karte geliefert, nicht das Modell. Das Praefix
        trennt Geratenes von Gemessenem, und hier ist nichts geraten.

        Jeder Zweifelsfall faellt auf die Modellantwort zurueck, nie ins Leere.
        """
        ziel = h_frag.verweis(antwort)
        if ziel is None:
            return None
        getippt, argument = ziel
        name = ALIASES.get(getippt)
        if name not in self.FRAG_AUSFUEHRBAR:
            return None
        handler = self.router.handlers.get(name)
        if handler is None:
            return None
        try:
            echt = await handler(argument, sender)
        except Exception as exc:
            log.warning("frag_ausfuehrung_fehler", cmd=name, arg=argument, error=str(exc))
            return None
        # Eine Verwendungszeile ist keine Antwort: sie beginnt mit "!" und sagt
        # dem Fragenden nur, wie der Befehl geht, den er gar nicht getippt hat.
        if not echt or echt.startswith("!"):
            return None
        log.info("frag_ausgefuehrt", sender=sender, cmd=name, arg=argument, antwort=echt)
        return echt

    # What a command needs when it is missing -- the shape and an example to
    # copy. The example is the more important half: someone typing `!sicht`
    # without arguments usually does not know what format two positions are
    # expected in, and `<lat,lon>` does not answer that.
    #
    # Same shape everywhere: "!command <what> - z.B. !command concrete".
    USAGE = {
        "wx": "!wx <ort|gipfel|lat lon> - z.B. !wx villach oder !wx triglav",
        "vorhersage": "!vorhersage <ort|lat lon> - z.B. !vorhersage spittal",
        "warn": "!warn [ort|lat lon] - ohne Angabe ganz Kaernten, sonst z.B. !warn hermagor",
        "sota": "!sota <ref|lat lon> - z.B. !sota kt-048 oder !sota 46.60 13.67",
        "az": "!az <lat lon> - z.B. !az 46.9089,13.8506",
        "spot": "!spot [assoc] - ohne Angabe OE, sonst z.B. !spot DL",
        "relais": "!relais <2m|70cm|23cm> [ort] - z.B. !relais 2m villach",
        "sicht": "!sicht <lat,lon> <lat,lon> - z.B. !sicht 46.60,13.67 46.67,13.89",
        "hoehe": "!hoehe <lat,lon> - z.B. !hoehe 46.6719,13.8902",
        "dist": "!dist <lat,lon> <lat,lon> - z.B. !dist 46.60,13.67 46.79,14.96",
        "qth": "!qth <locator|lat lon> - z.B. !qth JN76hp oder !qth 46.62 13.85",
        "wo": "!wo <name|hash> - z.B. !wo dobratsch oder !wo d733",
        "melde": "!melde <was, wo> - z.B. !melde kein Empfang, Bad Bleiberg Ortsmitte",
        "iss": "!iss [lat lon] - ohne Angabe der Standardort, sonst z.B. !iss 46.62 13.85",
        "help": "!help [befehl|thema] - z.B. !help sicht oder !help berg",
        "gipfel": "!gipfel <berg> - z.B. !gipfel dobratsch oder !gipfel triglav",
        "frag": "!frag <frage> - z.B. !frag wie weit traegt 868 MHz",
    }

    HILFE = {
        "wx": "!wx <ort|lat lon> Messwerte einer der 34 Stationen in Kaernten. Fuer Berge: !gipfel",
        "gipfel": "!gipfel <berg> Gipfelwetter aus dem Modell, AT/IT/SI/DE/CH/HR/CZ/SK/HU/PL. Alias !berg",
        "version": "!version welcher Stand laeuft und was daran neu ist. Aliase !ver !stand",
        "vorhersage": "!vorhersage <ort|lat lon> Spanne, Regen und Boeen der naechsten 24h",
        "warn": "!warn [ort|lat lon] amtliche Warnungen der Gemeinde (GeoSphere). Ohne Angabe ganz Kaernten",
        "sota": "!sota <ref> Gipfeldaten. !sota <lat lon> naechster Gipfel. !spot wer ist QRV",
        "az": "!az <lat lon> stehst du in der SOTA-Aktivierungszone? Polygon von SOTLAS",
        "spot": "!spot [assoc] wer gerade auf einem Gipfel funkt, Vorgabe OE",
        "relais": "!relais <2m|70cm|23cm> [ort|lat lon] naechste Relais",
        "sonne": "!sonne [ort|lat lon] Auf-, Untergang, Daemmerung",
        "lawine": "!lawine Lawinenwarnstufe Kaernten (nur in der Saison)",
        "netz": "!netz Zustand des Mesh: aktive Repeater und Verkehr",
        "zeit": "!zeit UTC und Epoch-Sekunden, fuer Uhren am Node",
        "ping": "!ping Lebenszeichen des Bots, taugt auch als Reichweitentest",
        "quota": "!quota wie viele Sendungen diese Stunde noch gehen. Aliase !kontingent !rest",
        "help": "!help zeigt alle Befehle, !help <cmd> die Einzelheiten",
        "wo": "!wo <name|hash> Position, Verkehr, letzter Empfang. Auch der Pfad-Hash aus der App, Alias !pfad",
        "melde": "!melde <was, wo> Luecke oder Stoerung melden, Position mitschicken",
        "qth": "!qth <locator|lat lon> Maidenhead in Koordinaten und zurueck",
        "sicht": "!sicht <lat,lon> <lat,lon> Funkstrecke pruefen: frei, knapp oder blockiert",
        "hoehe": "!hoehe <lat,lon> Gelaendehoehe aus dem 25m-Modell",
        "dist": "!dist <lat,lon> <lat,lon> Entfernung, Peilung und Gegenpeilung",
        "dx": "!dx Kurzwellenbedingungen: Sonnenfluss, A- und K-Index",
        "mond": "!mond [ort|lat lon] Auf-, Untergang und Phase",
        "iss": "!iss [lat lon] naechster Ueberflug der Raumstation ueber 10 Grad",
        "frag": "!frag <frage> KI antwortet, Praefix KI:. Kann irren, ist keine Messung",
    }

    # Groups for the second help stage. The order matches the overview.
    GRUPPEN = {
        "wetter": ["wx", "gipfel", "vorhersage", "warn", "lawine"],
        "berg": ["gipfel", "sota", "az", "spot", "sonne", "mond"],
        "standort": ["sicht", "hoehe", "dist", "qth"],
        "netz": ["netz", "wo", "relais", "ping", "quota"],
        "sonst": ["dx", "iss", "zeit", "melde", "frag", "version"],
    }

    def gruppen(self) -> dict[str, list[str]]:
        """The groups as the help should present them *right now*.

        `!frag` is listed only while it is switched on. A listed but disabled
        command is worse than an unlisted one: the bot answers unknown commands
        with silence, so somebody who reads `!frag` in the overview and types it
        gets nothing back and concludes the bot is broken. With `FRAG_ENABLED`
        off -- which is how it ships and how the first deployment runs -- the
        command simply does not exist as far as the help is concerned.

        `getattr` because a couple of tests build a bare `Bot.__new__(Bot)` with
        nothing but `settings` on it.
        """
        an = getattr(self, "frag_enabled", self.settings.frag_enabled)
        if an:
            return self.GRUPPEN
        return {name: [c for c in cmds if c != "frag"] for name, cmds in self.GRUPPEN.items()}

    def usage(self, cmd: str) -> str:
        """What is missing, and what it looks like when present.

        An answer rather than silence, on purpose: a command somebody typed
        correctly is not garbage -- it is only missing an argument. The airtime
        is better spent on that than on a second round of guessing. For an
        *unknown* command the bot still stays silent.
        """
        return self.USAGE.get(cmd, f"!{cmd}: Argument fehlt")

    async def cmd_help(self, arg: str, sender: str) -> str:
        """Three stages: single command, group, overview.

        Aliases are resolved too, but **only after the groups**: `wetter` is
        both -- an alias for !wx and the name of a group. Whoever types
        `!help wetter` means the group. Without that ordering a published alias
        such as !pfad leads nowhere: the command answers, its help does not.

        `netz` is both in a different way: a command **and** a group. Asking for
        it returns both in one message -- previously the command won and the
        group was unreachable altogether.
        """
        thema = arg.strip().lstrip("!").lower()
        befehl = self.HILFE.get(thema)
        gruppe = None
        gruppen = self.gruppen()
        if thema in gruppen:
            gruppe = f"{thema.title()}: " + " ".join("!" + c for c in gruppen[thema])

        if befehl and gruppe:
            # The command is already in the group listing -- naming it a second
            # time in the appended help text reads like a bug. So append only
            # the explanation, without the "!netz" in front of it.
            erklaerung = befehl[len(f"!{thema} "):] if befehl.startswith(f"!{thema} ") else befehl
            beides = f"{gruppe} | {erklaerung}"
            return beides if len(beides) <= self.settings.nutzlimit else gruppe
        if gruppe:
            return gruppe
        if befehl:
            return befehl
        ziel = ALIASES.get(thema)
        if ziel in self.HILFE:
            return self.HILFE[ziel]
        return self._uebersicht()

    def _uebersicht(self) -> str:
        """All commands in one message — as long as they fit.

        The flat list is the better answer: whoever types !help wants to see
        what exists, not click through a menu first. But it grows with every
        command. Once it no longer fits, the answer falls back to the group
        names by itself instead of being truncated at the character limit.
        """
        # A command may appear in two groups -- !gipfel is weather and mountain.
        # In the flat list it would show up twice, and the count would be wrong.
        gruppen = self.gruppen()
        alle = list(dict.fromkeys(c for gruppe in gruppen.values() for c in gruppe))
        grenze = self.settings.nutzlimit
        # From the nicest to the shortest form; the first that fits wins.
        #
        # Since the drop to 100 characters the group form is the normal case,
        # not the emergency brake -- which is why it names the command count.
        # Without it, it reads like an error message: five words, with no way to
        # tell that two dozen commands sit behind them.
        for kandidat in (" ".join("!" + c for c in alle) + " | !help <cmd>",
                         " ".join(alle) + " !help <cmd>",
                         " ".join(alle),
                         f"{len(alle)} Befehle in {len(gruppen)} Gruppen: "
                         + " ".join(gruppen) + " | !help <thema>",
                         "Themen: " + " ".join(gruppen) + " | !help <thema>"):
            if len(kandidat) <= grenze:
                return kandidat
        return "!help <thema>: " + " ".join(gruppen)

    # --- Infrastructure --------------------------------------------------

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

    async def _frag_wache(self) -> None:
        """Notice a dead inference host before somebody asks.

        Without this the failure mode is invisible: `!frag` answers nothing, and
        on the air that is indistinguishable from nobody having asked. The bot's
        own health check deliberately covers only MQTT, so nothing else watches
        this.

        Only the **change** is logged, not every probe -- a host that has been
        down for a day should produce one line, not 288.
        """
        while True:
            if self.frag_enabled:
                jetzt = await h_frag.erreichbar(self.http, self.settings)
                if jetzt != self.frag_erreichbar:
                    if jetzt:
                        log.info("frag_erreichbar", url=h_frag.probe_url(self.settings))
                    else:
                        log.warning("frag_nicht_erreichbar",
                                    url=h_frag.probe_url(self.settings),
                                    hinweis="!frag schweigt, bis der Dienst zurueck ist")
                    self.frag_erreichbar = jetzt
            else:
                self.frag_erreichbar = None
            await asyncio.sleep(self.settings.frag_probe_s)

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
        """Remote switches over MQTT.

        `frag off` exists separately from `pause` on purpose: !frag is the one
        command whose output nobody vetted before it went on the air. If it
        misbehaves, the weather and the network status should keep working while
        it is switched off -- pausing the whole bot to silence one command is
        the wrong-sized hammer.
        """
        befehl = raw.decode("utf-8", errors="replace").strip().lower()
        if befehl in ("pause", "stop", "off"):
            self.router.enabled = False
            log.warning("bot_pausiert")
        elif befehl in ("resume", "start", "on"):
            self.router.enabled = True
            log.warning("bot_fortgesetzt")
        elif befehl in ("frag off", "frag stop", "ki off"):
            self.frag_enabled = False
            log.warning("frag_aus")
        elif befehl in ("frag on", "frag start", "ki on"):
            self.frag_enabled = True
            log.warning("frag_an")

    def on_quota(self, raw: bytes) -> None:
        """Record the gate's quota state. Runs in the paho thread.

        Unusable messages are ignored rather than clearing the old value: a
        stale reading is worth more than none, and the answer says where the
        number came from anyway.
        """
        daten = h_quota.parse(raw)
        if daten is not None:
            # Logged so that in operation it is provable the message arrives:
            # if it stops, !quota says "Gate meldet nichts" -- and from the
            # outside that looks exactly like a refused subscription or a wrong
            # topic.
            log.info("quota", **{k: daten.get(k) for k in ("used", "remaining", "limit")})
            self.quota = daten

    async def run(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        self.mqtt.start(loop)
        health = asyncio.create_task(serve_health(self.settings, self))
        wache = asyncio.create_task(self._frag_wache())
        log.info("gestartet", rx=self.settings.topic_rx, tx=self.settings.topic_tx,
                 enabled=self.router.enabled, relais=len(self.relais),
                 orte=len(self.stations.get("orte", {})),
                 stationen=len(self.stations.get("stationen", [])),
                 gipfel=len(self.summits))
        await stop.wait()
        log.info("beende")
        wache.cancel()
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
