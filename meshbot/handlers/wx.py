"""!wx — aktuelle Messwerte einer TAWES-Station der GeoSphere Austria.

Quelle: dataset.api.hub.geosphere.at, Datensatz `tawes-v1-10min`, frei nutzbar
unter CC BY 4.0. Die Zuordnung Ort → Station steht in `data/stations_ktn.json`
und wurde aus der Stationsliste der GeoSphere erzeugt (nächstgelegene Station).
"""

from __future__ import annotations

import json
import math
from difflib import get_close_matches
from typing import Any

import httpx

from ..config import Settings
from .sota import parse_coords

PARAMS = "TL,RF,FFAM,DD,P"          # Temperatur, Feuchte, Wind, Richtung, Druck
HIMMELSRICHTUNG = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def _richtung(grad: float | None) -> str:
    if grad is None:
        return ""
    return HIMMELSRICHTUNG[int((grad % 360) / 45 + 0.5) % 8]


def load_stations(settings: Settings) -> dict[str, Any]:
    """Ortszuordnung und vollstaendige Stationsliste.

    Die Ortszuordnung deckt die gaengigen Namen ab, die Stationsliste erlaubt
    die Suche ueber Koordinaten — am Berg tippt niemand einen Ortsnamen, aber
    das Geraet kennt die Position.
    """
    with open(settings.stations_file, encoding="utf-8") as fh:
        daten = json.load(fh)
    if "orte" not in daten:            # altes Format ohne Stationsliste
        return {"orte": daten, "stationen": []}
    return daten


def distanz_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def station_bei(stationen: list[dict[str, Any]], lat: float, lon: float) -> dict[str, Any] | None:
    """Naechstgelegene Wetterstation zu einer Position."""
    if not stationen:
        return None
    return min(stationen, key=lambda s: distanz_km(lat, lon, s["lat"], s["lon"]))


def resolve_place(arg: str, stations: dict[str, Any], default: str) -> tuple[str, dict[str, Any]] | None:
    """Ort oder Position auf eine Station abbilden.

    Akzeptiert einen Ortsnamen (mit Tippfehler-Toleranz) oder Koordinaten in
    beliebiger Schreibweise. Bei Koordinaten wird die naechstgelegene Station
    genommen und ihr Name zurueckgegeben — damit sieht der Empfaenger, woher
    die Werte stammen.
    """
    orte = stations.get("orte", stations)

    koord = parse_coords(arg)
    if koord is not None:
        s = station_bei(stations.get("stationen", []), *koord)
        if s is not None:
            return s["name"], {"station_id": s["id"], "station": s["name"],
                               "lat": s["lat"], "lon": s["lon"]}
        return None

    key = " ".join(arg.split()).lower().strip() or default
    key = key.replace("ö", "oe").replace("ä", "ae").replace("ü", "ue").replace("ß", "ss")
    if key in orte:
        return key, orte[key]
    treffer = get_close_matches(key, list(orte), n=1, cutoff=0.6)
    if treffer:
        return treffer[0], orte[treffer[0]]
    return None


async def fetch(client: httpx.AsyncClient, settings: Settings, station_id: str) -> dict[str, Any]:
    resp = await client.get(
        settings.geosphere_tawes_url,
        params={"parameters": PARAMS, "station_ids": station_id, "output_format": "geojson"},
    )
    resp.raise_for_status()
    data = resp.json()
    props = data["features"][0]["properties"]["parameters"]
    return {name: (props.get(name) or {}).get("data", [None])[0] for name in PARAMS.split(",")}


def render(ort: str, werte: dict[str, Any], stale: bool = False) -> str:
    """Eine Zeile, feste Reihenfolge: Temperatur, Feuchte, Wind, Druck."""
    marker = "~" if stale else ""
    teile = [f"WX {ort.title()}: {marker}"]
    if werte.get("TL") is not None:
        teile.append(f"{werte['TL']:.1f}C")
    if werte.get("RF") is not None:
        teile.append(f"{werte['RF']:.0f}%")
    if werte.get("FFAM") is not None:
        kmh = werte["FFAM"] * 3.6
        teile.append(f"Wind {kmh:.0f}km/h {_richtung(werte.get('DD'))}".strip())
    if werte.get("P") is not None:
        teile.append(f"{werte['P']:.0f}hPa")
    return teile[0] + ", ".join(teile[1:])
