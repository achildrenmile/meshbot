"""!wo — the state of a node, from the map API.

Answers the question that otherwise needs a computer: is my repeater still
alive? Especially for operators standing on a mountain, not knowing whether the
drive up is worth it.

Second case: the **path hash**. The app shows an unknown repeater as
`<Unknown Repeater d733>` -- the hash is the start of the public key. Resolving
it otherwise needs a computer with internet. That is exactly what one does not
have out in the field, and out in the field is where the question comes up.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import get_close_matches
from typing import Any

import httpx

# A path hash is the leading hex digits of the public key: two for 1-byte
# hashes, four for the usual two bytes, six for three. Nobody types more than
# eight.
HASHFORM = re.compile(r"^[0-9a-f]{2,8}$")


async def fetch(client: httpx.AsyncClient, basis_url: str) -> list[dict[str, Any]]:
    resp = await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})
    resp.raise_for_status()
    return resp.json()["nodes"]


def suche_teilstring(nodes: list[dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Genuine name fragments only, no similarity."""
    b = begriff.strip().lower()
    if not b:
        return None
    treffer = [n for n in nodes if b in n["name"].lower()]
    return max(treffer, key=lambda n: n.get("relay_count_24h") or 0) if treffer else None


def suche(nodes: list[dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Substring first, then similarity — `dobra` finds AT-VI-Dobratsch."""
    b = begriff.strip().lower()
    if not b:
        return None
    genau = suche_teilstring(nodes, b)
    if genau is not None:
        return genau
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


def ist_hashform(begriff: str) -> bool:
    """Does the term look like a path hash? Says nothing about whether it hits one."""
    return bool(HASHFORM.match(begriff.strip().lower()))


def suche_hash(nodes: list[dict[str, Any]], begriff: str) -> list[dict[str, Any]]:
    """All nodes whose public key starts this way — busiest first.

    Multiple hits are not an error but the reason for 2 bytes: with one byte,
    two nodes in a growing network eventually collide on the same hash. The
    answer then has to name both.
    """
    h = begriff.strip().lower()
    treffer = [n for n in nodes if str(n.get("public_key") or "").lower().startswith(h)]
    treffer.sort(key=lambda n: -(n.get("relay_count_24h") or 0))
    return treffer


def render_hash(begriff: str, treffer: list[dict[str, Any]], jetzt: datetime) -> str:
    h = begriff.strip().lower()
    if not treffer:
        return f"Pfad {h}: kein Knoten mit diesem Hash"
    if len(treffer) == 1:
        return f"Pfad {h} = {render(h, treffer[0], jetzt)}"
    # On a collision, whoever carries the traffic counts: the top one is the
    # likely candidate. Naming three is enough, the total precedes them.
    namen = ", ".join(f"{n['name']} ({n.get('relay_count_24h', 0)}/24h)" for n in treffer[:3])
    return f"Pfad {h}: {len(treffer)} Treffer - {namen}"


def antwort(nodes: list[dict[str, Any]], begriff: str, jetzt: datetime) -> str:
    """Hash first, then name.

    A term can be both: `dead` is valid hex and could be a node name. So it is
    not the shape alone that decides but the hit — if the hash lookup comes up
    empty, the name lookup still runs.

    For hex-shaped terms, though, **without the similarity search**: typing
    `beef` means a hash. A fuzzy hit on a node called "Bergfee" would be an
    answer that looks certain and is wrong — here "never heard of it" is the
    better information.
    """
    if ist_hashform(begriff):
        treffer = suche_hash(nodes, begriff)
        if treffer:
            return render_hash(begriff, treffer, jetzt)
        node = suche_teilstring(nodes, begriff)
        if node is None:
            return render_hash(begriff, [], jetzt)
        return render(begriff, node, jetzt)
    return render(begriff, suche(nodes, begriff), jetzt)
