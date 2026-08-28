"""Gipfelwetter — `!wx <berggipfel>`, wenn der Name kein Kaerntner Ort ist.

Die Wetterstationen der GeoSphere enden an der Staatsgrenze, und auf einem
Gipfel steht ohnehin selten eine. Fuer Berge kommt deshalb ein **Modellwert**
von Open-Meteo, gerechnet auf die Gipfelhoehe.

**Das ist keine Messung, und die Antwort sagt das auch** -- sie endet auf
`(Modell)`. Das ist ehrlicher als eine Zahl, die aussieht wie ein Thermometer
am Gipfelkreuz. Wer den Unterschied nicht sieht, plant eine Tour nach einem
Modell und haelt es fuer eine Messung.

Das Gipfelverzeichnis kommt aus der SOTA-Liste und deckt Oesterreich, Italien,
Slowenien, Deutschland, die Schweiz, Kroatien, Tschechien, die Slowakei,
Ungarn und Polen ab -> `tools/build_gipfel.py`.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

import httpx

from .wx import normalisiere

# Windrichtung wie bei !wx, damit beide Antworten gleich zu lesen sind.
RICHTUNGEN = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

# Kein Luftdruck. Open-Meteo liefert ihn als `pressure_msl`, also auf
# Meereshoehe zurueckgerechnet -- auf einem Gipfel ist das kein Messwert von
# dort, sondern eine Regionalzahl, die ueberall gleich aussieht. Sie kostete
# neun Zeichen und hat die Antwort ueber 60 Zeichen gehoben, und genau dort
# faellt die Zustellquote im Funknetz von 92 auf 47 Prozent.
FELDER = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
          "wind_direction_10m")

# Die SOTA-Liste benennt den **hoechsten Punkt**, der Volksmund das **Massiv**.
# Wer "Koralpe" tippt, meint den Grossen Speikkogel -- unter "Koralpe" steht
# dort nichts. Ohne diese Tabelle landet so eine Anfrage in der
# Aehnlichkeitssuche und bekommt irgendeinen fremden Berg zurueck.
#
# Nur gepruefte Eintraege: Jeder Schluessel rechts muss im Verzeichnis stehen,
# sonst faellt die Suche wieder aufs Raten zurueck.
ALIASE = {
    "koralpe": "grosser speikkogel",
    "koralm": "grosser speikkogel",
    "saualpe": "ladinger spitz",
    "kellerwand": "hohe warte",
    "coglians": "hohe warte",
    # Einwortnamen, bei denen die Wortgrenze nicht hilft: "Glockner" steckt
    # mitten in "Grossglockner", "Obir" mitten in "Hochobir". Bei
    # mehrwortigen Namen wie "Grosser Hafner" oder "Hoher Sonnblick" braucht
    # es das nicht -- da trifft die Wortstufe von selbst.
    "glockner": "grossglockner",
    "obir": "hochobir",
    # Der Hochstuhl steht unter seinem slowenischen Namen in der Liste
    # (S5/KA-001, 2236 m). Der Berg ist derselbe, die Grenze laeuft ueber ihn.
    "hochstuhl": "stol",
    "stou": "stol",
}

# Berge, die es gibt -- nur nicht in der SOTA-Liste. Sie fuehrt nur Gipfel mit
# genug Schartenhoehe; Kaernten hat dort 282 Eintraege, und Petzen, Kornock,
# Falkert und die Koschuta sind nicht darunter.
#
# Ohne diese Tabelle raet die Aehnlichkeitssuche: "Petzen" wurde zu "Pletzen"
# (ein anderer Berg), "Kornock" zu "Koflernock". Ein Alias hilft nicht -- es
# gibt nichts, worauf er zeigen koennte. Also die Wahrheit sagen.
#
# Hier gehoert nur hinein, was wirklich fehlt: Der Hochstuhl sah lange danach
# aus und steht doch drin, unter "Stol". Ein falscher Eintrag hier sperrt einen
# Gipfel aus, den es gibt -- deshalb prueft ein Test jeden Namen gegen das
# Verzeichnis.
FEHLT = {"petzen", "peca", "kornock", "falkert", "koschuta", "kosuta"}


def index(gipfel: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Namensverzeichnis, normalisiert wie das Ortsverzeichnis.

    Mehrere Gipfel teilen sich Namen -- bei einer Dublette gewinnt der hoehere.
    Wer nach "Hochwart" fragt, meint den Berg, nicht den Huegel daneben.
    """
    aus: dict[str, dict[str, Any]] = {}
    for g in gipfel:
        for teil in _namensvarianten(g["name"]):
            k = normalisiere(teil)
            if not k:
                continue
            alt = aus.get(k)
            if alt is None or (g.get("alt") or 0) > (alt.get("alt") or 0):
                aus[k] = g
    return aus


