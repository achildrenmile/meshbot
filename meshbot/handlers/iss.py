"""!iss — nächster sichtbarer Überflug der Raumstation.

Bahndaten (TLE) von Celestrak, Bahnrechnung mit SGP4 — demselben Modell, mit
dem die Bahndaten erzeugt werden. Alles andere (Kepler-Näherungen) liegt nach
wenigen Stunden um Minuten daneben.

TLE altern: Nach etwa einer Woche wird die Vorhersage merklich ungenau. Der
Cache läuft deshalb nach 6 Stunden ab, und die Antwort verschweigt nicht, wenn
nur ein alter Datensatz da war.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import httpx
from sgp4.api import Satrec

RAD = math.pi / 180
A_ERDE = 6378.137            # WGS84, km
F_ERDE = 1 / 298.257223563
MIN_ELEVATION = 10.0         # darunter steht sie im Gelände, nicht am Himmel


async def fetch_tle(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    resp = await client.get(url, timeout=15.0)
    resp.raise_for_status()
    zeilen = [z.strip() for z in resp.text.splitlines() if z.strip()]
    for i, z in enumerate(zeilen):
        if z.startswith("1 ") and i + 1 < len(zeilen) and zeilen[i + 1].startswith("2 "):
            return z, zeilen[i + 1]
    raise ValueError("keine TLE im Dokument")


def _gmst(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t
    return (g % 360) * RAD


def _beobachter_ecef(lat: float, lon: float, hoehe_km: float = 0.0) -> tuple[float, float, float]:
    la, lo = lat * RAD, lon * RAD
    e2 = F_ERDE * (2 - F_ERDE)
    n = A_ERDE / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + hoehe_km) * math.cos(la) * math.cos(lo),
            (n + hoehe_km) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + hoehe_km) * math.sin(la))


def _blickwinkel(r_teme: tuple[float, float, float], jd: float,
                 lat: float, lon: float) -> tuple[float, float]:
    """(Elevation, Azimut) in Grad, vom Beobachter aus gesehen."""
    g = _gmst(jd)
    x = r_teme[0] * math.cos(g) + r_teme[1] * math.sin(g)
    y = -r_teme[0] * math.sin(g) + r_teme[1] * math.cos(g)
    z = r_teme[2]

    ox, oy, oz = _beobachter_ecef(lat, lon)
    dx, dy, dz = x - ox, y - oy, z - oz

    la, lo = lat * RAD, lon * RAD
    sued = math.sin(la) * math.cos(lo) * dx + math.sin(la) * math.sin(lo) * dy - math.cos(la) * dz
    ost = -math.sin(lo) * dx + math.cos(lo) * dy
    zenit = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz

    reichweite = math.sqrt(dx * dx + dy * dy + dz * dz)
    elevation = math.degrees(math.asin(zenit / reichweite))
    azimut = math.degrees(math.atan2(-ost, sued)) % 360
    return elevation, azimut


def naechster_ueberflug(tle: tuple[str, str], lat: float, lon: float, start: datetime,
                        stunden: int = 24, schritt_s: int = 30) -> dict | None:
    """Ersten Überflug über `MIN_ELEVATION` im Suchfenster finden."""
    sat = Satrec.twoline2rv(tle[0], tle[1])
    beginn = hoehepunkt = None
    max_el = -90.0
    az_auf = az_ab = 0.0

    for i in range(int(stunden * 3600 / schritt_s)):
        zeit = start + timedelta(seconds=i * schritt_s)
        jd = zeit.timestamp() / 86400.0 + 2440587.5
        fehler, r, _ = sat.sgp4(math.floor(jd) + 0.5, jd - (math.floor(jd) + 0.5))
        if fehler:
            continue
        el, az = _blickwinkel(r, jd, lat, lon)

        if el >= MIN_ELEVATION and beginn is None:
            beginn, az_auf = zeit, az
            max_el, hoehepunkt = el, zeit
        elif el >= MIN_ELEVATION:
            if el > max_el:
                max_el, hoehepunkt = el, zeit
            az_ab = az
        elif beginn is not None:
            return {"start": beginn, "max_el": max_el, "hoehepunkt": hoehepunkt,
                    "dauer_min": (zeit - beginn).total_seconds() / 60,
                    "az_auf": az_auf, "az_ab": az_ab}
    return None


def render(pass_: dict | None, tz_offset_h: int = 2, alt: bool = False) -> str:
    if pass_ is None:
        return "ISS: kein Ueberflug ueber 10 Grad in den naechsten 24h"
    from .geo import richtung
    zeit = (pass_["start"] + timedelta(hours=tz_offset_h)).strftime("%H:%M")
    marker = "~" if alt else ""
    return (f"ISS {marker}{zeit} max {pass_['max_el']:.0f}Grad, "
            f"{richtung(pass_['az_auf'])}>{richtung(pass_['az_ab'])}, "
            f"{pass_['dauer_min']:.0f}min")
