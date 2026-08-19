"""!uwz — amtliche Wetterwarnungen.

Quelle: GeoSphere Austria Warn-API (`warnungen.zamg.at/wsapp/api`), Endpunkt
`getWarningsForCoords`. uwz.at selbst hat keine offene Schnittstelle; die
amtliche Warnung kommt ohnehin von der GeoSphere.

Abgefragt werden mehrere Punkte in Kärnten, weil die API gemeindeweise
antwortet — ein einzelner Punkt würde eine Warnung im Nachbartal übersehen.

Wer einen Ort oder eine Position mitschickt, bekommt stattdessen genau seine
Gemeinde:

    !uwz                  -> Übersicht über vier Landesteile
    !uwz 46.60 13.67      -> nur die Gemeinde an dieser Position
    !uwz waidegg          -> dasselbe über das Ortsverzeichnis von `!wx`
"""

from __future__ import annotations

from typing import Any

import httpx

# Vier Abfragepunkte decken die Landesteile grob ab: Zentralraum, Oberkärnten,
# Gailtal, Lavanttal. Mehr Punkte kosten Zeit, nicht Airtime.
PUNKTE = [
    ("Zentralraum", 46.6247, 14.3053),
    ("Oberkaernten", 46.7956, 13.4967),
    ("Gailtal", 46.6255, 13.3690),
    ("Lavanttal", 46.8406, 14.8408),
]

class QuelleNichtErreichbar(RuntimeError):
    """Kein Abfragepunkt hat geantwortet — Schweigen ist keine Entwarnung."""


STUFE = {1: "GELB", 2: "ORANGE", 3: "ROT"}
TYP = {
    1: "Wind", 2: "Regen", 3: "Schnee", 4: "Glatteis", 5: "Gewitter",
    6: "Hitze", 7: "Kaelte", 10: "Hitze",
}


async def fetch(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """Warnungen aller Abfragepunkte einsammeln, doppelte zusammenfassen.

    Wirft, wenn **kein einziger** Punkt geantwortet hat. Ohne diese
    Unterscheidung sind "nichts gefunden" und "nichts erreicht" dasselbe leere
    Ergebnis — und der Bot funkt bei ausgefallener Warn-API Entwarnung. Das ist
    die gefaehrlichste Falschaussage, die ein Warndienst machen kann.

    Ein einzelner erreichter Punkt genuegt dagegen: Die vier Punkte decken
    verschiedene Landesteile ab, ein Ausfall davon macht die Antwort
    unvollstaendig, nicht falsch.
    """
    treffer: dict[int, dict[str, Any]] = {}
    erreicht = 0
    for name, lat, lon in PUNKTE:
        try:
            resp = await client.get(url, params={"lat": lat, "lon": lon, "lang": "de"})
            resp.raise_for_status()
            warnungen = resp.json().get("properties", {}).get("warnings", [])
        except Exception:
            continue
        erreicht += 1
        for w in warnungen:
            p = w.get("properties", {})
            wid = p.get("warnid")
            if wid is None:
                continue
            eintrag = treffer.setdefault(wid, {
                "stufe": p.get("warnstufeid"),
                "typ": p.get("warntypid"),
                "ende": p.get("end"),
                "gebiete": [],
            })
            if name not in eintrag["gebiete"]:
                eintrag["gebiete"].append(name)
    if erreicht == 0:
        raise QuelleNichtErreichbar(f"kein Abfragepunkt erreichbar ({len(PUNKTE)} versucht)")
    return list(treffer.values())


def parse_warnungen(daten: dict[str, Any]) -> list[dict[str, Any]]:
    """Warnungsliste einer API-Antwort in die interne Form bringen."""
    eintraege = []
    for w in daten.get("properties", {}).get("warnings", []):
        p = w.get("properties", {})
        eintraege.append({
            "stufe": p.get("warnstufeid"),
            "typ": p.get("warntypid"),
            "ende": p.get("end"),
            "gebiete": [],          # bei einer Position steht das Gebiet vorne
        })
    return eintraege


async def fetch_punkt(client: httpx.AsyncClient, url: str, lat: float, lon: float
                      ) -> tuple[str, list[dict[str, Any]]]:
    """Warnungen fuer genau eine Position — Gemeindename und Warnungen.

    Anders als `fetch` wird hier jeder Fehler durchgereicht: Bei einem einzigen
    Abfragepunkt gibt es keine Teilabdeckung, die man retten koennte. Ohne
    Antwort gibt es keine Aussage, und keine Aussage ist keine Entwarnung.
    """
    resp = await client.get(url, params={"lat": lat, "lon": lon, "lang": "de"})
    resp.raise_for_status()
    daten = resp.json()
    ort = ((daten.get("properties", {}).get("location") or {}).get("properties") or {}).get("name")
    return ort or f"{lat:.3f},{lon:.3f}", parse_warnungen(daten)


def render_unbekannt(arg: str) -> str:
    """Ort steht nicht im Verzeichnis.

    Anders als `!wx` ohne Spott: Wer nach einer Warnung fragt, soll einen Weg
    bekommen statt eine Pointe. Die Position funktioniert immer, auch fuer
    Almen und Gipfel, die in keinem Ortsverzeichnis stehen.
    """
    ort = " ".join(arg.split())[:20] or "?"
    return f"UWZ: {ort} unbekannt. Position geht immer: !uwz 46.61 13.85"


def render(warnungen: list[dict[str, Any]], stale: bool = False, ort: str = "KTN") -> str:
    marker = "~" if stale else ""
    if not warnungen:
        # Auch die Entwarnung braucht das Alterszeichen. Sonst sieht ein Stand
        # von vor zwei Stunden aus wie eine frische Entwarnung -- und genau da
        # ist der Unterschied am wichtigsten.
        return f"UWZ {ort}: {marker}keine Warnungen aktiv"

    def rang(w: dict[str, Any]) -> int:
        return -(w.get("stufe") or 0)

    teile = []
    for w in sorted(warnungen, key=rang):
        stufe = STUFE.get(w.get("stufe") or 0, "WARN")
        typ = TYP.get(w.get("typ") or 0, "Warnung")
        # Bei einer Ortsabfrage steht das Gebiet schon vorne im Kopf — in der
        # Klammer bleibt dann nur die Uhrzeit, statt den Namen zu wiederholen.
        klammer = []
        if w["gebiete"]:
            klammer.append(w["gebiete"][0] if len(w["gebiete"]) < 3 else "KTN weit")
        ende = (w.get("ende") or "").split(" ")[-1][:5]
        if ende:
            klammer.append(f"bis {ende}")
        teil = f"{stufe} {typ}"
        if klammer:
            teil += " (" + " ".join(klammer) + ")"
        teile.append(teil)
    # Zwei Warnungen passen in eine Nachricht, drei nicht mehr zuverlaessig.
    text = f"UWZ {ort}: {marker}" + ", ".join(teile[:2])
    if len(teile) > 2:
        text += f" +{len(teile) - 2} weitere"
    return text
