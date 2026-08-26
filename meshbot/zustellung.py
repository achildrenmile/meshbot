"""Kam die Antwort an? — Messung und ein zweiter Versuch.

Der Bot hoert **seine eigene Antwort** ueber das Funknetz zurueck, sobald ein
Repeater sie wiederholt. Genau daran laesst sich messen, was sonst niemand
sieht: Die Sendebruecke meldet fuer jede Antwort Erfolg, auch fuer die, die
kein Empfaenger je bekommt.

Gemessen am 26.08.2026 ueber zwoelf Stunden:

    bis 59 Zeichen   11 von 12 angekommen   92 %
    ab  60 Zeichen    7 von 15 angekommen   47 %

**Ein ausbleibendes Echo ist kein Beweis fuer einen Verlust.** Wer den
sendenden Knoten direkt hoert, bekommt die Antwort auch dann, wenn sie kein
Repeater wiederholt. Die Messung ist deshalb eine Untergrenze, und der
Zweitversuch schickt gelegentlich etwas, das schon angekommen war -- das ist
der Preis, und er steht in der Statistik.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


def schluessel(text: str) -> str:
    """Vergleichsform. Das Echo kommt mit vorangestelltem Absendernamen zurueck."""
    return " ".join(text.split()).strip().lower()


@dataclass
class Offen:
    text: str
    laenge: int
    gesendet: float
    versuch: int = 1


@dataclass
class Zustellung:
    """Bucht jede Antwort, bis ihr Echo kommt oder die Frist ablaeuft."""

    frist_s: int = 25
    zweitversuche: int = 1
    datei: Path | None = None
    offen: dict[str, Offen] = field(default_factory=dict)
    zugestellt: int = 0
    verloren: int = 0
    wiederholt: int = 0

    def gesendet(self, text: str, versuch: int = 1) -> None:
        self.offen[schluessel(text)] = Offen(text=text, laenge=len(text),
                                             gesendet=time.time(), versuch=versuch)

    def echo(self, roher_text: str) -> Offen | None:
        """Eigene Antwort im Funkverkehr wiedererkannt.

        Verglichen wird auf Enthaltensein, nicht auf Gleichheit: Kanalnachrichten
        tragen den Absendernamen vorne, und manche Bruecken haengen noch etwas an.
        """
        k = schluessel(roher_text)
        for kandidat in list(self.offen):
            if kandidat and kandidat in k:
                eintrag = self.offen.pop(kandidat)
                self.zugestellt += 1
                self._schreibe("zugestellt", eintrag, time.time() - eintrag.gesendet)
                return eintrag
        return None

    def faellig(self, jetzt: float | None = None) -> list[Offen]:
        """Antworten, deren Frist abgelaufen ist. Entfernt sie aus der Liste."""
        jetzt = jetzt if jetzt is not None else time.time()
        reif = [k for k, o in self.offen.items() if jetzt - o.gesendet >= self.frist_s]
        aus = []
        for k in reif:
            eintrag = self.offen.pop(k)
            if eintrag.versuch <= self.zweitversuche:
                self.wiederholt += 1
                self._schreibe("wiederholt", eintrag, jetzt - eintrag.gesendet)
            else:
                self.verloren += 1
                self._schreibe("verloren", eintrag, jetzt - eintrag.gesendet)
            aus.append(eintrag)
        return aus

    def darf_wiederholen(self, eintrag: Offen) -> bool:
        return eintrag.versuch <= self.zweitversuche

    def quote(self) -> dict[str, Any]:
        gesamt = self.zugestellt + self.verloren
        return {"zugestellt": self.zugestellt, "verloren": self.verloren,
                "wiederholt": self.wiederholt,
                "quote": round(100 * self.zugestellt / gesamt) if gesamt else None}

    def _schreibe(self, ereignis: str, o: Offen, nach_s: float) -> None:
        if self.datei is None:
            return
        try:
            with open(self.datei, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "zeit": round(time.time()),
                    "ereignis": ereignis,
                    "laenge": o.laenge,
                    "versuch": o.versuch,
                    "nach_s": round(nach_s, 1),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass          # Messung darf den Betrieb nie stoeren
