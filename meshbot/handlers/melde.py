"""!melde — Abdeckungsluecken und Stoerungen aus dem Funknetz melden.

Der Punkt dieses Befehls: Wer in einem Funkloch steht, hat kein Handynetz. Eine
Meldung, die erst zu Hause abgesetzt wird, kommt selten. Deshalb nimmt der Bot
sie direkt ueber Funk entgegen.

Gespeichert wird zweifach — als Datei fuer die Nachwelt und als MQTT-Nachricht
fuer alles, was daraus etwas machen will (Telegram, Wiki, Karte).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sota import parse_coords


def erfassen(text: str, sender: str, jetzt: datetime) -> dict[str, Any]:
    """Meldung strukturieren. Position wird herausgezogen, wenn eine drinsteht."""
    koord = parse_coords(text)
    return {
        "zeit": jetzt.isoformat(timespec="seconds"),
        "von": sender,
        "text": " ".join(text.split())[:200],
        "lat": koord[0] if koord else None,
        "lon": koord[1] if koord else None,
    }


def speichern(meldung: dict[str, Any], pfad: Path) -> int:
    """Anhaengen, nie ueberschreiben. Rueckgabe: laufende Nummer."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    nummer = 1
    if pfad.exists():
        with open(pfad, encoding="utf-8") as fh:
            nummer = sum(1 for _ in fh) + 1
    with open(pfad, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**meldung, "nr": nummer}, ensure_ascii=False) + "\n")
    return nummer


def render(meldung: dict[str, Any], nummer: int) -> str:
    """Kurze Bestaetigung — ohne sie weiss niemand, ob die Meldung ankam."""
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
