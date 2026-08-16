"""!wo — Zustand eines Knotens aus der Karten-API.

Beantwortet die Frage, die man sonst nur am Rechner beantworten kann: Lebt mein
Repeater noch? Gerade fuer Betreiber, die am Berg stehen und nicht wissen, ob
sich die Auffahrt lohnt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import get_close_matches
from typing import Any

import httpx


async def fetch(client: httpx.AsyncClient, basis_url: str) -> list[dict[str, Any]]:
    resp = await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})
    resp.raise_for_status()
    return resp.json()["nodes"]


def suche(nodes: list[dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Teilstring zuerst, dann Aehnlichkeit — `dobra` findet AT-VI-Dobratsch."""
    b = begriff.strip().lower()
    if not b:
        return None
    treffer = [n for n in nodes if b in n["name"].lower()]
    if treffer:
        return max(treffer, key=lambda n: n.get("relay_count_24h") or 0)
    namen = {n["name"].lower(): n for n in nodes}
    aehnlich = get_close_matches(b, list(namen), n=1, cutoff=0.5)
    return namen[aehnlich[0]] if aehnlich else None


def _alter(zeit: str, jetzt: datetime) -> str:
    try:
        ts = datetime.fromisoformat(zeit.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    minuten = int((jetzt - ts).total_seconds() / 60)
    if minuten < 90:
        return f"{max(minuten, 0)}min"
    if minuten < 2880:
        return f"{minuten // 60}h"
    return f"{minuten // 1440}d"


def render(begriff: str, node: dict[str, Any] | None, jetzt: datetime) -> str:
    if node is None:
        return f"Node {begriff[:16]}: nicht gefunden"
    teile = [node["name"]]
    if node.get("lat") and node.get("lon"):
        teile.append(f"{node['lat']:.3f},{node['lon']:.3f}")
    teile.append(f"{node.get('relay_count_24h', 0)}/24h")
    teile.append(f"zuletzt {_alter(str(node.get('last_seen', '')), jetzt)}")
    return ": ".join([teile[0], ", ".join(teile[1:])])
