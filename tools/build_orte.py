"""Build the MeshBot place directory from OSM place nodes (Carinthia).

Rewrites data/stations_ktn.json — the station list and hand-maintained entries
survive, the place directory is replaced. Source: OpenStreetMap, ODbL.

    python3 tools/build_orte.py [target] [curated-places]

Two queries, both with a bounding box — searching by the `ISO3166-2` tag alone
runs into a timeout at Overpass.
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

# Place names only get valley stations. Arnoldstein sits at 580 m, the Villacher
# Alpe at 2117 m and is still the nearest station — without this limit
# "!wx arnoldstein" answers ten degrees too cold. Anyone wanting the summit
# values asks with a position; the limit does not apply there.
TALGRENZE_M = 1100.0


def frage(query: str, versuche: int = 4) -> dict:
    """Overpass is a free service and fends off load with a 504.

    That is not an error but a request for patience — so wait and retry instead
    of aborting the run.
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
    """All border segments as (lat1, lon1, lat2, lon2).

    The rings need not be sorted: the ray-casting test only counts crossings,
    and holes (role=inner) cancel themselves out in the process.
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


# "(ehem.) Hader", "Bach (Zweinitz)": OSM appends qualifiers in parentheses.
# The qualifier is not a name anyone asks under.
KLAMMER = re.compile(r"\s*\([^)]*\)")


def normalisiere(name: str) -> str:
    s = KLAMMER.sub("", " ".join(name.split())).lower()
    for alt, neu in (("ö", "oe"), ("ä", "ae"), ("ü", "ue"), ("ß", "ss"),
                     ("š", "s"), ("č", "c"), ("ž", "z"),
                     (".", " "), ("-", " "), ("'", ""), ("`", "")):
        s = s.replace(alt, neu)
    # "Bad Sankt Leonhard" and "Bad St. Leonhard" are the same place.
    return " ".join("st" if w == "sankt" else w for w in s.split())


def main(ziel: str, gepflegt_datei: str) -> None:
    """Regenerates `ziel` — input and output file are the same one.

    The station list is carried over, the place directory replaced wholesale.
    Were the old contents used as a starting point instead, every entry ever
    generated would survive every later run — including a wrong one.
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

    # On equal names the larger place wins (town before hamlet).
    beste: dict[str, tuple[int, dict]] = {}
    for el in elemente:
        tags = el.get("tags", {})
        namen = [tags[k] for k in ("name", "name:de", "name:sl", "alt_name") if tags.get(k)]
        rang = RANG.get(tags.get("place", ""), 0)
        for roh in namen:
            # Bilingual signs appear in OSM as "Feistritz ob Bleiburg /
            # Bistrica pri Pliberku" in one field. Both halves are place names
            # in their own right that somebody may ask under.
            for teil in roh.replace("\uff0f", "/").replace("/", ";").split(";"):
                key = normalisiere(teil)
                if len(key) < 3:
                    continue
                if key not in beste or rang > beste[key][0]:
                    # Carry the original spelling along: the key is umlaut-free
                    # so that "noetsch" and "nötsch" find the same entry -- but
                    # what goes on the air is "Nötsch im Gailtal", the way the
                    # place is actually called.
                    beste[key] = (rang, el, teil.strip())

    tal = [s for s in stationen if s["hoehe"] <= TALGRENZE_M]
    orte = {}
    weit = []
    for key, (_rang, el, anzeige) in beste.items():
        lat, lon = el["lat"], el["lon"]
        # Exception to the valley limit: Mallnitz itself sits at 1200 m,
        # Flattnitz at 1400, and the station carries the name of the place.
        # Sending it down into the valley would be worse than the altitude.
        # Proximity alone is no criterion — the Kanzelhöhe stands two kilometres
        # from Annenheim and a thousand metres above it.
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
        # Only stored when it differs from the key -- otherwise a second,
        # identical name inflates the file by 3199 entries.
        if anzeige and anzeige.lower() != key:
            orte[key]["anzeige"] = anzeige

    # Every station is a place in its own right — otherwise `!wx arriach` fails.
    # Summit stations included here: typing "Villacher Alpe" means that station.
    for st in stationen:
        orte.setdefault(normalisiere(st["name"]), {
            "station_id": st["id"], "station": st["name"],
            "lat": round(st["lat"], 5), "lon": round(st["lon"], 5)})

    for d, key, name in sorted(weit, reverse=True)[:10]:
        print(f"weit weg: {key} -> {name} ({d} km)", file=sys.stderr)

    # The curated entries win: they deliberately name a particular station
    # (e.g. "gailtal" -> Hermagor), and OSM must not overwrite that.
    orte.update(bestand)

    daten["orte"] = dict(sorted(orte.items()))
    schreibe(daten, ziel)
    print(f"{len(daten['orte'])} Orte geschrieben nach {ziel}", file=sys.stderr)


def schreibe(daten: dict, ziel: str) -> None:
    """One place per line.

    `indent` inflates the file to six lines per place, `separators` squeezes
    everything onto a single one — both make the file unreadable in a diff. A
    three-thousand-line directory, by contrast, reads like a list.
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
