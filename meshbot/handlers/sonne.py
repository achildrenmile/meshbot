"""!sonne — sunrise and sunset for a position.

Deliberately without an external source: pure arithmetic following the NOAA
solar position algorithm. That way the command works even when the bot has no
internet — and it answers without any wait.

Returned are sunrise, sunset and the end of civil twilight, because on a tour
the last of those is the number that actually matters: until then you can get
off the mountain without a headlamp.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

ZENIT_AUFGANG = 90.833      # Sonnenmitte plus Refraktion
ZENIT_DAEMMERUNG = 96.0     # bürgerliche Dämmerung


def _ereignis(tag: date, lat: float, lon: float, zenit: float, aufgang: bool) -> datetime | None:
    """Time of a solar event in UTC, or None when it does not occur.

    Solar position equation after NOAA. `None` stands for polar day or polar
    night — never in Carinthia, but certainly further north.
    """
    n = tag.toordinal() - date(2000, 1, 1).toordinal()
    # Western longitude is positive in this equation, eastern negative.
    j_stern = n + 0.0009 + (-lon) / 360

    m = (357.5291 + 0.98560028 * j_stern) % 360                  # mittlere Anomalie
    c = (1.9148 * math.sin(math.radians(m))
         + 0.0200 * math.sin(math.radians(2 * m))
         + 0.0003 * math.sin(math.radians(3 * m)))               # Mittelpunktsgleichung
    lam = (m + c + 180 + 102.9372) % 360                         # ekliptische Laenge
    j_transit = (2451545.0 + j_stern
                 + 0.0053 * math.sin(math.radians(m))
                 - 0.0069 * math.sin(math.radians(2 * lam)))     # Sonnenhoechststand
    dek = math.degrees(math.asin(math.sin(math.radians(lam)) * math.sin(math.radians(23.44))))

    zaehler = math.cos(math.radians(zenit)) - math.sin(math.radians(lat)) * math.sin(math.radians(dek))
    nenner = math.cos(math.radians(lat)) * math.cos(math.radians(dek))
    if nenner == 0 or abs(zaehler / nenner) > 1:
        return None                                              # the sun neither rises nor sets
    stundenwinkel = math.degrees(math.acos(zaehler / nenner))

    jd = j_transit + (-stundenwinkel if aufgang else stundenwinkel) / 360
    return datetime.fromtimestamp((jd - 2440587.5) * 86400, tz=timezone.utc)


def berechne(lat: float, lon: float, jetzt: datetime) -> dict[str, datetime | None]:
    tag = jetzt.date()
    return {
        "aufgang": _ereignis(tag, lat, lon, ZENIT_AUFGANG, True),
        "untergang": _ereignis(tag, lat, lon, ZENIT_AUFGANG, False),
        "daemmerung": _ereignis(tag, lat, lon, ZENIT_DAEMMERUNG, False),
    }


def render(werte: dict[str, datetime | None], jetzt: datetime, tz_offset_h: int = 2) -> str:
    """Print local time — on a tour nobody cares about UTC."""
    def hm(dt: datetime | None) -> str:
        if dt is None:
            return "--:--"
        return (dt + timedelta(hours=tz_offset_h)).strftime("%H:%M")

    untergang = werte["untergang"]
    text = f"Sonne: auf {hm(werte['aufgang'])}, unter {hm(untergang)}, dunkel {hm(werte['daemmerung'])}"
    if untergang is not None:
        rest = (untergang - jetzt).total_seconds() / 60
        if 0 < rest < 600:                       # nur solange es hilft
            text += f" (noch {int(rest // 60)}h{int(rest % 60):02d})"
    return text
