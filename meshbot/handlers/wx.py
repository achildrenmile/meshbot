"""!wx — aktuelle Messwerte einer TAWES-Station der GeoSphere Austria.

Quelle: dataset.api.hub.geosphere.at, Datensatz `tawes-v1-10min`, frei nutzbar
unter CC BY 4.0. Die Zuordnung Ort → Station steht in `data/stations_ktn.json`
und wurde aus der Stationsliste der GeoSphere erzeugt (nächstgelegene Station).
"""

from __future__ import annotations

import json
from difflib import get_close_matches
from typing import Any

import httpx

from ..config import Settings

PARAMS = "TL,RF,FFAM,DD,P"          # Temperatur, Feuchte, Wind, Richtung, Druck
HIMMELSRICHTUNG = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def _richtung(grad: float | None) -> str:
    if grad is None:
        return ""
    return HIMMELSRICHTUNG[int((grad % 360) / 45 + 0.5) % 8]


def load_stations(settings: Settings) -> dict[str, dict[str, Any]]:
    with open(settings.stations_file, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_place(arg: str, stations: dict[str, dict[str, Any]], default: str) -> tuple[str, dict[str, Any]] | None:
    """Ort auf eine Station abbilden, mit Tippfehler-Toleranz."""
    key = " ".join(arg.split()).lower().strip() or default
    key = key.replace("ö", "oe").replace("ä", "ae").replace("ü", "ue").replace("ß", "ss")
    if key in stations:
        return key, stations[key]
    treffer = get_close_matches(key, list(stations), n=1, cutoff=0.6)
    if treffer:
        return treffer[0], stations[treffer[0]]
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
