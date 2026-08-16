"""!relais — nächstgelegene Amateurfunk-Relais.

Datenbestand aus RelaisBlick (oeradio.at), als JSON im Repo mitgeliefert. Kein
Netzzugriff nötig: Die Liste ändert sich selten, und ein Bot ohne Internet soll
diesen Befehl trotzdem beantworten können. Aktualisieren siehe README.
"""

from __future__ import annotations

import json
import math
from typing import Any

BAENDER = {"2m", "70cm", "23cm", "6m", "13cm"}


def load_relais(pfad: Any) -> list[dict[str, Any]]:
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)["relais"]


def distanz_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def suche(relais: list[dict[str, Any]], band: str, lat: float, lon: float, limit: int = 2) -> list[dict[str, Any]]:
    passend = [r for r in relais if (r.get("band") or "").lower() == band.lower() and r.get("lat")]
    for r in passend:
        r["_d"] = distanz_km(lat, lon, r["lat"], r["lon"])
    return sorted(passend, key=lambda r: r["_d"])[:limit]


def render(band: str, ort: str, treffer: list[dict[str, Any]]) -> str:
    if not treffer:
        return f"Relais {band}: nichts gefunden"
    teile = []
    for r in treffer:
        shift = r.get("shift")
        shift_txt = f" {shift/1000:+.1f}".rstrip("0").rstrip(".") if isinstance(shift, (int, float)) else ""
        teile.append(f"{r['call']} {r['ort'].split('-')[0].strip()} {r['tx']}{shift_txt} ({r['_d']:.0f}km)")
    return f"{band} b. {ort.title()}: " + " | ".join(teile)
