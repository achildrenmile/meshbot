"""Erzeugt das Ortsverzeichnis fuer den MeshBot aus OSM-Ortsknoten (Kaernten).

Schreibt data/stations_ktn.json neu — Stationsliste und gepflegte Eintraege
bleiben, das Ortsverzeichnis wird ersetzt. Quelle: OpenStreetMap, ODbL.

    python3 tools/build_orte.py [ziel] [gepflegte-orte]

Zwei Abfragen, beide mit Bounding-Box — eine Suche allein ueber das Tag
`ISO3166-2` laeuft bei Overpass in den Timeout.
"""

import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = "46.35,12.60,47.15,15.10"          # Kaernten mit Rand

Q_ORTE = f"""[out:json][timeout:120];
node["place"~"^(city|town|village|hamlet|suburb|isolated_dwelling)$"]({BBOX});
out body;"""

Q_GRENZE = f"""[out:json][timeout:120];
rel({BBOX})["ISO3166-2"="AT-2"]["boundary"="administrative"];
out geom;"""

RANG = {"city": 6, "town": 5, "village": 4, "suburb": 3,
        "hamlet": 2, "isolated_dwelling": 1}

# Ortsnamen bekommen nur Talstationen. Arnoldstein liegt auf 580 m, die
# Villacher Alpe auf 2117 m und ist trotzdem die naechste Station — ohne diese
# Grenze antwortet "!wx arnoldstein" mit zehn Grad zu wenig. Wer die Bergwerte
# will, fragt mit Position; dort gilt die Grenze nicht.
TALGRENZE_M = 1100.0


def frage(query: str, versuche: int = 4) -> dict:
    """Overpass ist ein freier Dienst und wehrt Last mit 504 ab.

    Das ist kein Fehler, sondern eine Bitte um Geduld — also warten und
    wiederholen, statt den Lauf abzubrechen.
    """
    daten = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=daten,
                                 headers={"User-Agent": "meshbot-ortsverzeichnis/1.0"})
    for n in range(versuche):
        try:
            with urllib.request.urlopen(req, timeout=200) as fh:
                return json.load(fh)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 504) or n == versuche - 1:
                raise
            pause = 30 * (n + 1)
            print(f"Overpass {e.code}, warte {pause}s", file=sys.stderr)
            time.sleep(pause)
    raise RuntimeError("unerreichbar")


def kanten(grenze: dict) -> list[tuple[float, float, float, float]]:
    """Alle Grenzsegmente als (lat1, lon1, lat2, lon2).

    Die Ringe muessen nicht sortiert sein: der Strahlensatz-Test zaehlt nur
    Schnitte, und Loecher (role=inner) heben sich dabei von selbst auf.
    """
    out = []
    for rel in grenze["elements"]:
        for m in rel.get("members", []):
            geo = m.get("geometry") or []
            for a, b in zip(geo, geo[1:]):
                out.append((a["lat"], a["lon"], b["lat"], b["lon"]))
    return out


def drinnen(lat: float, lon: float, kanten_liste) -> bool:
    innen = False
    for lat1, lon1, lat2, lon2 in kanten_liste:
        if (lat1 > lat) != (lat2 > lat):
            schnitt = lon1 + (lat - lat1) / (lat2 - lat1) * (lon2 - lon1)
            if lon < schnitt:
                innen = not innen
    return innen


def distanz_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# "(ehem.) Hader", "Bach (Zweinitz)": OSM haengt Zusaetze in Klammern an. Der
# Zusatz ist keine Ortsangabe, unter der jemand fragt.
KLAMMER = re.compile(r"\s*\([^)]*\)")


def normalisiere(name: str) -> str:
    s = KLAMMER.sub("", " ".join(name.split())).lower()
    for alt, neu in (("ö", "oe"), ("ä", "ae"), ("ü", "ue"), ("ß", "ss"),
                     ("š", "s"), ("č", "c"), ("ž", "z"),
                     (".", " "), ("-", " "), ("'", ""), ("`", "")):
        s = s.replace(alt, neu)
    # "Bad Sankt Leonhard" und "Bad St. Leonhard" sind derselbe Ort.
    return " ".join("st" if w == "sankt" else w for w in s.split())


