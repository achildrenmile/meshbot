"""!qth — Maidenhead-Locator in Koordinaten und zurueck.

Reine Rechnung, keine Quelle. Im Amateurfunk ist der Locator die uebliche
Standortangabe, im Mesh sind es Dezimalgrade — der Befehl uebersetzt zwischen
beiden Welten.
"""

from __future__ import annotations

import re
import string

LOCATOR = re.compile(r"^[A-R]{2}\d{2}([A-X]{2})?(\d{2})?$", re.I)


def to_locator(lat: float, lon: float, stellen: int = 6) -> str:
    """Koordinaten -> Locator. 6 Stellen entsprechen rund 5 x 4 km."""
    lon += 180.0
    lat += 90.0
    gross = string.ascii_uppercase
    text = gross[int(lon // 20)] + gross[int(lat // 10)]
    text += str(int((lon % 20) // 2)) + str(int(lat % 10))
    if stellen >= 6:
        text += gross[int((lon % 2) * 12)].lower() + gross[int((lat % 1) * 24)].lower()
    return text


def from_locator(loc: str) -> tuple[float, float] | None:
    """Locator -> Mittelpunkt des Feldes. None bei ungueltiger Eingabe."""
    loc = loc.strip().upper()
    if not LOCATOR.match(loc):
        return None
    gross = string.ascii_uppercase
    lon = (gross.index(loc[0]) * 20) - 180
    lat = (gross.index(loc[1]) * 10) - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3])
    if len(loc) >= 6:
        lon += (gross.index(loc[4]) + 0.5) / 12
        lat += (gross.index(loc[5]) + 0.5) / 24
    else:                                   # Mitte des groben Feldes
        lon += 1
        lat += 0.5
    return round(lat, 4), round(lon, 4)


def render_locator(loc: str, koord: tuple[float, float] | None) -> str:
    if koord is None:
        return f"QTH: {loc[:12]} ist kein gueltiger Locator"
    return f"QTH {loc.upper()}: {koord[0]:.4f}, {koord[1]:.4f}"


def render_koord(lat: float, lon: float) -> str:
    return f"QTH {lat:.4f}, {lon:.4f}: {to_locator(lat, lon)}"
