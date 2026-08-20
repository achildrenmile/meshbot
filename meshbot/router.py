"""Befehlserkennung und Weiterleitung.

Grundhaltung: **Im Zweifel nicht senden.** Ein unbekannter Befehl, eine fremde
Absenderkennung, ein Duplikat oder ein gerissenes Limit führen zu Stille, nicht
zu einer Fehlermeldung — jede Antwort kostet Sendezeit im geteilten Band.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import structlog

from .config import Settings
from .formatting import prepare
from .ratelimit import Deduplicator, SenderLimiter, TokenBucket

log = structlog.get_logger(__name__)

Handler = Callable[[str, str], Awaitable[str | None]]


@dataclass
class Eingang:
    text: str
    sender: str
    channel: str | None


ALIASES = {
    "wx": "wx", "wetter": "wx",
    "uwz": "uwz", "warn": "uwz",
    "sota": "sota", "summit": "sota",
    "relais": "relais", "rpt": "relais",
    "ping": "ping",
    "help": "help", "hilfe": "help",
    "sonne": "sonne", "sun": "sonne",
    "spot": "spot", "spots": "spot",
    "lawine": "lawine", "avalanche": "lawine",
    "netz": "netz", "status": "netz",
    "vorhersage": "vorhersage", "morgen": "vorhersage", "fc": "vorhersage",
    "zeit": "zeit", "time": "zeit", "utc": "zeit",
    "wo": "wo", "node": "wo",
    "melde": "melde", "luecke": "melde", "report": "melde",
    "qth": "qth", "loc": "qth", "locator": "qth",
    "sicht": "sicht", "los": "sicht", "sichtverbindung": "sicht",
    "hoehe": "hoehe", "höhe": "hoehe", "alt": "hoehe", "seehoehe": "hoehe",
    "dist": "dist", "distanz": "dist", "entfernung": "dist", "peilung": "dist",
    "dx": "dx", "solar": "dx", "bedingungen": "dx",
    "mond": "mond", "moon": "mond",
    "az": "az", "sotaaz": "az", "zone": "az", "gipfelzone": "az", "aktivierungszone": "az",
    "quota": "quota", "kontingent": "quota", "rest": "quota",
    "iss": "iss", "sat": "iss",
}


def dig(data: dict[str, Any], pfad: str) -> Any:
    """Verschachtelten Wert holen: `payload.text` steigt zwei Ebenen hinab.

    Die Bruecke verpackt das Ereignis, der Nutztext liegt eine Ebene tiefer.
    Ein Punktpfad haelt das konfigurierbar, statt das Format anzunehmen.
    """
    wert: Any = data
    for teil in pfad.split("."):
        if not isinstance(wert, dict):
            return None
        wert = wert.get(teil)
    return wert


def split_sender_prefix(text: str) -> tuple[str | None, str]:
    """`"AT-Node: !help"` -> `("AT-Node", "!help")`.

    MeshCore stellt bei Kanalnachrichten den Absendernamen voran — der Befehl
    beginnt dadurch nicht mit `!`. Dieselbe Trennung macht auch die App.
    """
    stelle = text.find(": ")
    if 0 < stelle < 50:
        name = text[:stelle]
        if not any(z in name for z in ":[]!"):
            return name, text[stelle + 2:].strip()
    return None, text


def parse_payload(raw: bytes | str, settings: Settings) -> Eingang | None:
    """Rohnutzlast der Bruecke in Text, Absender und Kanal zerlegen."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if settings.payload_format == "text":
        name, text = split_sender_prefix(raw.strip())
        return Eingang(text=text, sender=name or "unbekannt", channel=None)
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None
    text = str(dig(data, settings.json_path_text) or "").strip()
    sender = str(dig(data, settings.json_path_sender) or "").strip()
    channel = dig(data, settings.json_path_channel)

    # Absender steht bei Kanalnachrichten im Text, nicht in einem eigenen Feld.
    name, text = split_sender_prefix(text)
    if not sender:
        sender = name or "unbekannt"

    return Eingang(text=text, sender=sender, channel=None if channel is None else str(channel))


def parse_command(text: str) -> tuple[str, str] | None:
    """`!wx villach` → `("wx", "villach")`. Kein Präfix, kein Befehl."""
    text = text.strip()
    if not text.startswith("!"):
        return None
    teile = text[1:].split(maxsplit=1)
    if not teile:
        return None
    name = ALIASES.get(teile[0].lower())
    if name is None:
        return None
    return name, (teile[1].strip() if len(teile) > 1 else "")


class Router:
    def __init__(self, settings: Settings, handlers: dict[str, Handler]) -> None:
        self.settings = settings
        self.handlers = handlers
        self.global_bucket = TokenBucket(settings.global_limit, settings.global_window_s)
        self.sender_limiter = SenderLimiter(settings.sender_limit, settings.sender_window_s)
        self.dedup = Deduplicator(settings.dedup_window_s)
        self.enabled = settings.bot_enabled
        self.started = time.time()
        self.served = 0

    def uptime(self) -> str:
        s = int(time.time() - self.started)
        return f"{s // 86400}d{s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h{s % 3600 // 60}m"

    async def handle(self, raw: bytes | str) -> str | None:
        """Rückgabe: fertige Antwort oder None, wenn geschwiegen wird."""
        if not self.enabled:
            return None

        eingang = parse_payload(raw, self.settings)
        if eingang is None or not eingang.text:
            return None

        # Eigene Nachrichten nie als Befehl auffassen — sonst Endlosschleife.
        if eingang.sender.strip().lower() == self.settings.bot_name.lower():
            return None

        if self.settings.channel_filter and eingang.channel not in (None, self.settings.channel_filter):
            return None

        befehl = parse_command(eingang.text)
        if befehl is None:
            return None
        name, argument = befehl

        if self.dedup.is_duplicate(eingang.sender, eingang.text):
            log.info("duplikat", sender=eingang.sender, cmd=name)
            return None
        if not self.sender_limiter.allow(eingang.sender):
            log.info("absenderlimit", sender=eingang.sender, cmd=name)
            return None
        if not self.global_bucket.allow():
            log.info("globales_limit", cmd=name)
            return None

        handler = self.handlers.get(name)
        if handler is None:
            return None
        try:
            antwort = await handler(argument, eingang.sender)
        except Exception as exc:                       # nie den Dienst mitreissen
            log.exception("handler_fehler", cmd=name, error=str(exc))
            return None
        if not antwort:
            return None

        self.served += 1
        return prepare(antwort, self.settings.nutzlimit, self.settings.transliterate)
