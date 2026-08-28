"""!sota — look up a summit, by reference or by position.

Two ways, because on a summit one rarely knows the reference while the device
does have the coordinates:

    !sota kt-048          -> lookup via the SOTA API
    !sota 46.60 13.67     -> nearest summits from the local dataset

The local dataset (`data/sota_summits.json`) comes from the SOTA list and covers
Carinthia together with the neighbouring regions. It needs no network and
answers immediately.
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


# --- Lookup by position --------------------------------------------------

HIMMEL = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
# Two decimal numbers anywhere in the text. Generous on purpose: what the app
# inserts when sharing a position is not predictable - sometimes bare numbers,
# sometimes a geo: link, sometimes with a label in front. Nobody should have to
# retype it.
ZAHL = re.compile(r"-?\d{1,3}[.,]\d+")


def load_summits(pfad: Any) -> list[dict[str, Any]]:
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)["gipfel"]


def parse_coords(arg: str) -> tuple[float, float] | None:
    """Pull a position out of arbitrary text. None when there is none.

    Recognised forms include::

        46.60 13.67
        46.6031, 13.6712
        46,6031, 13,6712              (German decimal comma)
        geo:46.6031,13.6712
        https://maps.google.com/?q=46.6031,13.6712
        Position: 46.6031 / 13.6712

    The first two decimal numbers are used; whole numbers such as a zoom factor
    in a map link therefore do not interfere.
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
    """Rough compass direction from the position to the summit."""
    dl = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    grad = (math.degrees(math.atan2(y, x)) + 360) % 360
    return HIMMEL[int(grad / 45 + 0.5) % 8]


def nearest(summits: list[dict[str, Any]], lat: float, lon: float, limit: int = 2) -> list[dict[str, Any]]:
    """Nearest summits, enriched with distance and direction."""
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
