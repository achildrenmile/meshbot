"""Gipfelwetter — `!wx <berggipfel>`, wenn der Name kein Kaerntner Ort ist.

Die Wetterstationen der GeoSphere enden an der Staatsgrenze, und auf einem
Gipfel steht ohnehin selten eine. Fuer Berge kommt deshalb ein **Modellwert**
von Open-Meteo, gerechnet auf die Gipfelhoehe.

**Das ist keine Messung, und die Antwort sagt das auch** -- sie endet auf
`(Modell)`. Das ist ehrlicher als eine Zahl, die aussieht wie ein Thermometer
am Gipfelkreuz. Wer den Unterschied nicht sieht, plant eine Tour nach einem
Modell und haelt es fuer eine Messung.

Das Gipfelverzeichnis kommt aus der SOTA-Liste und deckt Oesterreich, Italien,
Slowenien, Deutschland, die Schweiz, Kroatien, Tschechien, die Slowakei,
Ungarn und Polen ab -> `tools/build_gipfel.py`.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

import httpx

from .wx import normalisiere

# Windrichtung wie bei !wx, damit beide Antworten gleich zu lesen sind.
RICHTUNGEN = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

FELDER = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
          "wind_direction_10m", "pressure_msl")


def index(gipfel: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Namensverzeichnis, normalisiert wie das Ortsverzeichnis.

    Mehrere Gipfel teilen sich Namen -- bei einer Dublette gewinnt der hoehere.
    Wer nach "Hochwart" fragt, meint den Berg, nicht den Huegel daneben.
    """
    aus: dict[str, dict[str, Any]] = {}
    for g in gipfel:
        for teil in _namensvarianten(g["name"]):
            k = normalisiere(teil)
            if not k:
                continue
            alt = aus.get(k)
            if alt is None or (g.get("alt") or 0) > (alt.get("alt") or 0):
                aus[k] = g
    return aus


def _namensvarianten(name: str) -> list[str]:
    """`Villacher Alpe (Dobratsch)` findet man unter beiden Namen.

    Dasselbe bei `Matterhorn/Mont Cervin/Monte Cervino` und bei Bindestrich-
    Doppelnamen: Wer den slowenischen oder italienischen Namen tippt, meint
    denselben Berg.
    """
    varianten = [name]
    if "(" in name and ")" in name:
        vorne = name[: name.index("(")].strip()
        drin = name[name.index("(") + 1: name.rindex(")")].strip()
        varianten += [vorne, drin]
    for trenner in ("/", " – ", " - "):
        if trenner in name:
            varianten += [t.strip() for t in name.split(trenner)]
    return [v for v in varianten if v]


def suche(verzeichnis: dict[str, dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Genau, dann Teilstring, dann Aehnlichkeit."""
    k = normalisiere(begriff)
    if not k:
        return None
    if k in verzeichnis:
        return verzeichnis[k]
    treffer = [g for name, g in verzeichnis.items() if k in name]
    if treffer:
        return max(treffer, key=lambda g: g.get("alt") or 0)
    nah = get_close_matches(k, list(verzeichnis), n=1, cutoff=0.82)
    return verzeichnis[nah[0]] if nah else None


async def fetch(client: httpx.AsyncClient, url: str, gipfel: dict[str, Any]) -> dict[str, Any]:
    """Modellwerte fuer die Gipfelposition, auf die Gipfelhoehe gerechnet.

    `elevation` ist der Punkt an der Sache: Ohne diesen Parameter antwortet das
    Modell fuer die mittlere Hoehe seiner Gitterzelle, und die liegt bei einem
    Gipfel regelmaessig mehrere hundert Meter zu tief.
    """
    r = await client.get(url, params={
        "latitude": gipfel["lat"], "longitude": gipfel["lon"],
        "elevation": gipfel["alt"], "current": ",".join(FELDER),
        "wind_speed_unit": "kmh",
    }, timeout=20.0)
    r.raise_for_status()
    daten = r.json().get("current") or {}
    if daten.get("temperature_2m") is None:
        raise ValueError("Modell ohne Temperatur")
    return daten


def kurzname(name: str, grenze: int = 22) -> str:
    """Lange Doppelnamen auf den Bergnamen zusammenziehen.

    `Punta Penia – Marmolada` oder `Matterhorn/Mont Cervin/Monte Cervino`
    sprengen sonst die Nachricht. Hart abschneiden geht nicht -- daraus wird
    `Punta Penia – Marmolad`, und das ist kein Berg, das ist ein Tippfehler.
    Genommen wird deshalb die **kuerzeste** der Namensvarianten: meistens der
    Name, unter dem der Berg bekannt ist.
    """
    if len(name) <= grenze:
        return name
    varianten = [v for v in _namensvarianten(name) if 3 < len(v) <= grenze]
    return min(varianten, key=len) if varianten else name[:grenze].rsplit(" ", 1)[0]


def render(gipfel: dict[str, Any], w: dict[str, Any], stale: bool = False) -> str:
    """Eine Zeile, dieselbe Reihenfolge wie !wx -- plus Hoehe und Modellhinweis."""
    marker = "~" if stale else ""
    name = kurzname(gipfel["name"])
    teile = [f"WX {name} {gipfel['alt']}m: {marker}"]
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
    if w.get("pressure_msl") is not None:
        werte.append(f"{w['pressure_msl']:.0f}hPa")
    return teile[0] + ", ".join(werte) + " (Modell)"
