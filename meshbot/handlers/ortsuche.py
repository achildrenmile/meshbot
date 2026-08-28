"""Orte ausserhalb Kaerntens — Ortssuche und Modellwetter von Open-Meteo.

Die GeoSphere misst in Kaernten, und dort enden die 34 Stationen. Wer nach
Lienz, Hamburg oder Ljubljana fragt, bekam frueher entweder eine Absage oder,
schlimmer, den aehnlichsten Kaerntner Weiler: "Hamburg" wurde zu "Haimburg".

Hier kommt beides aus derselben Quelle wie das Gipfelwetter: erst die Position
(Geocoding-API), dann der Modellwert dazu. **Es ist ein Modell, keine Messung**,
und die Antwort sagt das -- genauso wie beim Gipfelwetter.

Das Laenderkuerzel steht mit in der Antwort, und das ist kein Schmuck: "Lienz"
gibt es in Osttirol und im Kanton St. Gallen, "Hamburg" in Deutschland und
viermal in den USA. Ohne Kuerzel weiss der Empfaenger nicht, welches er
bekommen hat.
"""

from __future__ import annotations

from typing import Any

import httpx

from .wx import normalisiere


async def suche_ort(client: httpx.AsyncClient, url: str, name: str,
                    anzahl: int = 10) -> list[dict[str, Any]]:
    """Kandidaten zu einem Ortsnamen, unbewertet."""
    if not name.strip():
        return []
    # Auch die Abfrage laeuft ueber die Aliase: Nach "Koschuta" gefragt liefert
    # der Dienst Orte in Bosnien und Weissrussland, nach "Koschutnikturm" den
    # Berg in den Karawanken.
    name = ORTSALIASE.get(normalisiere(name), name.strip())
    r = await client.get(url, params={"name": name.strip(), "count": anzahl,
                                      "language": "de", "format": "json"},
                         timeout=20.0)
    r.raise_for_status()
    return r.json().get("results") or []


# Ungefaehre Mitte Kaerntens. Wer hier funkt, meint bei einem mehrdeutigen
# Namen fast immer den Ort in der Naehe -- "Peca" gibt es in Indonesien und in
# den Karawanken, und nur eines davon ist gemeint.
HEIMAT = (46.70, 13.90)
NAHBEREICH_KM = 300.0

# Namen, unter denen der Ortsverzeichnisdienst den Berg nicht fuehrt. Gleiche
# Idee wie die SOTA-Aliase in `wxberg`, nur fuer die andere Quelle: Die Petzen
# steht dort als "Peca", die Koschuta als "Koschutnikturm". Ohne das gewinnt
# ein gleichnamiges Dorf in Niedersachsen.
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
    """Exakter Name, dann Naehe, dann Einwohnerzahl.

    Jede Stufe loest einen gemessenen Fehlgriff:

    * **Exakter Name** -- nur nach Einwohnerzahl gewaehlt liefert "Petzen" das
      groessere *Petzenkirchen*.
    * **Naehe** -- "Peca" heisst ein Ort in Indonesien und der Berg in den
      Karawanken, beide mit Einwohnerzahl null. Ohne Naehe entscheidet der
      Zufall. "Lienz" gibt es in Osttirol und im Kanton St. Gallen.
    * **Einwohnerzahl** -- greift, wenn nichts in der Naehe liegt: "Hamburg"
      steht einmal in Deutschland und viermal in den USA.

    `nur_exakt` ist die Stufe, die **vor** der Kaerntner Tippfehlersuche
    laeuft. Ohne sie wird aus dem Tippfehler "vilach" das spanische *Vilachá*
    mit fuenf Einwohnern -- ein exakter Treffer darf einen geratenen schlagen,
    ein geratener einen geratenen nicht.
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
    """Modellwerte fuer die Position des Ortes.

    Dieselben Felder wie beim Gipfelwetter, damit beide Antworten gleich zu
    lesen sind. `elevation` wird mitgegeben, wenn die Ortssuche eine Hoehe
    kennt -- sonst rechnet das Modell fuer die mittlere Hoehe seiner
    Gitterzelle, und die liegt im Gebirge regelmaessig daneben.
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

    Kein Luftdruck, wie beim Gipfelwetter: Open-Meteo liefert ihn auf
    Meereshoehe zurueckgerechnet, das ist keine Angabe von dort.
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
