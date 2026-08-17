"""!netz — Zustand des Mesh aus der Karten-API.

Beantwortet die Frage, die sonst nur beantwortet, wer die Karte im Browser
offen hat: Wie viele Repeater sind aktiv, wie viel laeuft, wer traegt am meisten.

Gezaehlt wird ueber **zwei Zeitfenster**. Die Stunde sagt, ob das Netz gerade
laeuft, der Tag sagt, wie viel es traegt. Am Tageswert allein stand die Antwort
tagelang still: als aktiv gilt, wer in 24 Stunden einmal weitergeleitet hat, und
das trifft praktisch immer auf alle zu — ein Ausfall wurde erst nach einem vollen
Tag sichtbar.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

KTN = ("K", "KL", "VI", "VL", "FE", "HE", "SV", "SP", "VK", "WO")


def ist_kaernten(name: str, lat: float | None, lon: float | None) -> bool:
    m = re.match(r"AT-([A-Z]{1,2})-", name)
    if not m or m.group(1) not in KTN:
        return False
    return lat is not None and lon is not None and 46.3 < lat < 47.3 and 12.4 < lon < 15.3


def _zahl(n: dict[str, Any], feld: str) -> int:
    return n.get(feld) or 0


async def fetch(client: httpx.AsyncClient, basis_url: str) -> dict[str, Any]:
    """Ein Abruf reicht.

    Die Stats-API lieferte frueher `packetsLast24h`, das in der Antwort nie
    vorkam — ein HTTP-Aufruf je Cache-Miss fuer einen Wert, den niemand sah.
    """
    nodes = (await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})).json()["nodes"]
    ktn = [n for n in nodes
           if n.get("role") == "repeater" and ist_kaernten(n.get("name", ""), n.get("lat"), n.get("lon"))]
    # Der staerkste wird nach der Stunde bestimmt: ueber 24 Stunden gemittelt
    # steht die Reihenfolge tagelang, und dann traegt die Angabe nichts bei.
    top = sorted(ktn, key=lambda n: -_zahl(n, "relay_count_1h"))[:1]
    return {
        "aktiv_1h": sum(1 for n in ktn if _zahl(n, "relay_count_1h") > 0),
        "aktiv_24h": sum(1 for n in ktn if _zahl(n, "relay_count_24h") > 0),
        "gesamt": len(ktn),
        "weiter_1h": sum(_zahl(n, "relay_count_1h") for n in ktn),
        "weiter_24h": sum(_zahl(n, "relay_count_24h") for n in ktn),
        "top": (top[0]["name"], _zahl(top[0], "relay_count_1h")) if top else None,
    }


def render(w: dict[str, Any], stale: bool = False) -> str:
    marker = "~" if stale else ""
    teile = [f"Netz KTN: {marker}{w['aktiv_1h']}/{w['gesamt']} aktiv"]
    # Der Tageswert kommt nur zur Sprache, wenn er etwas sagt: dass ein Repeater
    # einen ganzen Tag lang stumm war. Solange alle liefern, waere er Fuellsel.
    if w.get("aktiv_24h") is not None and w["aktiv_24h"] < w["gesamt"]:
        teile[0] += f" (24h nur {w['aktiv_24h']})"
    if w.get("weiter_1h") or w.get("weiter_24h"):
        teile.append(f"Weiterl. {w.get('weiter_1h', 0)}/1h {w.get('weiter_24h', 0)}/24h")
    if w.get("top"):
        name, zahl = w["top"]
        teile.append(f"stärkster {name.replace('AT-', '')} ({zahl})")
    return ", ".join(teile)
