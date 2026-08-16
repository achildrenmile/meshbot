"""!spot — wer gerade auf einem Gipfel funkt, aus SOTAwatch.

Standardmaessig nur oesterreichische Aktivierungen: Eine Liste amerikanischer
Spots hilft im Kaerntner Mesh niemandem und kostet dieselbe Sendezeit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


async def fetch(client: httpx.AsyncClient, base_url: str, anzahl: int = 25) -> list[dict[str, Any]]:
    resp = await client.get(f"{base_url}/{anzahl}/all")
    resp.raise_for_status()
    spots = resp.json() or []
    # Die API fuehrt einen Platzhalter mit "DEPRECATED" als erste Zeile.
    return [s for s in spots if s.get("activatorCallsign") and "DEPRECATED" not in str(s.get("activatorCallsign"))]


def filtern(spots: list[dict[str, Any]], assoc: str = "OE") -> list[dict[str, Any]]:
    return [s for s in spots if str(s.get("associationCode", "")).upper() == assoc.upper()]


def _alter(zeitstempel: str, jetzt: datetime) -> str:
    try:
        ts = datetime.fromisoformat(zeitstempel.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    minuten = int((jetzt - ts).total_seconds() / 60)
    if minuten < 1:
        return "jetzt"
    return f"{minuten}min" if minuten < 90 else f"{minuten // 60}h"


def render(spots: list[dict[str, Any]], jetzt: datetime, assoc: str = "OE") -> str:
    if not spots:
        return f"SOTA {assoc}: gerade niemand QRV"
    teile = []
    for s in spots[:2]:
        ruf = str(s.get("activatorCallsign", "?")).upper()
        gipfel = f"{s.get('associationCode','')}/{s.get('summitCode','')}".strip("/")
        freq = str(s.get("frequency", "")).strip()
        modus = str(s.get("mode", "")).upper().replace("OTHER", "")
        alter = _alter(str(s.get("timeStamp", "")), jetzt)
        teile.append(" ".join(x for x in (ruf, gipfel, freq, modus, alter) if x))
    text = " | ".join(teile)
    if len(spots) > 2:
        text += f" +{len(spots) - 2}"
    return text
