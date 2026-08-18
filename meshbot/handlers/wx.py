"""!wx — aktuelle Messwerte einer TAWES-Station der GeoSphere Austria.

Quelle: dataset.api.hub.geosphere.at, Datensatz `tawes-v1-10min`, frei nutzbar
unter CC BY 4.0. Die Zuordnung Ort → Station steht in `data/stations_ktn.json`
und wurde aus der Stationsliste der GeoSphere erzeugt (nächstgelegene Station).
Die Ortsnamen selbst stammen aus OpenStreetMap (ODbL) und werden von
`tools/build_orte.py` erzeugt: Kärnten hat 34 Wetterstationen, aber dreitausend
Orte — wer in Knappenberg steht, tippt "Knappenberg" und nicht "Friesach".
"""

from __future__ import annotations

import json
import math
import re
from difflib import get_close_matches
from typing import Any

import httpx

from ..config import Settings
from .sota import parse_coords

# Klammerzusaetze sind keine Ortsangabe: die Station "Klagenfurt-Flughafen
# (Automat)" ist fuer den Fragenden schlicht Klagenfurt-Flughafen.
KLAMMER = re.compile(r"\s*\([^)]*\)")

PARAMS = "TL,RF,FFAM,DD,P"          # Temperatur, Feuchte, Wind, Richtung, Druck
HIMMELSRICHTUNG = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def _richtung(grad: float | None) -> str:
    if grad is None:
        return ""
    return HIMMELSRICHTUNG[int((grad % 360) / 45 + 0.5) % 8]


def load_stations(settings: Settings) -> dict[str, Any]:
    """Ortszuordnung und vollstaendige Stationsliste.

    Die Ortszuordnung deckt die gaengigen Namen ab, die Stationsliste erlaubt
    die Suche ueber Koordinaten — am Berg tippt niemand einen Ortsnamen, aber
    das Geraet kennt die Position.
    """
    with open(settings.stations_file, encoding="utf-8") as fh:
        daten = json.load(fh)
    if "orte" not in daten:            # altes Format ohne Stationsliste
        return {"orte": daten, "stationen": []}
    return daten


def distanz_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def station_bei(stationen: list[dict[str, Any]], lat: float, lon: float) -> dict[str, Any] | None:
    """Naechstgelegene Wetterstation zu einer Position."""
    if not stationen:
        return None
    return min(stationen, key=lambda s: distanz_km(lat, lon, s["lat"], s["lon"]))


def normalisiere(name: str) -> str:
    """Ortsname auf die Schreibweise des Verzeichnisses bringen.

    Muss zu `tools/build_orte.py` passen, sonst findet der Schluessel seinen
    Eintrag nicht. Punkte und Bindestriche fallen weg, damit "St.Veit",
    "St-Veit" und "Sankt Veit" alle bei "st veit" landen. Den Schraegstrich
    kennt nur diese Seite: im Verzeichnis trennt er die zweisprachigen Namen
    in zwei Eintraege, in einer Anfrage ist er ein Tippfehler.
    """
    s = KLAMMER.sub("", " ".join(name.split())).lower()
    for alt, neu in (("ö", "oe"), ("ä", "ae"), ("ü", "ue"), ("ß", "ss"),
                     ("š", "s"), ("č", "c"), ("ž", "z"),
                     (".", " "), ("-", " "), ("/", " "), ("'", ""), ("`", "")):
        s = s.replace(alt, neu)
    # "Bad Sankt Leonhard" und "Bad St. Leonhard" sind derselbe Ort.
    return " ".join("st" if w == "sankt" else w for w in s.split())


def resolve_place(arg: str, stations: dict[str, Any], default: str) -> tuple[str, dict[str, Any]] | None:
    """Ort oder Position auf eine Station abbilden.

    Akzeptiert einen Ortsnamen (mit Tippfehler-Toleranz) oder Koordinaten in
    beliebiger Schreibweise. Bei Koordinaten wird die naechstgelegene Station
    genommen und ihr Name zurueckgegeben — damit sieht der Empfaenger, woher
    die Werte stammen.
    """
    orte = stations.get("orte", stations)

    koord = parse_coords(arg)
    if koord is not None:
        s = station_bei(stations.get("stationen", []), *koord)
        if s is not None:
            return s["name"], {"station_id": s["id"], "station": s["name"],
                               "lat": s["lat"], "lon": s["lon"]}
        return None

    key = normalisiere(arg) or normalisiere(default)
    if key in orte:
        return key, orte[key]
    # Bei dreitausend Ortsnamen findet eine lockere Schwelle zu jedem Tippfehler
    # irgendeinen Weiler. 0.8 laesst "vilach" durch und "xyz" nicht.
    treffer = get_close_matches(key, list(orte), n=1, cutoff=0.8)
    if treffer:
        return treffer[0], orte[treffer[0]]
    return None


