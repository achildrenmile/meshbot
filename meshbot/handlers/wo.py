"""!wo — Zustand eines Knotens aus der Karten-API.

Beantwortet die Frage, die man sonst nur am Rechner beantworten kann: Lebt mein
Repeater noch? Gerade fuer Betreiber, die am Berg stehen und nicht wissen, ob
sich die Auffahrt lohnt.

Zweiter Fall: der **Pfad-Hash**. Die App zeigt einen unbekannten Repeater als
`<Unknown Repeater d733>` -- der Hash ist der Anfang des Public Key. Wer ihn
aufloesen will, braucht sonst einen Rechner mit Internet. Genau den hat man
unterwegs nicht, und unterwegs stellt sich die Frage.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import get_close_matches
from typing import Any

import httpx

# Ein Pfad-Hash sind die ersten Hexziffern des Public Key: zwei bei 1-Byte-
# Hashes, vier bei den ueblichen zwei Byte, sechs bei drei. Mehr als acht
# tippt niemand ab.
HASHFORM = re.compile(r"^[0-9a-f]{2,8}$")


async def fetch(client: httpx.AsyncClient, basis_url: str) -> list[dict[str, Any]]:
    resp = await client.get(f"{basis_url}/api/nodes", params={"limit": 2000})
    resp.raise_for_status()
    return resp.json()["nodes"]


def suche_teilstring(nodes: list[dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Nur echte Namensbestandteile, keine Aehnlichkeit."""
    b = begriff.strip().lower()
    if not b:
        return None
    treffer = [n for n in nodes if b in n["name"].lower()]
    return max(treffer, key=lambda n: n.get("relay_count_24h") or 0) if treffer else None


def suche(nodes: list[dict[str, Any]], begriff: str) -> dict[str, Any] | None:
    """Teilstring zuerst, dann Aehnlichkeit — `dobra` findet AT-VI-Dobratsch."""
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
    """Sieht der Begriff wie ein Pfad-Hash aus? Sagt nichts darueber, ob er einen trifft."""
    return bool(HASHFORM.match(begriff.strip().lower()))


def suche_hash(nodes: list[dict[str, Any]], begriff: str) -> list[dict[str, Any]]:
    """Alle Knoten, deren Public Key so beginnt — meistverkehrter zuerst.

    Mehrere Treffer sind kein Fehler, sondern der Grund fuer 2 Byte: Bei einem
    Byte kollidieren in einem wachsenden Netz irgendwann zwei Knoten auf
    demselben Hash. Dann muss die Antwort beide nennen.
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
    # Bei einer Kollision zaehlt, wer den Verkehr traegt: Der oberste ist der
    # wahrscheinlich gemeinte. Drei genannte reichen, die Gesamtzahl steht davor.
    namen = ", ".join(f"{n['name']} ({n.get('relay_count_24h', 0)}/24h)" for n in treffer[:3])
    return f"Pfad {h}: {len(treffer)} Treffer - {namen}"


def antwort(nodes: list[dict[str, Any]], begriff: str, jetzt: datetime) -> str:
    """Hash zuerst, dann Name.

    Ein Begriff kann beides sein: `dead` ist gueltiges Hex und koennte ein
    Knotenname sein. Deshalb entscheidet nicht die Form allein, sondern der
    Treffer — greift die Hashsuche ins Leere, laeuft die Namenssuche noch.

    Fuer hexfoermige Begriffe aber **ohne Aehnlichkeitssuche**: Wer `beef`
    tippt, meint einen Hash. Ein Fuzzy-Treffer auf einen Knoten namens
    "Bergfee" waere eine Antwort, die sicher aussieht und falsch ist — hier
    ist "kenne ich nicht" die bessere Auskunft.
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
