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


STAMM = 5


def _worte(text: str) -> set[str]:
    text = text.lower().replace("ß", "ss")
    return {w for w in re.findall(r"[a-z0-9äöü]+", text) if len(w) > 2}


def _passt(a: str, b: str) -> bool:
    """Gleiches Wort, oder lange Woerter mit gleichem Anfang (benenne ~ benennung)."""
    return a == b or (len(a) >= STAMM and len(b) >= STAMM and a[:STAMM] == b[:STAMM])


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

    def _bewertet(self, frage: str) -> list[tuple[int, dict]]:
        gesucht = _worte(frage)
        klein = " ".join(frage.lower().replace("ß", "ss").split())
        bewertet = []
        for e in self.aktuell():
            if not all(str(t).lower() in klein for t in e.get("enthaelt", [])):
                continue
            stich = _worte(" ".join(map(str, e.get("stichworte", [])))) | _worte(str(e.get("thema", "")))
            punkte = sum(1 for s in stich if any(_passt(g, s) for g in gesucht))
            if punkte >= MIN_PUNKTE:
                bewertet.append((punkte, e))
        bewertet.sort(key=lambda p: -p[0])
        return bewertet

    def suche(self, frage: str) -> list[dict]:
        return [e for _, e in self._bewertet(frage)[:MAX_TREFFER]]

    def direkt(self, frage: str) -> str | None:
        """Antwort ohne Modell, wenn der beste Treffer `"direkt": true` traegt.

        Das Modell ruft das Werkzeug bei Fragen wie "was ist ein AT-LV8 Repeater"
        nicht auf und erfindet eine Antwort; deterministisch geht vor Prompt.
        """
        bewertet = self._bewertet(frage)
        if bewertet and bewertet[0][1].get("direkt") is True:
            return self._text(bewertet[0][1])
        return None

    @staticmethod
    def _text(e: dict) -> str:
        return f"{e['text']}" + (f" ({e['quelle']})" if e.get("quelle") else "")

    async def handler(self, arg: str, sender: str) -> str | None:
        treffer = self.suche(arg)
        if not treffer:
            return None
        return " | ".join(self._text(e) for e in treffer)
