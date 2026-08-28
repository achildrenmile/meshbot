"""!melde — report coverage gaps and faults from the radio network.

The point of this command: whoever is standing in a dead spot has no mobile
network. A report that only gets filed once back home rarely gets filed at all.
So the bot accepts it over the air directly.

Stored twice — as a file for posterity and as an MQTT message for anything that
wants to do something with it (Telegram, wiki, map).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sota import parse_coords


def erfassen(text: str, sender: str, jetzt: datetime) -> dict[str, Any]:
    """Structure the report. A position is extracted when one is present."""
    koord = parse_coords(text)
    return {
        "zeit": jetzt.isoformat(timespec="seconds"),
        "von": sender,
        "text": " ".join(text.split())[:200],
        "lat": koord[0] if koord else None,
        "lon": koord[1] if koord else None,
    }


def speichern(meldung: dict[str, Any], pfad: Path) -> int:
    """Append, never overwrite. Returns the sequence number."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    nummer = 1
    if pfad.exists():
        with open(pfad, encoding="utf-8") as fh:
            nummer = sum(1 for _ in fh) + 1
    with open(pfad, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**meldung, "nr": nummer}, ensure_ascii=False) + "\n")
    return nummer


def render(meldung: dict[str, Any], nummer: int) -> str:
    """A short acknowledgement — without it nobody knows the report arrived."""
    teil = f"Meldung #{nummer} notiert"
    if meldung.get("lat") is not None:
        teil += f" ({meldung['lat']:.4f},{meldung['lon']:.4f})"
    return teil + ", danke!"


def letzte(pfad: Path, anzahl: int = 3) -> list[dict[str, Any]]:
    if not pfad.exists():
        return []
    with open(pfad, encoding="utf-8") as fh:
        zeilen = fh.readlines()[-anzahl:]
    return [json.loads(z) for z in zeilen]
