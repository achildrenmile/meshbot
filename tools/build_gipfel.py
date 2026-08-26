#!/usr/bin/env python3
"""Gipfelverzeichnis aus der SOTA-Gesamtliste bauen.

    ./tools/build_gipfel.py                    baut data/sota_summits.json neu
    ./tools/build_gipfel.py --assoc OE I S5    nur diese Verbaende

Warum aus der CSV und nicht ueber die API: Die Gesamtliste kommt in **einem**
Abruf, die API braucht einen je Region. Bei zehn Verbaenden waeren das ueber
hundert Abrufe fuer Daten, die sich einmal am Tag aendern.

Die Datei versorgt zwei Befehle:

  !sota <lat lon>   naechster Gipfel zu einer Position
  !wx <gipfel>      Gipfelwetter, sobald ein Name nicht im Ortsverzeichnis steht

Deshalb der grosse Umgriff: Ein Gipfel, der nicht in dieser Datei steht, ist
fuer den Bot nicht existent -- und Berge enden nicht an der Staatsgrenze.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

URL = "https://storage.sota.org.uk/summitslist.csv"

# Oesterreich und alle Nachbarn, dazu Kroatien. Das ist der Umkreis, in dem
# jemand aus Kaernten steht, wenn er nach einem Gipfel fragt.
VERBAENDE = ["OE", "I", "S5", "DL", "HB", "9A", "OK", "OM", "HA", "SP"]

LAENDER = {
    "OE": "Oesterreich", "I": "Italien", "S5": "Slowenien", "DL": "Deutschland",
    "HB": "Schweiz", "9A": "Kroatien", "OK": "Tschechien", "OM": "Slowakei",
    "HA": "Ungarn", "SP": "Polen",
}


def hole(url: str) -> str:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read().decode("utf-8", errors="replace")


def gueltig(zeile: dict[str, str], heute: date) -> bool:
    """Aufgelassene Gipfel weglassen -- die SOTA-Liste fuehrt sie weiter mit."""
    roh = (zeile.get("ValidTo") or "").strip()
    if not roh:
        return True
    try:
        return datetime.strptime(roh, "%d/%m/%Y").date() >= heute
    except ValueError:
        return True


def baue(text: str, verbaende: list[str]) -> dict:
    fh = io.StringIO(text)
    fh.readline()                        # Kopfzeile mit dem Erstellungsdatum
    heute = date.today()
    gewollt = set(verbaende)
    gipfel = []
    for z in csv.DictReader(fh):
        code = z["SummitCode"]
        assoc = code.split("/")[0]
        if assoc not in gewollt or not gueltig(z, heute):
            continue
        try:
            gipfel.append({
                "ref": code,
                "name": z["SummitName"].strip(),
                "alt": int(z["AltM"]),
                "pts": int(z["Points"]),
                "akt": int(z["ActivationCount"] or 0),
                "lat": round(float(z["Latitude"]), 5),
                "lon": round(float(z["Longitude"]), 5),
            })
        except (ValueError, KeyError):
            continue          # unvollstaendige Zeile ueberspringen, nicht raten
    gipfel.sort(key=lambda g: g["ref"])
    return {
        "quelle": "SOTA Summits List (summitslist.csv)",
        "stand": heute.isoformat(),
        "regionen": sorted({g["ref"].split("-")[0] for g in gipfel}),
        "verbaende": {a: LAENDER.get(a, a) for a in sorted(gewollt)},
        "gipfel": gipfel,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--assoc", nargs="+", default=VERBAENDE)
    p.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "sota_summits.json")
    a = p.parse_args()

    print(f"Lade {URL} …", file=sys.stderr)
    daten = baue(hole(URL), a.assoc)
    g = daten["gipfel"]
    if len(g) < 1000:
        print(f"Nur {len(g)} Gipfel -- das sieht nach einem kaputten Abruf aus, "
              f"nichts geschrieben.", file=sys.stderr)
        return 1

    a.out.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(g)} Gipfel, {a.out.stat().st_size / 1024:.0f} KB -> {a.out}", file=sys.stderr)
    from collections import Counter
    for assoc, n in Counter(x["ref"].split("/")[0] for x in g).most_common():
        print(f"  {assoc:4} {LAENDER.get(assoc, ''):14} {n:6}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
