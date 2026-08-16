"""!sota — Gipfel nachschlagen, per Referenz oder per Position.

Zwei Wege, weil man am Gipfel selten die Referenz kennt, das Gerät aber die
Koordinaten hat:

    !sota kt-048          -> Nachschlag ueber die SOTA-API
    !sota 46.60 13.67     -> naechstgelegene Gipfel aus dem lokalen Bestand

Der lokale Bestand (`data/sota_summits.json`) stammt aus der SOTA-API und deckt
Kaernten samt Nachbarregionen ab. Er braucht kein Netz und antwortet sofort.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

import httpx

REF = re.compile(r"^\s*(?:([A-Z0-9]{1,3}(?:/[A-Z0-9]{1,3})?)[/\s-]*)?([A-Z]{2})[\s-]?(\d{1,3})\s*$", re.I)


def normalise(arg: str, default_assoc: str) -> str | None:
    """`oe/kt-048`, `kt048`, `KT-48` → `OE/KT-048`."""
    m = REF.match(arg.replace("_", "-"))
    if not m:
        return None
    assoc, region, num = m.groups()
    if assoc and "/" in assoc:
        praefix = assoc.upper()
    elif assoc:
        praefix = f"{assoc.upper()}/{region.upper()}"
        return f"{praefix}-{int(num):03d}"
    else:
        praefix = default_assoc.upper().split("/")[0] + "/" + region.upper()
    if not praefix.endswith(region.upper()):
        praefix = f"{praefix.split('/')[0]}/{region.upper()}"
    return f"{praefix}-{int(num):03d}"


async def fetch(client: httpx.AsyncClient, base_url: str, ref: str) -> dict[str, Any] | None:
    assoc, code = ref.split("/", 1)[0], ref.split("/", 1)[1]
    resp = await client.get(f"{base_url}/{assoc}/{code}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data or None


def render(ref: str, gipfel: dict[str, Any] | None, stale: bool = False) -> str:
    if not gipfel:
        return f"SOTA: {ref} nicht gefunden"
    marker = "~" if stale else ""
    name = gipfel.get("name") or gipfel.get("summitName") or "?"
    hoehe = gipfel.get("altM") or gipfel.get("altitudeM")
    punkte = gipfel.get("points")
    akt = gipfel.get("activationCount", gipfel.get("activations"))
    teile = [f"{ref} {marker}{name}"]
    if hoehe:
        teile.append(f"{int(hoehe)}m")
    if punkte:
        teile.append(f"{int(punkte)}Pkt")
    if akt is not None:
        teile.append(f"Akt: {int(akt)}")
    return teile[0] + " " + ", ".join(teile[1:])


# --- Suche nach Position -------------------------------------------------

HIMMEL = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
# Zwei Dezimalzahlen irgendwo im Text. Bewusst grosszuegig: Was die App beim
# Teilen einer Position einfuegt, ist nicht vorhersagbar - mal nackte Zahlen,
# mal ein geo:-Link, mal mit Beschriftung davor. Abtippen soll niemand muessen.
ZAHL = re.compile(r"-?\d{1,3}[.,]\d+")


def load_summits(pfad: Any) -> list[dict[str, Any]]:
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)["gipfel"]


def parse_coords(arg: str) -> tuple[float, float] | None:
    """Position aus beliebigem Text ziehen. None, wenn keine drinsteckt.

    Erkannt werden unter anderem::

        46.60 13.67
        46.6031, 13.6712
        46,6031, 13,6712              (deutsches Dezimalkomma)
        geo:46.6031,13.6712
        https://maps.google.com/?q=46.6031,13.6712
        Position: 46.6031 / 13.6712

    Gesucht werden die ersten zwei Dezimalzahlen; ganze Zahlen wie ein
    Zoomfaktor in einem Kartenlink fallen dadurch nicht ins Gewicht.
    """
    treffer = ZAHL.findall(arg)
    if len(treffer) < 2:
        return None
    try:
        lat = float(treffer[0].replace(",", "."))
        lon = float(treffer[1].replace(",", "."))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def distanz_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def richtung(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Grobe Himmelsrichtung vom Standort zum Gipfel."""
    dl = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    grad = (math.degrees(math.atan2(y, x)) + 360) % 360
    return HIMMEL[int(grad / 45 + 0.5) % 8]


def nearest(summits: list[dict[str, Any]], lat: float, lon: float, limit: int = 2) -> list[dict[str, Any]]:
    """Naechstgelegene Gipfel, mit Entfernung und Richtung angereichert."""
    treffer = []
    for s in summits:
        d = distanz_km(lat, lon, s["lat"], s["lon"])
        if d < 25:                       # weiter weg ist als Standortangabe wertlos
            treffer.append({**s, "_d": d, "_r": richtung(lat, lon, s["lat"], s["lon"])})
    treffer.sort(key=lambda s: s["_d"])
    return treffer[:limit]


def render_nearest(treffer: list[dict[str, Any]]) -> str:
    if not treffer:
        return "SOTA: kein Gipfel in 25km"
    teile = []
    for s in treffer:
        entfernung = f"{s['_d']*1000:.0f}m" if s["_d"] < 1 else f"{s['_d']:.1f}km"
        teile.append(f"{s['ref']} {s['name']} {s['alt']}m {s['pts']}Pkt ({entfernung} {s['_r']})")
    return " | ".join(teile)
