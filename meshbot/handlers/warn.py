"""!warn — official weather warnings.

Source: GeoSphere Austria warning API (`warnungen.zamg.at/wsapp/api`), endpoint
`getWarningsForCoords`, CC BY 4.0.

The command was called `!uwz` until August 2026 and the answers started with
`UWZ`. That was mislabelled: the Unwetterzentrale (uwz.at) is a private service
run by UBIMET and has nothing to do with this data -- in Austria the official
warnings come from GeoSphere. Somebody else's brand name over somebody else's
data is not a triviality, so both are now called `WARN`. `!uwz` survives as
input so that nobody types into the void.

Several points across Carinthia are queried, because the API answers per
municipality — a single point would miss a warning in the next valley.

Sending a place or a position along returns exactly that municipality instead:

    !warn                 -> overview across four parts of the state
    !warn 46.60 13.67     -> only the municipality at this position
    !warn waidegg         -> the same via the place directory of `!wx`
"""

from __future__ import annotations

from typing import Any

import httpx

# Four query points cover the parts of the state roughly: central area, Upper
# Carinthia, Gailtal, Lavanttal. More points cost time, not airtime.
PUNKTE = [
    ("Zentralraum", 46.6247, 14.3053),
    ("Oberkaernten", 46.7956, 13.4967),
    ("Gailtal", 46.6255, 13.3690),
    ("Lavanttal", 46.8406, 14.8408),
]

class QuelleNichtErreichbar(RuntimeError):
    """No query point answered — silence is not an all-clear."""


STUFE = {1: "GELB", 2: "ORANGE", 3: "ROT"}
TYP = {
    1: "Wind", 2: "Regen", 3: "Schnee", 4: "Glatteis", 5: "Gewitter",
    6: "Hitze", 7: "Kaelte", 10: "Hitze",
}


async def fetch(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """Collect warnings from all query points, merging duplicates.

    Raises when **not a single** point answered. Without that distinction
    "found nothing" and "reached nothing" are the same empty result — and with
    the warning API down the bot would transmit an all-clear. That is the most
    dangerous false statement a warning service can make.

    A single point reached is enough, though: the four points cover different
    parts of the state, so one failing makes the answer incomplete, not wrong.
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
    """Convert the warning list of an API response into the internal form."""
    eintraege = []
    for w in daten.get("properties", {}).get("warnings", []):
        p = w.get("properties", {})
        eintraege.append({
            "stufe": p.get("warnstufeid"),
            "typ": p.get("warntypid"),
            "ende": p.get("end"),
            "gebiete": [],          # with a position the area is already in front
        })
    return eintraege


async def fetch_punkt(client: httpx.AsyncClient, url: str, lat: float, lon: float
                      ) -> tuple[str, list[dict[str, Any]]]:
    """Warnings for exactly one position — municipality name and warnings.

    Unlike `fetch`, every error is passed through here: with a single query
    point there is no partial coverage worth salvaging. No answer means no
    statement, and no statement is not an all-clear.
    """
    resp = await client.get(url, params={"lat": lat, "lon": lon, "lang": "de"})
    resp.raise_for_status()
    daten = resp.json()
    ort = ((daten.get("properties", {}).get("location") or {}).get("properties") or {}).get("name")
    return ort or f"{lat:.3f},{lon:.3f}", parse_warnungen(daten)


def render_unbekannt(arg: str) -> str:
    """The place is not in the directory.

    Unlike `!wx`, without mockery: whoever asks about a warning should get a way
    forward, not a punchline. A position always works, including for alpine
    pastures and summits that appear in no place directory.
    """
    ort = " ".join(arg.split())[:20] or "?"
    return f"WARN: {ort} unbekannt. Position geht immer: !warn 46.61 13.85"


def render(warnungen: list[dict[str, Any]], stale: bool = False, ort: str = "KTN") -> str:
    marker = "~" if stale else ""
    if not warnungen:
        # The all-clear needs the staleness marker too. Otherwise a reading from
        # two hours ago looks like a fresh all-clear -- and that is exactly where
        # the difference matters most.
        return f"WARN {ort}: {marker}keine Warnungen aktiv"

    def rang(w: dict[str, Any]) -> int:
        return -(w.get("stufe") or 0)

    teile = []
    for w in sorted(warnungen, key=rang):
        stufe = STUFE.get(w.get("stufe") or 0, "WARN")
        typ = TYP.get(w.get("typ") or 0, "Warnung")
        # On a place query the area is already in the header — the parentheses
        # then carry only the time, instead of repeating the name.
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
    # Two warnings fit into one message, three no longer reliably.
    text = f"WARN {ort}: {marker}" + ", ".join(teile[:2])
    if len(teile) > 2:
        text += f" +{len(teile) - 2} weitere"
    return text
