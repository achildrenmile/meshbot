"""!dx — Ausbreitungsbedingungen auf Kurzwelle.

Quelle: hamqsl.com (N0NBH), das übliche Solar-Widget der Funkamateure. Es
liefert XML mit Sonnenfluss, A- und K-Index, Sonnenflecken und Röntgenklasse.

Für das Mesh selbst ist das ohne Belang — 869 MHz interessiert kein K-Index.
Es steht hier, weil auf dem Kanal Funkamateure unterwegs sind, für die es die
erste Frage des Tages ist.
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
        # Kein XML-Parser: Das Dokument ist flach, und ein Parser wuerde bei
        # jedem kaputten Zeichen die ganze Antwort verlieren statt eines Feldes.
        m = re.search(rf"<{feld}>\s*([^<]*?)\s*</{feld}>", text)
        if m and m.group(1):
            werte[feld] = m.group(1).strip()
    if not werte:
        raise ValueError("keine Solardaten im Dokument")
    return werte


def _stufe(k: str) -> str:
    """K-Index in Klartext. Ab 5 ist es ein Sturm, darunter nur Rauschen."""
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
