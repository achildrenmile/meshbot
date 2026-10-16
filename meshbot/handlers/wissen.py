"""Gepflegte Wissensbasis fuer !frag (Werkzeug `wissen`, kein eigener Funkbefehl).

Fragen, auf die ein Sprachmodell nichts Verlaessliches weiss, deren Antwort aber
feststeht (Namensschema, Regeln, Verweise), stehen hier als kurzer Text mit Quelle.
Die Datei wird bei Aenderung neu gelesen (in Kubernetes eine ConfigMap).

    {"eintraege": [
      {"thema": "Repeater benennen", "stichworte": ["repeater", "name", "benennen"],
       "text": "AT-BEZIRK-NAME, z.B. AT-VI-Name", "quelle": "meshcore.at"}
    ]}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

MAX_TREFFER = 1
MIN_PUNKTE = 2


def _worte(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9äöüß]+", text.lower()) if len(w) > 2}


class Wissen:
    def __init__(self, pfad: Path) -> None:
        self.pfad = Path(pfad)
        self._mtime: float | None = None
        self._eintraege: list[dict] = []

    def aktuell(self) -> list[dict]:
        try:
            mtime = self.pfad.stat().st_mtime
        except OSError:
            self._mtime, self._eintraege = None, []
            return self._eintraege
        if mtime != self._mtime:
            try:
                roh = json.loads(self.pfad.read_text(encoding="utf-8")).get("eintraege", [])
                self._eintraege = [e for e in roh if isinstance(e, dict) and e.get("text")]
                self._mtime = mtime
            except (OSError, ValueError, AttributeError) as exc:
                log.warning("wissen_nicht_lesbar", fehler=str(exc))
        return self._eintraege

    def suche(self, frage: str) -> list[dict]:
        gesucht = _worte(frage)
        bewertet = []
        for e in self.aktuell():
            punkte = len(gesucht & (_worte(" ".join(map(str, e.get("stichworte", [])))) | _worte(str(e.get("thema", "")))))
            if punkte >= MIN_PUNKTE:
                bewertet.append((punkte, e))
        bewertet.sort(key=lambda p: -p[0])
        return [e for _, e in bewertet[:MAX_TREFFER]]

    async def handler(self, arg: str, sender: str) -> str | None:
        treffer = self.suche(arg)
        if not treffer:
            return None
        return " | ".join(
            f"{e['text']}" + (f" ({e['quelle']})" if e.get("quelle") else "") for e in treffer)
