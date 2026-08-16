"""Zeichenlimit und Zeichensatz.

Airtime ist das knappste Gut: Jede Antwort wird hart begrenzt, bevor sie den
Dienst verlaesst. Lieber eine gekuerzte Zeile als zwei Pakete.
"""

from __future__ import annotations

UMLAUTE = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a", "č": "c", "š": "s", "ž": "z",
}


def transliterate(text: str) -> str:
    """Umlaute ersetzen. MeshCore kann UTF-8, aber nicht jedes Display."""
    for k, v in UMLAUTE.items():
        text = text.replace(k, v)
    return text


def clamp(text: str, limit: int) -> str:
    """Auf die Zeichengrenze kuerzen, moeglichst an einer Wortgrenze.

    Der Rest wird mit einem einzelnen Zeichen markiert, damit der Empfaenger
    sieht, dass etwas fehlt — das kostet weniger als drei Punkte.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[int(limit * 0.6):]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:") + "…"


def prepare(text: str, limit: int, do_transliterate: bool) -> str:
    if do_transliterate:
        text = transliterate(text)
    return clamp(text, limit)
