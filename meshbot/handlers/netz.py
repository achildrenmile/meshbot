"""!netz — Zustand des Mesh aus der Karten-API.

Beantwortet die Frage, die sonst nur beantwortet, wer die Karte im Browser
offen hat: Wie viele Repeater sind aktiv, wie viel laeuft, wer traegt am meisten.
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


async def fetch(client: httpx.AsyncClient, basis_url: str) -> dict[str, Any]:
    stats = (await client.get(f"{basis_url}/api/stats")).json()
    nodes = (await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})).json()["nodes"]
    ktn = [n for n in nodes
           if n.get("role") == "repeater" and ist_kaernten(n.get("name", ""), n.get("lat"), n.get("lon"))]
    aktiv = [n for n in ktn if (n.get("relay_count_24h") or 0) > 0]
    top = sorted(ktn, key=lambda n: -(n.get("relay_count_24h") or 0))[:1]
    return {
        "repeater": len(aktiv),
        "gesamt": len(ktn),
        "pakete24": stats.get("packetsLast24h"),
        "weiterleitungen": sum(n.get("relay_count_24h") or 0 for n in ktn),
        "top": (top[0]["name"], top[0].get("relay_count_24h")) if top else None,
    }


def render(w: dict[str, Any], stale: bool = False) -> str:
    marker = "~" if stale else ""
    teile = [f"Netz KTN: {marker}{w['repeater']}/{w['gesamt']} Repeater aktiv"]
    if w.get("weiterleitungen"):
        teile.append(f"{w['weiterleitungen']} Weiterleitungen/24h")
    if w.get("top"):
        name, zahl = w["top"]
        teile.append(f"stärkster {name.replace('AT-','')} ({zahl})")
    return ", ".join(teile)
