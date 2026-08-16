"""!lawine — Lawinenwarnstufe aus dem EAWS-Bulletin.

Quelle: static.avalanche.report, das gemeinsame Bulletin der europaeischen
Warndienste im EAWS-Format. Kaernten ist die Region `AT-02`.

Ausserhalb der Saison gibt es kein Bulletin — das ist kein Fehler und wird auch
so beantwortet.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

STUFE = {"low": "1 gering", "moderate": "2 maessig", "considerable": "3 erheblich",
         "high": "4 gross", "very_high": "5 sehr gross", "no_rating": "keine Angabe"}
GRENZE = {"treeline": "Waldgrenze"}


def url_fuer(tag: date, region: str = "AT-02") -> str:
    d = tag.isoformat()
    return f"https://static.avalanche.report/eaws_bulletins/{d}/{d}-{region}.json"


async def fetch(client: httpx.AsyncClient, tag: date, region: str = "AT-02") -> list[dict[str, Any]] | None:
    """None heisst: kein Bulletin fuer diesen Tag (Sommer, oder noch nicht da)."""
    resp = await client.get(url_fuer(tag, region))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("bulletins") or None


def _hoehe(rating: dict[str, Any]) -> str:
    e = rating.get("elevation") or {}
    if "upperBound" in e:
        return f"bis {GRENZE.get(e['upperBound'], e['upperBound'])}"
    if "lowerBound" in e:
        return f"ab {GRENZE.get(e['lowerBound'], e['lowerBound'])}"
    return ""


def render(bulletins: list[dict[str, Any]] | None) -> str:
    if not bulletins:
        return "Lawine KTN: kein Bulletin (ausserhalb der Saison)"
    # Hoechste Stufe zaehlt - im Zweifel die vorsichtigere Angabe.
    reihenfolge = list(STUFE)
    beste: tuple[int, str, str] | None = None
    for b in bulletins:
        for r in b.get("dangerRatings", []):
            wert = r.get("mainValue", "no_rating")
            rang = reihenfolge.index(wert) if wert in reihenfolge else -1
            if beste is None or rang > beste[0]:
                beste = (rang, wert, _hoehe(r))
    if beste is None:
        return "Lawine KTN: kein Bulletin (ausserhalb der Saison)"
    _, wert, hoehe = beste
    text = f"Lawine KTN: Stufe {STUFE.get(wert, wert)}"
    return f"{text} ({hoehe})" if hoehe else text
