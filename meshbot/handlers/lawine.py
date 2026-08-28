"""!lawine — avalanche danger level from the EAWS bulletin.

Source: static.avalanche.report, the joint bulletin of the European warning
services in EAWS format. Carinthia is region `AT-02`.

Outside the season there is no bulletin — that is not an error, and the answer
says so.
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
    """None means: no bulletin for this day (summer, or not published yet)."""
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
    # The highest level counts - when in doubt, the more cautious figure.
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
