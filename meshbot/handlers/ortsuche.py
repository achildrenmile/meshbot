"""Places outside Carinthia — place lookup and model weather from Open-Meteo.

GeoSphere measures in Carinthia, and that is where the 34 stations end. Asking
for Lienz, Hamburg or Ljubljana used to yield either a refusal or, worse, the
most similar Carinthian hamlet: "Hamburg" became "Haimburg".

Here both come from the same source as the summit weather: first the position
(geocoding API), then the model value for it. **It is a model, not a
measurement**, and the answer says so -- exactly as with summit weather.

The country code is part of the answer, and it is not decoration: there is a
"Lienz" in East Tyrol and one in the canton of St. Gallen, a "Hamburg" in
Germany and four in the USA. Without the code the receiver has no way to tell
which one arrived.
"""

from __future__ import annotations

from typing import Any

import httpx

from .wx import normalisiere


async def suche_ort(client: httpx.AsyncClient, url: str, name: str,
                    anzahl: int = 10) -> list[dict[str, Any]]:
    """Candidates for a place name, unranked."""
    if not name.strip():
        return []
    # The query goes through the aliases as well: asked for "Koschuta" the
    # service returns places in Bosnia and Belarus; asked for "Koschutnikturm"
    # it returns the mountain in the Karawanks.
    name = ORTSALIASE.get(normalisiere(name), name.strip())
    r = await client.get(url, params={"name": name.strip(), "count": anzahl,
                                      "language": "de", "format": "json"},
                         timeout=20.0)
    r.raise_for_status()
    return r.json().get("results") or []


# Approximate centre of Carinthia. Anyone transmitting from here almost always
# means the nearby place when a name is ambiguous -- there is a "Peca" in
# Indonesia and one in the Karawanks, and only one of them is meant.
HEIMAT = (46.70, 13.90)
NAHBEREICH_KM = 300.0

# Names under which the gazetteer does not carry the mountain. Same idea as the
# SOTA aliases in `wxberg`, but for the other source: the Petzen is listed there
# as "Peca", the Koschuta as "Koschutnikturm". Without this a village of the
# same name in Lower Saxony wins.
ORTSALIASE = {
    "petzen": "Peca",
    "koschuta": "Koschutnikturm",
    "kosuta": "Koschutnikturm",
}


def _entfernung_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


def waehle(treffer: list[dict[str, Any]], begriff: str,
           nur_exakt: bool = False, heimat: tuple[float, float] = HEIMAT
           ) -> dict[str, Any] | None:
    """Exact name, then proximity, then population.

    Every stage fixes a measured mistake:

    * **Exact name** -- chosen by population alone, "Petzen" yields the larger
      *Petzenkirchen*.
    * **Proximity** -- "Peca" is a place in Indonesia and the mountain in the
      Karawanks, both with population zero. Without proximity, chance decides.
      There is a "Lienz" in East Tyrol and one in the canton of St. Gallen.
    * **Population** -- applies when nothing is nearby: "Hamburg" exists once in
      Germany and four times in the USA.

    `nur_exakt` is the stage that runs **before** the Carinthian typo search.
    Without it the typo "vilach" turns into the Spanish *Vilachá*, population
    five -- an exact match may beat a guess, a guess may not beat another guess.
    """
    k = normalisiere(begriff)
    if not k:
        return None
    k = normalisiere(ORTSALIASE.get(k, k))
    genau = [t for t in treffer if normalisiere(t.get("name", "")) == k]
    auswahl = genau if genau else ([] if nur_exakt else treffer)
    if not auswahl:
        return None
    nah = [t for t in auswahl
           if _entfernung_km(*heimat, t["latitude"], t["longitude"]) <= NAHBEREICH_KM]
    if nah:
        return min(nah, key=lambda t: _entfernung_km(*heimat, t["latitude"], t["longitude"]))
    return max(auswahl, key=lambda t: t.get("population") or 0)


async def fetch(client: httpx.AsyncClient, url: str, ort: dict[str, Any]) -> dict[str, Any]:
    """Model values for the position of the place.

    Same fields as the summit weather, so both answers read alike. `elevation`
    is passed along when the place lookup knows one -- otherwise the model
    computes for the mean elevation of its grid cell, which in the mountains is
    routinely off.
    """
    params: dict[str, Any] = {
        "latitude": ort["latitude"], "longitude": ort["longitude"],
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh",
    }
    if ort.get("elevation") is not None:
        params["elevation"] = ort["elevation"]
    r = await client.get(url, params=params, timeout=20.0)
    r.raise_for_status()
    daten = r.json().get("current") or {}
    if daten.get("temperature_2m") is None:
        raise ValueError("Modell ohne Temperatur")
    return daten


RICHTUNGEN = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def kurzname(ort: dict[str, Any], grenze: int = 20) -> str:
    name = ort.get("name") or "?"
    return name if len(name) <= grenze else name[:grenze].rstrip()


def render(ort: dict[str, Any], w: dict[str, Any], stale: bool = False,
           geraten: bool = False) -> str:
    """`WX Hamburg (DE): 18.2C, 71%, Wind 14km/h W (Modell)`

    No air pressure, as with the summit weather: Open-Meteo reports it reduced
    to sea level, which is not a value from that location.
    """
    marker = "~" if stale else ""
    land = ort.get("country_code") or "?"
    name = kurzname(ort) + ("?" if geraten else "")
    werte = []
    if w.get("temperature_2m") is not None:
        werte.append(f"{w['temperature_2m']:.1f}C")
    if w.get("relative_humidity_2m") is not None:
        werte.append(f"{int(w['relative_humidity_2m'])}%")
    if w.get("wind_speed_10m") is not None:
        wind = f"Wind {w['wind_speed_10m']:.0f}km/h"
        if w.get("wind_direction_10m") is not None:
            wind += " " + RICHTUNGEN[round(w["wind_direction_10m"] / 22.5) % 16]
        werte.append(wind)
    return f"WX {name} ({land}): {marker}" + ", ".join(werte) + " (Modell)"