def main(ziel: str, gepflegt_datei: str) -> None:
    """Erzeugt `ziel` neu — Ein- und Ausgabedatei sind dieselbe.

    Die Stationsliste wird uebernommen, das Ortsverzeichnis komplett ersetzt.
    Wuerde stattdessen der alte Bestand als Vorgabe gelten, ueberlebte jeder
    einmal erzeugte Eintrag jeden weiteren Lauf — auch ein falscher.
    """
    with open(ziel, encoding="utf-8") as fh:
        daten = json.load(fh)
    stationen = daten["stationen"]
    with open(gepflegt_datei, encoding="utf-8") as fh:
        bestand = json.load(fh)

    grenze = kanten(frage(Q_GRENZE))
    print(f"Grenze: {len(grenze)} Segmente", file=sys.stderr)
    elemente = frage(Q_ORTE)["elements"]
    print(f"OSM: {len(elemente)} Ortsknoten in der Box", file=sys.stderr)

    elemente = [e for e in elemente if drinnen(e["lat"], e["lon"], grenze)]
    print(f"davon in Kaernten: {len(elemente)}", file=sys.stderr)

    # Bei gleichem Namen gewinnt der groessere Ort (Stadt vor Weiler).
    beste: dict[str, tuple[int, dict]] = {}
    for el in elemente:
        tags = el.get("tags", {})
        namen = [tags[k] for k in ("name", "name:de", "name:sl", "alt_name") if tags.get(k)]
        rang = RANG.get(tags.get("place", ""), 0)
        for roh in namen:
            # Zweisprachige Schilder stehen in OSM als "Feistritz ob Bleiburg /
            # Bistrica pri Pliberku" in einem Feld. Beide Haelften sind eigene
            # Ortsnamen, unter denen jemand fragen kann.
            for teil in roh.replace("\uff0f", "/").replace("/", ";").split(";"):
                key = normalisiere(teil)
                if len(key) < 3:
                    continue
                if key not in beste or rang > beste[key][0]:
                    beste[key] = (rang, el)

    tal = [s for s in stationen if s["hoehe"] <= TALGRENZE_M]
    orte = {}
    weit = []
    for key, (_rang, el) in beste.items():
        lat, lon = el["lat"], el["lon"]
        # Ausnahme von der Talgrenze: Mallnitz liegt selbst auf 1200 m,
        # Flattnitz auf 1400, und die Station traegt den Namen des Ortes. Sie
        # ins Tal zu schicken waere schlechter als die Hoehe. Naehe allein
        # taugt nicht als Kriterium — die Kanzelhoehe steht zwei Kilometer von
        # Annenheim entfernt und tausend Meter darueber.
        naechste = min(stationen, key=lambda s: distanz_km(lat, lon, s["lat"], s["lon"]))
        heisst_so = normalisiere(naechste["name"]).split()[:1] == [key]
        if naechste["hoehe"] > TALGRENZE_M and heisst_so:
            st = naechste
        else:
            st = min(tal, key=lambda s: distanz_km(lat, lon, s["lat"], s["lon"]))
        d = distanz_km(lat, lon, st["lat"], st["lon"])
        if d > 30:
            weit.append((round(d), key, st["name"]))
        orte[key] = {"station_id": st["id"], "station": st["name"],
                     "lat": round(lat, 5), "lon": round(lon, 5)}

    # Jede Station ist auch selbst ein Ort — sonst scheitert `!wx arriach`.
    # Hier auch die Bergstationen: wer "Villacher Alpe" tippt, meint sie.
    for st in stationen:
        orte.setdefault(normalisiere(st["name"]), {
            "station_id": st["id"], "station": st["name"],
            "lat": round(st["lat"], 5), "lon": round(st["lon"], 5)})

    for d, key, name in sorted(weit, reverse=True)[:10]:
        print(f"weit weg: {key} -> {name} ({d} km)", file=sys.stderr)

    # Die gepflegten Eintraege gewinnen: dort steht bewusst eine bestimmte
    # Station (z. B. "gailtal" -> Hermagor), das darf OSM nicht ueberschreiben.
    orte.update(bestand)

    daten["orte"] = dict(sorted(orte.items()))
    schreibe(daten, ziel)
    print(f"{len(daten['orte'])} Orte geschrieben nach {ziel}", file=sys.stderr)


def schreibe(daten: dict, ziel: str) -> None:
    """Ein Ort je Zeile.

    `indent` blaeht die Datei auf sechs Zeilen je Ort, `separators` presst
    alles in eine einzige — beides macht die Datei im Diff unlesbar. Ein
    dreitausendzeiliges Verzeichnis liest sich dagegen wie eine Liste.
    """
    def zeilen(d: dict) -> str:
        inhalt = ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: "
                            f"{json.dumps(v, ensure_ascii=False)}" for k, v in d.items())
        return "{\n" + inhalt + "\n }"

    with open(ziel, "w", encoding="utf-8") as fh:
        fh.write("{\n")
        fh.write(' "orte": ' + zeilen(daten["orte"]) + ",\n")
        fh.write(' "stationen": [\n')
        fh.write(",\n".join("  " + json.dumps(s, ensure_ascii=False)
                            for s in daten["stationen"]))
        fh.write("\n ]\n}\n")


if __name__ == "__main__":
    ziel = sys.argv[1] if len(sys.argv) > 1 else "data/stations_ktn.json"
    gepflegt = sys.argv[2] if len(sys.argv) > 2 else "data/orte_gepflegt.json"
    main(ziel, gepflegt)
