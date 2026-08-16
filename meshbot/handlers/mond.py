"""!mond — Mondauf- und -untergang sowie Phase.

Wie !sonne bewusst ohne Internetquelle: reine Rechnung, damit der Befehl auch
dann antwortet, wenn draußen alles hängt.

Genauigkeit: gekürzte Reihen nach Meeus (Astronomical Algorithms, Kap. 47).
Auf- und Untergang stimmen auf wenige Minuten — mehr braucht niemand, der
wissen will, ob er nachts ohne Stirnlampe vom Berg kommt.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

RAD = math.pi / 180
HORIZONT = 0.125          # Mondrand plus Refraktion minus Parallaxe, in Grad

# Hauptglieder der Mondlängen- und -abstandsreihe: D, M, M', F, Längenkoeff, Abstandskoeff
GLIEDER = [
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968), (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733), (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0), (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824), (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
]
# Breitenreihe
GLIEDER_B = [
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237), (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198), (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
]


def _jd(zeit: datetime) -> float:
    return zeit.timestamp() / 86400.0 + 2440587.5


def _position(jd: float) -> tuple[float, float]:
    """Rektaszension und Deklination des Mondes in Grad."""
    t = (jd - 2451545.0) / 36525.0
    L = 218.3164477 + 481267.88123421 * t - 0.0015786 * t * t
    D = 297.8501921 + 445267.1114034 * t - 0.0018819 * t * t
    M = 357.5291092 + 35999.0502909 * t
    Ms = 134.9633964 + 477198.8675055 * t + 0.0087414 * t * t
    F = 93.2720950 + 483202.0175233 * t - 0.0036539 * t * t
    e = 1 - 0.002516 * t

    sl = sb = 0.0
    for d, m, ms, f, kl, _ in GLIEDER:
        arg = (d * D + m * M + ms * Ms + f * F) * RAD
        sl += kl * math.sin(arg) * (e ** abs(m))
    for d, m, ms, f, kb in GLIEDER_B:
        arg = (d * D + m * M + ms * Ms + f * F) * RAD
        sb += kb * math.sin(arg) * (e ** abs(m))

    lam = (L + sl / 1_000_000) * RAD
    beta = (sb / 1_000_000) * RAD
    eps = (23.439291 - 0.0130042 * t) * RAD

    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps)
                    + math.cos(beta) * math.sin(eps) * math.sin(lam))
    return math.degrees(ra) % 360, math.degrees(dec)


def _hoehe(jd: float, lat: float, lon: float) -> float:
    """Höhe des Mondes über dem Horizont, in Grad."""
    ra, dec = _position(jd)
    t = (jd - 2451545.0) / 36525.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t) % 360
    h = (gmst + lon - ra) * RAD
    return math.degrees(math.asin(
        math.sin(lat * RAD) * math.sin(dec * RAD)
        + math.cos(lat * RAD) * math.cos(dec * RAD) * math.cos(h)))


def _phase(jd: float) -> tuple[float, bool]:
    """(beleuchteter Anteil 0–1, zunehmend?)"""
    t = (jd - 2451545.0) / 36525.0
    D = (297.8501921 + 445267.1114034 * t) % 360
    M = (357.5291092 + 35999.0502909 * t) % 360
    Ms = (134.9633964 + 477198.8675055 * t) % 360
    # Elongation Sonne–Mond, gekürzt
    i = (180 - D - 6.289 * math.sin(Ms * RAD) + 2.100 * math.sin(M * RAD)
         - 1.274 * math.sin((2 * D - Ms) * RAD) - 0.658 * math.sin(2 * D * RAD)
         - 0.214 * math.sin(2 * Ms * RAD) - 0.110 * math.sin(D * RAD))
    return (1 + math.cos(i * RAD)) / 2, (D % 360) < 180


def ereignisse(tag: date, lat: float, lon: float) -> dict[str, datetime | None]:
    """Auf- und Untergang des Tages in UTC. None heißt: findet heute nicht statt.

    Der Mond geht rund 50 Minuten später auf als tags zuvor — an manchen Tagen
    fällt Aufgang oder Untergang deshalb schlicht aus dem Kalendertag heraus.
    Das ist kein Fehler und wird auch nicht als solcher gemeldet.
    """
    start = datetime(tag.year, tag.month, tag.day, tzinfo=timezone.utc)
    jd0 = _jd(start)
    schritt = 1 / 144.0                       # 10 Minuten
    aufgang = untergang = None
    vorher = _hoehe(jd0, lat, lon) - HORIZONT

    for i in range(1, 145):
        jd = jd0 + i * schritt
        jetzt = _hoehe(jd, lat, lon) - HORIZONT
        if vorher * jetzt < 0:
            # lineare Interpolation auf die Nullstelle
            anteil = vorher / (vorher - jetzt)
            treffer = start + timedelta(days=(i - 1 + anteil) * schritt)
            if jetzt > 0 and aufgang is None:
                aufgang = treffer
            elif jetzt < 0 and untergang is None:
                untergang = treffer
        vorher = jetzt

    anteil, zunehmend = _phase(jd0 + 0.5)
    return {"aufgang": aufgang, "untergang": untergang,
            "anteil": anteil, "zunehmend": zunehmend}


def render(werte: dict, tz_offset_h: int = 2) -> str:
    def hm(dt: datetime | None) -> str:
        return "--:--" if dt is None else (dt + timedelta(hours=tz_offset_h)).strftime("%H:%M")

    anteil = werte["anteil"]
    if anteil > 0.96:
        phase = "Vollmond"
    elif anteil < 0.04:
        phase = "Neumond"
    else:
        phase = ("zunehmend " if werte["zunehmend"] else "abnehmend ") + f"{anteil * 100:.0f}%"
    return f"Mond: auf {hm(werte['aufgang'])}, unter {hm(werte['untergang'])}, {phase}"