def _namensvarianten(name: str) -> list[str]:
    """`Villacher Alpe (Dobratsch)` findet man unter beiden Namen.

    Dasselbe bei `Matterhorn/Mont Cervin/Monte Cervino` und bei Bindestrich-
    Doppelnamen: Wer den slowenischen oder italienischen Namen tippt, meint
    denselben Berg.
    """
    varianten = [name]
    if "(" in name and ")" in name:
        vorne = name[: name.index("(")].strip()
        drin = name[name.index("(") + 1: name.rindex(")")].strip()
        varianten += [vorne, drin]
    for trenner in ("/", " – ", " - "):
        if trenner in name:
            varianten += [t.strip() for t in name.split(trenner)]
    return [v for v in varianten if v]


def suche_stufe(verzeichnis: dict[str, dict[str, Any]], begriff: str,
                fuzzy: bool = True) -> tuple[dict[str, Any] | None, str]:
    """Genau, dann ganzes Wort, dann Aehnlichkeit -- und sagt, welche Stufe traf.

    Die Stufe ist `exakt`, `wort`, `geraten` oder `keiner`. Die ersten beiden
    sind Wissen, `geraten` ist eine Vermutung und wird in der Antwort
    gekennzeichnet.

    **Ganzes Wort, nicht Teilstring.** Frueher genuegte es, dass die Anfrage
    irgendwo im Namen vorkam -- mitten im Wort. So wurde "Eckwand" zu
    "Bl-eckwand" und "Lienz" zu "Sandegg - Lienz-er". Gemessen an zwei Dutzend
    echten Bergabfragen kostet die Verschaerfung genau einen Treffer
    (`glockner` -> Grossglockner); alle anderen trafen ohnehin exakt.
    """
    k = normalisiere(begriff)
    if not k:
        return None, "keiner"
    if k in FEHLT:
        return None, "fehlt"
    k = ALIASE.get(k, k)
    if k in verzeichnis:
        return verzeichnis[k], "exakt"
    treffer = [g for name, g in verzeichnis.items() if f" {k} " in f" {name} "]
    if treffer:
        return max(treffer, key=lambda g: g.get("alt") or 0), "wort"
    if not fuzzy:
        return None, "keiner"
    nah = get_close_matches(k, list(verzeichnis), n=1, cutoff=0.82)
    return (verzeichnis[nah[0]], "geraten") if nah else (None, "keiner")


def suche(verzeichnis: dict[str, dict[str, Any]], begriff: str,
          fuzzy: bool = True) -> dict[str, Any] | None:
    """Wie `suche_stufe`, nur ohne die Stufe -- fuer Aufrufer, die sie nicht brauchen."""
    return suche_stufe(verzeichnis, begriff, fuzzy)[0]


def station_am_gipfel(stationen: list[dict[str, Any]], gipfel: dict[str, Any],
                      km: float = 3.0, hoehendiff: float = 300.0) -> dict[str, Any] | None:
    """Steht eine Wetterstation praktisch auf diesem Gipfel?

    Auf ein paar Bergen misst wirklich jemand -- auf dem Dobratsch etwa steht
    die Station "Villacher Alpe", 200 m vom Gipfelkreuz und 49 m tiefer. Dort
    ein Modell zu rechnen waere absurd: **Gemessen schlaegt gerechnet.**

    Beide Bedingungen muessen gelten. Naehe allein genuegt nicht -- eine
    Talstation kann in der Luftlinie nah sein und trotzdem 1500 m tiefer
    liegen, und dann misst sie ein anderes Wetter.
    """
    bester = None
    for st in stationen:
        if st.get("hoehe") is None:
            continue
        if abs(st["hoehe"] - gipfel["alt"]) > hoehendiff:
            continue
        d = _entfernung_km(gipfel["lat"], gipfel["lon"], st["lat"], st["lon"])
        if d <= km and (bester is None or d < bester[0]):
            bester = (d, st)
    return bester[1] if bester else None


