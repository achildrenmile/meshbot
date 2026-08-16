"""!sonne — Sonnenauf- und -untergang für eine Position.

Bewusst ohne externe Quelle: reine Rechnung nach dem NOAA-Sonnenstandsalgorithmus.
Damit funktioniert der Befehl auch dann, wenn der Bot kein Internet hat — und er
antwortet ohne Wartezeit.

Zurückgegeben werden Aufgang, Untergang und das Ende der bürgerlichen Dämmerung,
weil Letzteres auf Tour die eigentlich interessante Zahl ist: bis dahin kommt man
ohne Stirnlampe vom Berg.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

ZENIT_AUFGANG = 90.833      # Sonnenmitte plus Refraktion
ZENIT_DAEMMERUNG = 96.0     # bürgerliche Dämmerung


def _ereignis(tag: date, lat: float, lon: float, zenit: float, aufgang: bool) -> datetime | None:
    """Zeitpunkt eines Sonnenereignisses in UTC, oder None wenn es ihn nicht gibt.

    Sonnenstandsgleichung nach NOAA. `None` steht fuer Polartag oder Polarnacht —
    in Kaernten nie, weiter noerdlich sehr wohl.
    """
    n = tag.toordinal() - date(2000, 1, 1).toordinal()
    # Westliche Laenge ist positiv in dieser Gleichung, oestliche negativ.
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
        return None                                              # Sonne geht nicht auf oder unter
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
    """Ortszeit ausgeben — auf Tour interessiert niemanden UTC."""
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
