"""!netz — the state of the mesh, from the map API.

Answers the question otherwise only answerable by whoever has the map open in a
browser: how many repeaters are active, how much traffic there is, who carries
the most.

Counting runs over **two time windows**. The hour says whether the network is
running right now, the day says how much it carries. On the daily figure alone
the answer stood still for days: anything that relayed once in 24 hours counts
as active, and that is practically always everything — an outage only became
visible after a full day.
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
    """One fetch is enough.

    The stats API used to supply `packetsLast24h`, which never appeared in the
    answer — one HTTP call per cache miss for a value nobody ever saw.
    """
    nodes = (await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})).json()["nodes"]
    ktn = [n for n in nodes
           if n.get("role") == "repeater" and ist_kaernten(n.get("name", ""), n.get("lat"), n.get("lon"))]
    # The busiest one is determined over the hour: averaged over 24 hours the
    # ranking stands still for days, and then the figure contributes nothing.
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
    """Keep it short -- the answer sat at 99 of 100 permitted characters.

    One more digit in the daily traffic would have been enough for the bot to
    truncate the busiest repeater off the end. Hence "Pakete" instead of
    "Weiterl.", "top" instead of "staerkster" and "24h" instead of "24h nur":
    close to thirty characters of headroom without dropping a single figure.
    """
    marker = "~" if stale else ""
    teile = [f"Netz KTN: {marker}{w['aktiv_1h']}/{w['gesamt']} aktiv"]
    # The daily figure is only mentioned when it says something: that a repeater
    # was silent for a whole day. While everything reports, it would be filler.
    if w.get("aktiv_24h") is not None and w["aktiv_24h"] < w["gesamt"]:
        teile[0] += f" (24h {w['aktiv_24h']})"
    if w.get("weiter_1h") or w.get("weiter_24h"):
        teile.append(f"Pakete {w.get('weiter_1h', 0)}/1h {w.get('weiter_24h', 0)}/24h")
    if w.get("top"):
        name, zahl = w["top"]
        teile.append(f"top {name.replace('AT-', '')} ({zahl})")
    return ", ".join(teile)
