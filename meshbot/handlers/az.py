"""!az — stehe ich in der SOTA-Aktivierungszone?

Die Zone ist **keine Kreisfläche** um den Gipfel. Sie ist alles, was höchstens
25 Höhenmeter unter dem Gipfel liegt und mit ihm zusammenhängt — im Gelände
also eine krumme Fläche, die sich am Grat entlangzieht und am Steilhang
abrupt endet.

Deshalb wird hier **nicht gerechnet, sondern nachgeschlagen**: SOTLAS stellt
für jeden Gipfel das fertige Zonenpolygon bereit, erzeugt aus hochauflösenden
Höhenmodellen und in der SOTA-Gemeinschaft in Gebrauch:

    https://az.sotl.as/OE/KT/048.gpx     (WGS84, direkt verwendbar)
    https://az.sotl.as/OE/KT/048.geojson (EPSG:3035, bräuchte Umprojektion)

Genommen wird die GPX-Fassung: Sie steht schon in Grad und erspart eine
eigene Projektionsrechnung — eine Fehlerquelle weniger bei einer Frage, die
stimmen muss.

Der Test ist ein Punkt-in-Polygon nach der Even-odd-Regel über **alle** Ringe
zusammen. Das erledigt Löcher nebenbei richtig: Wer in einem Loch der Zone
steht, kreuzt zwei Ränder und liegt damit außerhalb.

Was der Bot **nicht** kann: Höhe prüfen. Er sagt, ob deine Position in der
Fläche liegt. Die Zonengrenze gilt am Boden — wer im Polygon steht, ist drin.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

AZ_BASIS = "https://az.sotl.as/"

# Weiter weg lohnt die Abfrage nicht. Zonen sind an flachen Gipfelplateaus
# selten breiter als ein paar hundert Meter.
MAX_ENTFERNUNG_KM = 3.0

# So viele Gipfel werden der Reihe nach geprueft, naechster zuerst. Zwischen
# zwei Gipfeln kann der naechstgelegene der falsche sein.
MAX_GIPFEL = 3

TRKPT = re.compile(r'lat="([-\d.]+)"\s+lon="([-\d.]+)"')
TRKSEG = re.compile(r"<trkseg>(.*?)</trkseg>", re.S)


class KeineZone(RuntimeError):
    """Fuer diesen Gipfel liegt bei SOTLAS kein Polygon."""


def az_url(ref: str, endung: str = "gpx") -> str:
    """`OE/KT-048` -> `https://az.sotl.as/OE/KT/048.gpx`.

    Nur der **erste** Bindestrich wird ersetzt; Referenzen wie `OE/KT-048`
    haben ohnehin nur einen, aber die Begrenzung haelt es vorhersagbar.
    """
    return f"{AZ_BASIS}{ref.replace('-', '/', 1)}.{endung}"


async def fetch_zone(client: httpx.AsyncClient, ref: str) -> list[list[tuple[float, float]]]:
    """Zonenpolygon holen. Wirft `KeineZone`, wenn SOTLAS keins hat."""
    resp = await client.get(az_url(ref), timeout=30.0)
    if resp.status_code == 404:
        raise KeineZone(ref)
    resp.raise_for_status()
    ringe = []
    for seg in TRKSEG.findall(resp.text):
        punkte = [(float(a), float(b)) for a, b in TRKPT.findall(seg)]
        if len(punkte) >= 4:              # weniger ist keine Flaeche
            ringe.append(punkte)
    if not ringe:
        raise KeineZone(ref)
    return ringe


def innerhalb(punkt: tuple[float, float], ringe: list[list[tuple[float, float]]]) -> bool:
    """Even-odd ueber alle Ringe gemeinsam - Loecher fallen damit richtig aus."""
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
    """Kuerzester Abstand zum Zonenrand in Metern.

    Ebene Naeherung mit Breitengrad-Stauchung. Ueber die paar hundert Meter,
    um die es geht, liegt der Fehler im Zentimeterbereich - die Rasterweite
    des Polygons ist um Groessenordnungen groeber.
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
    """Eine Zeile: Urteil zuerst, dann die Zahl, an der es haengt."""
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
    """Kein Polygon bei SOTLAS. Keine Ersatzrechnung, sondern eine Absage.

    Eine selbst gerechnete Zone aus einem 25-m-Modell waere hier gefaehrlich:
    Sie sieht aus wie eine Antwort, taugt aber am Grat genau dort nicht, wo
    die Frage schwierig wird.
    """
    return (f"AZ {gipfel['ref']}: kein Polygon bei SOTLAS. "
            f"Gipfel {gipfel['alt']:.0f}m, Zone ab {gipfel['alt']-25:.0f}m - selbst messen")
