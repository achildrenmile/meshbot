"""!az — am I inside the SOTA activation zone?

The zone is **not a circle** around the summit. It is everything at most 25
vertical metres below the summit and connected to it — on the ground a crooked
area that runs along the ridge and stops abruptly at a steep face.

Which is why this **looks the answer up rather than computing it**: SOTLAS
publishes the finished zone polygon for every summit, generated from
high-resolution elevation models and in use across the SOTA community:

    https://az.sotl.as/OE/KT/048.gpx     (WGS84, usable as is)
    https://az.sotl.as/OE/KT/048.geojson (EPSG:3035, would need reprojection)

The GPX version is used: it is already in degrees and spares us a projection
computation of our own — one fewer source of error on a question that has to be
right.

The test is a point-in-polygon by the even-odd rule across **all** rings taken
together. That handles holes correctly as a side effect: standing in a hole of
the zone crosses two boundaries and therefore counts as outside.

What the bot **cannot** do: check elevation. It says whether your position lies
inside the area. The zone boundary applies at ground level — inside the polygon
means inside.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

AZ_BASIS = "https://az.sotl.as/"

# Beyond this the query is not worth making. Even on flat summit plateaus,
# zones are rarely wider than a few hundred metres.
MAX_ENTFERNUNG_KM = 3.0

# This many summits are checked in turn, nearest first. Between two summits the
# nearest one can be the wrong one.
MAX_GIPFEL = 3

TRKPT = re.compile(r'lat="([-\d.]+)"\s+lon="([-\d.]+)"')
TRKSEG = re.compile(r"<trkseg>(.*?)</trkseg>", re.S)


class KeineZone(RuntimeError):
    """SOTLAS has no polygon for this summit."""


def az_url(ref: str, endung: str = "gpx") -> str:
    """`OE/KT-048` -> `https://az.sotl.as/OE/KT/048.gpx`.

    Only the **first** hyphen is replaced; references such as `OE/KT-048` have
    only one anyway, but the bound keeps it predictable.
    """
    return f"{AZ_BASIS}{ref.replace('-', '/', 1)}.{endung}"


async def fetch_zone(client: httpx.AsyncClient, ref: str) -> list[list[tuple[float, float]]]:
    """Fetch the zone polygon. Raises `KeineZone` when SOTLAS has none."""
    resp = await client.get(az_url(ref), timeout=30.0)
    if resp.status_code == 404:
        raise KeineZone(ref)
    resp.raise_for_status()
    ringe = []
    for seg in TRKSEG.findall(resp.text):
        punkte = [(float(a), float(b)) for a, b in TRKPT.findall(seg)]
        if len(punkte) >= 4:              # fewer is not an area
            ringe.append(punkte)
    if not ringe:
        raise KeineZone(ref)
    return ringe


def innerhalb(punkt: tuple[float, float], ringe: list[list[tuple[float, float]]]) -> bool:
    """Even-odd across all rings together - holes come out right that way."""
    lat, lon = punkt
    drin = False
    for ring in ringe:
        for i in range(len(ring)):
            a, b = ring[i], ring[i - 1]
            if (a[0] > lat) != (b[0] > lat):
                schnitt = (b[1] - a[1]) * (lat - a[0]) / (b[0] - a[0]) + a[1]
                if lon < schnitt:
                    drin = not drin
    return drin


def abstand_rand_m(punkt: tuple[float, float],
                   ringe: list[list[tuple[float, float]]]) -> float:
    """Shortest distance to the zone boundary, in metres.

    Planar approximation with latitude compression. Over the few hundred metres
    in question the error is centimetres - the polygon's own grid spacing is
    coarser by orders of magnitude.
    """
    lat, lon = punkt
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(lat))
    px, py = lon * mlon, lat * mlat
    best = float("inf")
    for ring in ringe:
        for i in range(len(ring)):
            a, b = ring[i], ring[i - 1]
            ax, ay = a[1] * mlon, a[0] * mlat
            bx, by = b[1] * mlon, b[0] * mlat
            dx, dy = bx - ax, by - ay
            laenge = dx * dx + dy * dy
            t = 0.0 if laenge == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / laenge))
            best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def bewerte(gipfel: dict[str, Any], punkt: tuple[float, float],
            ringe: list[list[tuple[float, float]]]) -> dict[str, Any]:
    return {
        "drin": innerhalb(punkt, ringe),
        "rand_m": abstand_rand_m(punkt, ringe),
        "ref": gipfel["ref"],
        "name": gipfel["name"],
        "alt": gipfel["alt"],
        "pts": gipfel.get("pts"),
        "dist_km": gipfel.get("_d"),
        "richtung": gipfel.get("_r"),
    }


def _entfernung(m: float) -> str:
    return f"{m:.0f}m" if m < 1000 else f"{m/1000:.1f}km"


def render(w: dict[str, Any]) -> str:
    """One line: the verdict first, then the number it hangs on."""
    if w["drin"]:
        return (f"AZ {w['ref']} {w['name']} {w['alt']:.0f}m: JA - "
                f"{_entfernung(w['rand_m'])} bis zum Rand, {w['pts']}Pkt")
    gipfel = ""
    if w["dist_km"] is not None:
        gipfel = f", Gipfel {_entfernung(w['dist_km']*1000)} {w['richtung']}"
    return (f"AZ {w['ref']} {w['name']}: NEIN - {_entfernung(w['rand_m'])} "
            f"bis zur Zone{gipfel}")


def render_kein_gipfel(dist_km: float | None) -> str:
    if dist_km is None:
        return f"AZ: kein SOTA-Gipfel in {MAX_ENTFERNUNG_KM:.0f}km"
    return (f"AZ: kein Gipfel in {MAX_ENTFERNUNG_KM:.0f}km - naechster "
            f"{dist_km:.1f}km weg")


def render_keine_zone(gipfel: dict[str, Any]) -> str:
    """No polygon at SOTLAS. A refusal, not a substitute computation.

    A self-computed zone from a 25 m model would be dangerous here: it looks
    like an answer but fails on the ridge exactly where the question gets hard.
    """
    return (f"AZ {gipfel['ref']}: kein Polygon bei SOTLAS. "
            f"Gipfel {gipfel['alt']:.0f}m, Zone ab {gipfel['alt']-25:.0f}m - selbst messen")
