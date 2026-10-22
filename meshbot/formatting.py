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


def utf8_len(text: str) -> int:
    """Length on the air: the firmware counts bytes, an umlaut is two."""
    return len(text.encode("utf-8"))


def clamp(text: str, limit: int, max_bytes: int | None = None) -> str:
    """Cut to the character limit (and the byte limit, if given), at a word
    boundary where possible.

    The remainder is marked with a single character so the receiver can see
    something is missing -- cheaper than three dots. The ellipsis is three
    bytes in UTF-8 and is counted as such. Never cuts through a multibyte
    character.
    """
    text = " ".join(text.split())
    if len(text) <= limit and (max_bytes is None or utf8_len(text) <= max_bytes):
        return text
    cut = text[: limit - 1]
    if max_bytes is not None and utf8_len(cut) > max_bytes - len("…".encode()):
        cut = cut.encode("utf-8")[: max_bytes - len("…".encode())].decode("utf-8", "ignore")
    ref = min(limit, len(cut) + 1)
    if " " in cut[int(ref * 0.6):]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:") + "…"


def prepare(text: str, limit: int, do_transliterate: bool, max_bytes: int | None = None) -> str:
    if do_transliterate:
        text = transliterate(text)
    return clamp(text, limit, max_bytes)
