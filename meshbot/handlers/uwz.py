"""!uwz — amtliche Wetterwarnungen.

Quelle: GeoSphere Austria Warn-API (`warnungen.zamg.at/wsapp/api`), Endpunkt
`getWarningsForCoords`. uwz.at selbst hat keine offene Schnittstelle; die
amtliche Warnung kommt ohnehin von der GeoSphere.

Abgefragt werden mehrere Punkte in Kärnten, weil die API gemeindeweise
antwortet — ein einzelner Punkt würde eine Warnung im Nachbartal übersehen.
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

STUFE = {1: "GELB", 2: "ORANGE", 3: "ROT"}
TYP = {
    1: "Wind", 2: "Regen", 3: "Schnee", 4: "Glatteis", 5: "Gewitter",
    6: "Hitze", 7: "Kaelte", 10: "Hitze",
}


async def fetch(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """Warnungen aller Abfragepunkte einsammeln, doppelte zusammenfassen."""
    treffer: dict[int, dict[str, Any]] = {}
    for name, lat, lon in PUNKTE:
        try:
            resp = await client.get(url, params={"lat": lat, "lon": lon, "lang": "de"})
            resp.raise_for_status()
            warnungen = resp.json().get("properties", {}).get("warnings", [])
        except Exception:
            continue
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
    return list(treffer.values())


def render(warnungen: list[dict[str, Any]], stale: bool = False) -> str:
    if not warnungen:
        return "UWZ KTN: keine Warnungen aktiv"

    def rang(w: dict[str, Any]) -> int:
        return -(w.get("stufe") or 0)

    marker = "~" if stale else ""
    teile = []
    for w in sorted(warnungen, key=rang):
        stufe = STUFE.get(w.get("stufe") or 0, "WARN")
        typ = TYP.get(w.get("typ") or 0, "Warnung")
        gebiete = w["gebiete"][0] if len(w["gebiete"]) < 3 else "KTN weit"
        ende = (w.get("ende") or "").split(" ")[-1][:5]
        teil = f"{stufe} {typ} ({gebiete}"
        teil += f" bis {ende})" if ende else ")"
        teile.append(teil)
    # Zwei Warnungen passen in eine Nachricht, drei nicht mehr zuverlaessig.
    text = f"UWZ KTN: {marker}" + ", ".join(teile[:2])
    if len(teile) > 2:
        text += f" +{len(teile) - 2} weitere"
    return text