def _entfernung_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


async def fetch(client: httpx.AsyncClient, url: str, gipfel: dict[str, Any]) -> dict[str, Any]:
    """Modellwerte fuer die Gipfelposition, auf die Gipfelhoehe gerechnet.

    `elevation` ist der Punkt an der Sache: Ohne diesen Parameter antwortet das
    Modell fuer die mittlere Hoehe seiner Gitterzelle, und die liegt bei einem
    Gipfel regelmaessig mehrere hundert Meter zu tief.
    """
    r = await client.get(url, params={
        "latitude": gipfel["lat"], "longitude": gipfel["lon"],
        "elevation": gipfel["alt"], "current": ",".join(FELDER),
        "wind_speed_unit": "kmh",
    }, timeout=20.0)
    r.raise_for_status()
    daten = r.json().get("current") or {}
    if daten.get("temperature_2m") is None:
        raise ValueError("Modell ohne Temperatur")
    return daten


def render_unbekannt(begriff: str) -> str:
    """Kein Gipfel dieses Namens -- und das ist eine brauchbare Auskunft.

    Die SOTA-Liste fuehrt nur Gipfel mit genug Schartenhoehe; Kaernten hat dort
    282 Eintraege. Petzen, Kornock und Hochstuhl fehlen ihr. Frueher bekam man
    dafuer den aehnlichsten fremden Berg, jetzt die Wahrheit plus einen Weg,
    trotzdem an Werte zu kommen.
    """
    name = " ".join(begriff.split())[:20] or "Nix"
    if normalisiere(begriff) in FEHLT:
        # Kein Tippfehler, sondern eine Luecke in der Quelle. Das gehoert
        # anders beantwortet als "kenn ich nicht".
        return f"{name.title()}: fehlt der SOTA-Liste. Mit Position gehts: !wx 46.6 13.8"
    return f"{name.title()}: kein Gipfel in der SOTA-Liste. Position geht: !wx 46.6 13.8"


def kurzname(name: str, grenze: int = 22) -> str:
    """Lange Doppelnamen auf den Bergnamen zusammenziehen.

    `Punta Penia – Marmolada` oder `Matterhorn/Mont Cervin/Monte Cervino`
    sprengen sonst die Nachricht. Hart abschneiden geht nicht -- daraus wird
    `Punta Penia – Marmolad`, und das ist kein Berg, das ist ein Tippfehler.
    Genommen wird deshalb die **kuerzeste** der Namensvarianten: meistens der
    Name, unter dem der Berg bekannt ist.
    """
    if len(name) <= grenze:
        return name
    varianten = [v for v in _namensvarianten(name) if 3 < len(v) <= grenze]
    return min(varianten, key=len) if varianten else name[:grenze].rsplit(" ", 1)[0]


def render(gipfel: dict[str, Any], w: dict[str, Any], stale: bool = False,
           geraten: bool = False) -> str:
    """Eine Zeile, dieselbe Reihenfolge wie !wx -- plus Hoehe und Modellhinweis.

    `geraten` haengt ein Fragezeichen an den Bergnamen. Die SOTA-Liste kennt
    nicht jeden Berg -- Petzen, Kornock und Hochstuhl fehlen ihr etwa ganz.
    Fuer die fand die Aehnlichkeitssuche bisher stillschweigend einen fremden
    Gipfel: "Kornock" wurde zu "Koflernock", "Petzen" zu "Pletzen".
    """
    marker = "~" if stale else ""
    name = kurzname(gipfel["name"]) + ("?" if geraten else "")
    teile = [f"WX {name} {gipfel['alt']}m: {marker}"]
    werte = []
    if w.get("temperature_2m") is not None:
        werte.append(f"{w['temperature_2m']:.1f}C")
    if w.get("relative_humidity_2m") is not None:
        werte.append(f"{int(w['relative_humidity_2m'])}%")
    if w.get("wind_speed_10m") is not None:
        wind = f"Wind {w['wind_speed_10m']:.0f}km/h"
        if w.get("wind_direction_10m") is not None:
            wind += " " + RICHTUNGEN[round(w["wind_direction_10m"] / 22.5) % 16]
        werte.append(wind)
    return teile[0] + ", ".join(werte) + " (Modell)"
