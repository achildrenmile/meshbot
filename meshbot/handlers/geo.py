"""Geometry and terrain — the shared basis for !dist, !hoehe and !sicht.

Everything here computes on the sphere, not the ellipsoid. Over the distances a
LoRa network deals with (up to ~150 km) the error stays below 0.3 % — well under
the uncertainty an elevation model with 25 m grid spacing brings anyway.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

R_ERDE = 6371.0
K_REFRAKTION = 4 / 3          # standard atmosphere: the beam bends along with it
F_GHZ = 0.869618              # EU-Preset, für den Fresnelradius

ZAHL = re.compile(r"-?\d{1,3}[.,]\d+")
HIMMELSRICHTUNG = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
                   "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def parse_punkte(text: str, anzahl: int = 2) -> list[tuple[float, float]] | None:
    """The first `anzahl` coordinate pairs from arbitrary text.

    Deliberately stubborn about decimal numbers: the MeshCore app shares
    positions sometimes as `46.6,13.8`, sometimes as `46.6 13.8`, sometimes
    embedded in a sentence. Trying to recognise separators loses against
    reality.
    """
    zahlen = [float(z.replace(",", ".")) for z in ZAHL.findall(text)]
    if len(zahlen) < 2 * anzahl:
        return None
    punkte = []
    for i in range(anzahl):
        lat, lon = zahlen[2 * i], zahlen[2 * i + 1]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        punkte.append((lat, lon))
    return punkte


def distanz_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_ERDE * math.asin(math.sqrt(h))


def peilung(a: tuple[float, float], b: tuple[float, float]) -> float:
    """True bearing from a to b, 0–360°."""
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dl = lo2 - lo1
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def richtung(grad: float) -> str:
    return HIMMELSRICHTUNG[int(grad / 22.5 + 0.5) % 16]


def zwischenpunkt(a: tuple[float, float], b: tuple[float, float], f: float) -> tuple[float, float]:
    """The point at fraction `f` along the great-circle path."""
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    if d == 0:
        return a
    A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
    x = A * math.cos(la1) * math.cos(lo1) + B * math.cos(la2) * math.cos(lo2)
    y = A * math.cos(la1) * math.sin(lo1) + B * math.cos(la2) * math.sin(lo2)
    z = A * math.sin(la1) + B * math.sin(la2)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


async def hoehen(client: httpx.AsyncClient, url: str,
                 punkte: list[tuple[float, float]]) -> list[float]:
    """Terrain elevations in metres. Raises when the source has gaps.

    A gap in the model must not slip through as "0 m above sea level" — that
    would turn into a clear line of sight straight over a mountain.
    """
    locs = "|".join(f"{p[0]:.5f},{p[1]:.5f}" for p in punkte)
    resp = await client.get(url, params={"locations": locs}, timeout=30.0)
    resp.raise_for_status()
    werte = [e.get("elevation") for e in resp.json()["results"]]
    if any(w is None for w in werte):
        raise ValueError("Hoehenmodell hat Luecken")
    return [float(w) for w in werte]


def fresnel_radius_m(d1: float, d2: float, gesamt: float) -> float:
    """First Fresnel zone, the tube that has to stay clear."""
    return 17.3 * math.sqrt(d1 * d2 / (F_GHZ * gesamt))


def erdkruemmung_m(d1: float, d2: float) -> float:
    return (d1 * d2 * 1000) / (2 * R_ERDE * K_REFRAKTION)


def bewerte_profil(hoehen_m: list[float], dist_km: float,
                   mast_a: float, mast_b: float) -> dict[str, Any]:
    """Find the tightest spot along the path.

    The measure is not "line of sight yes/no" but how much of the first Fresnel
    zone stays clear. A beam that just grazes the ridge is geometrically clear
    and radio-technically dead all the same — which is why the Fresnel fraction
    is in the answer and not merely a tick.
    """
    n = len(hoehen_m)
    h1, h2 = hoehen_m[0] + mast_a, hoehen_m[-1] + mast_b

    # The first and last metres do not count. Two reasons: there the Fresnel
    # radius is nearly zero by arithmetic, so any bump in the ground would yield
    # an absurd percentage -- and at that range the mounting (mast, roof edge,
    # tree) decides the link, not the terrain profile. An obstacle 50 m in front
    # of the antenna is visible without a computer.
    rand_km = min(0.5, dist_km * 0.05)

    eng: dict[str, Any] = {"anteil": 9e9}
    for i in range(1, n - 1):
        d1 = dist_km * i / (n - 1)
        d2 = dist_km - d1
        if d1 < rand_km or d2 < rand_km:
            continue
        sichtlinie = h1 + (h2 - h1) * d1 / dist_km - erdkruemmung_m(d1, d2)
        frei_m = sichtlinie - hoehen_m[i]
        r1 = fresnel_radius_m(d1, d2, dist_km)
        anteil = frei_m / r1 if r1 > 0 else 9e9
        if anteil < eng["anteil"]:
            eng = {"anteil": anteil, "km": d1, "gelaende": hoehen_m[i],
                   "frei_m": frei_m, "radius": r1}
    eng["dist"] = dist_km
    return eng


def render_sicht(eng: dict[str, Any]) -> str:
    """One line. The verdict first, then the number behind it."""
    anteil = eng["anteil"]
    if anteil <= 0:
        fehlt = -eng["frei_m"]
        return (f"Sicht {eng['dist']:.1f}km: BLOCKIERT bei km{eng['km']:.1f} "
                f"({eng['gelaende']:.0f}m, {fehlt:.0f}m zu hoch)")
    urteil = "FREI" if anteil >= 0.6 else "KNAPP"
    # Capped at 100 %: more than one entirely clear Fresnel zone buys nothing
    # further, and "685 %" reads like a bug.
    prozent = min(anteil, 1.0) * 100
    return (f"Sicht {eng['dist']:.1f}km: {urteil}, Fresnel {prozent:.0f}% "
            f"(enger bei km{eng['km']:.1f}, {eng['gelaende']:.0f}m)")


def render_dist(a: tuple[float, float], b: tuple[float, float]) -> str:
    d = distanz_km(a, b)
    p = peilung(a, b)
    rueck = (p + 180) % 360
    return f"{d:.1f}km, Peilung {p:.0f} {richtung(p)} (zurueck {rueck:.0f} {richtung(rueck)})"


def render_hoehe(punkt: tuple[float, float], meter: float) -> str:
    return f"Hoehe {punkt[0]:.4f},{punkt[1]:.4f}: {meter:.0f}m (EU-DEM 25m)"
