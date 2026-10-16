"""Websuche fuer !frag (Werkzeug `suche`, kein eigener Funkbefehl).

Fragt eine selbst gehostete SearXNG-Instanz (SEARCH_URL). Zurueck gehen nur
Domain, Titel und ein Textauszug, hart gekuerzt: Webtext ist unvertraute Eingabe
und darf das Modell nicht steuern. Leere URL = Werkzeug aus.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

log = structlog.get_logger(__name__)

MAX_TREFFER = 3
TITEL_MAX = 45
AUSZUG_MAX = 40
FRAGE_MAX = 120


def _sauber(text: object, maximal: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+|<[^>]*>", " ", str(text or ""))
    return " ".join(text.split())[:maximal]


def formatieren(ergebnisse: list[dict]) -> str | None:
    zeilen = []
    for r in ergebnisse:
        if len(zeilen) >= MAX_TREFFER:
            break
        domain = (urlparse(str(r.get("url", ""))).hostname or "").removeprefix("www.")
        titel = _sauber(r.get("title"), TITEL_MAX)
        if not domain or not titel:
            continue
        auszug = _sauber(r.get("content"), AUSZUG_MAX)
        zeilen.append(f"{domain}: {titel}" + (f" - {auszug}" if auszug else ""))
    return " | ".join(zeilen) or None


class Suche:
    def __init__(self, client: httpx.AsyncClient, url: str, timeout_s: float = 8.0):
        self.client, self.url, self.timeout_s = client, url.rstrip("/"), timeout_s

    async def handler(self, arg: str, sender: str) -> str | None:
        frage = _sauber(arg, FRAGE_MAX)
        if not frage:
            return None
        resp = await self.client.get(
            f"{self.url}/search", params={"q": frage, "format": "json"},
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
            timeout=self.timeout_s)
        resp.raise_for_status()
        return formatieren(resp.json().get("results") or [])