async def fetch(client: httpx.AsyncClient, settings: Settings, station_id: str) -> dict[str, Any]:
    resp = await client.get(
        settings.geosphere_tawes_url,
        params={"parameters": PARAMS, "station_ids": station_id, "output_format": "geojson"},
    )
    resp.raise_for_status()
    data = resp.json()
    props = data["features"][0]["properties"]["parameters"]
    return {name: (props.get(name) or {}).get("data", [None])[0] for name in PARAMS.split(",")}


# Antworten auf einen Ort, den es nicht gibt. Ausgewaehlt wird nach der
# Quersumme der Anfrage, nicht zufaellig: Derselbe Tippfehler bekommt immer
# dieselbe Antwort, das bleibt pruefbar und wirkt weniger wie eine Maschine,
# die wuerfelt.
SPOTT = [
    "{ort}? 🧐 Kenn i ned. Da war die Landkarte schneller: !wx villach",
    "{ort} 🗺️❓ Keiner von 3199 Orten. Probier: !wx villach",
    "{ort}? 🤨 Nie gehoert. Tipp: einen Ort in Kaernten waehlen",
    "{ort} 🫠 gibt's ned. Nochmal, diesmal mit Ort: !wx spittal",
    "{ort}? 🔍🤷 Nix gfunden. Position geht immer: !wx 46.61 13.85",
]

# Orte, die es erklaertermassen nicht gibt. Wer die tippt, hat sich keinen
# Tippfehler geleistet, sondern einen Scherz gemacht — der darf zurueckkommen.
SPEZIAL = {
    "hintertupfing": "Hintertupfing 🙄🏚️🐄 Erfunden. Wie deine Ortskenntnis. Nimm Villach",
    "kleinkleckersdorf": "Kleinkleckersdorf 🙄🐓 Auch beim dritten Versuch erfunden",
    "bielefeld": "Bielefeld 🛸🤫 Gibt's bekanntlich ned. Falscher Bot fuer Verschwoerungen",
    "entenhausen": "Entenhausen 🦆💰 Wetter dort: Comic. Hier: Kaernten",
    "absurdistan": "Absurdistan 🤡🌍 Liegt knapp ausserhalb unserer 34 Stationen",
    "timbuktu": "Timbuktu 🐪🏜️ 4700 km zu weit. Kaernten faengt bei Villach an",
    "walachei": "Walachei 🐺🌲 Nicht mal die Karawanken sind so weit weg",
    "mordor": "Mordor 🌋👁️ Ein Ort geht ned einfach so hinein. Nimm !wx villach",
    "nirgendwo": "Nirgendwo 🕳️🤷 Genau dort ist auch deine Wetterstation",
    "buxtehude": "Buxtehude 🐕🐰 Liegt 900 km nordwestlich. Knapp daneben",
}


def render_unbekannt(arg: str) -> str:
    """Der Ort ist nicht im Verzeichnis.

    Kostet dieselbe Sendezeit wie die alte Absage `WX: <ort> unbekannt`, sagt
    aber dazu, wie es richtig geht — sonst waere es nur Spott ohne Nutzen.
    """
    ort = " ".join(arg.split())[:20] or "Nix"
    key = normalisiere(ort)
    if key in SPEZIAL:
        return "WX: " + SPEZIAL[key]
    # Auf der normalisierten Form waehlen, sonst bekommt "villagh" eine andere
    # Antwort als "Villagh" — derselbe Tippfehler soll dieselbe bleiben.
    return "WX: " + SPOTT[sum(key.encode()) % len(SPOTT)].format(ort=ort.title())


def render(ort: str, werte: dict[str, Any], stale: bool = False,
           station: str | None = None) -> str:
    """Eine Zeile, feste Reihenfolge: Temperatur, Feuchte, Wind, Druck.

    Steht die Station woanders als der gefragte Ort, wird sie mitgenannt. In
    Knappenberg misst niemand — die Werte kommen aus Friesach, und das muss
    dranstehen, sonst haelt es jemand fuer eine Messung vor der Haustuer.
    """
    marker = "~" if stale else ""
    kopf = ort.title()
    if station and normalisiere(station) != normalisiere(ort):
        kopf = f"{kopf} ({station})"
    teile = [f"WX {kopf}: {marker}"]
    if werte.get("TL") is not None:
        teile.append(f"{werte['TL']:.1f}C")
    if werte.get("RF") is not None:
        teile.append(f"{werte['RF']:.0f}%")
    if werte.get("FFAM") is not None:
        kmh = werte["FFAM"] * 3.6
        teile.append(f"Wind {kmh:.0f}km/h {_richtung(werte.get('DD'))}".strip())
    if werte.get("P") is not None:
        teile.append(f"{werte['P']:.0f}hPa")
    return teile[0] + ", ".join(teile[1:])
