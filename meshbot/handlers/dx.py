"""!dx — propagation conditions on shortwave.

Source: hamqsl.com (N0NBH), the solar widget radio amateurs commonly use. It
serves XML with solar flux, A and K index, sunspots and X-ray class.

For the mesh itself this is irrelevant — 869 MHz does not care about a K index.
It is here because there are radio amateurs on the channel for whom it is the
first question of the day.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

FELDER = ("solarflux", "aindex", "kindex", "sunspots", "xray")


async def fetch(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    resp = await client.get(url)
    resp.raise_for_status()
    text = resp.text
    werte: dict[str, str] = {}
    for feld in FELDER:
        # No XML parser: the document is flat, and a parser would lose the whole
        # answer over any broken character instead of just one field.
        m = re.search(rf"<{feld}>\s*([^<]*?)\s*</{feld}>", text)
        if m and m.group(1):
            werte[feld] = m.group(1).strip()
    if not werte:
        raise ValueError("keine Solardaten im Dokument")
    return werte


def _stufe(k: str) -> str:
    """The K index in plain words. From 5 up it is a storm, below that noise."""
    try:
        wert = float(k)
    except ValueError:
        return ""
    if wert >= 5:
        return " STURM"
    if wert >= 4:
        return " unruhig"
    return ""


def render(werte: dict[str, Any]) -> str:
    teile = []
    if "solarflux" in werte:
        teile.append(f"SFI {werte['solarflux']}")
    if "aindex" in werte:
        teile.append(f"A{werte['aindex']}")
    if "kindex" in werte:
        teile.append(f"K{werte['kindex']}{_stufe(werte['kindex'])}")
    if "sunspots" in werte:
        teile.append(f"SN {werte['sunspots']}")
    if "xray" in werte:
        teile.append(f"Xray {werte['xray']}")
    return "DX: " + ", ".join(teile)
