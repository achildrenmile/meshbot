"""Character limit and character set.

Airtime is the scarcest resource: every answer is capped hard before it leaves
the service. A truncated line beats two packets.
"""

from __future__ import annotations

UMLAUTE = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a", "č": "c", "š": "s", "ž": "z",
}


def transliterate(text: str) -> str:
    """Replace umlauts. MeshCore speaks UTF-8, but not every display does.

    Off by default since 2026-08-28 -- the displays on the network handle them,
    and "Noetsch" is not the name of the place.
    """
    for k, v in UMLAUTE.items():
        text = text.replace(k, v)
    return text


def clamp(text: str, limit: int) -> str:
    """Cut to the character limit, at a word boundary where possible.

    The remainder is marked with a single character so the receiver can see
    something is missing — cheaper than three dots.
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
