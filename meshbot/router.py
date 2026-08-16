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
}


def parse_payload(raw: bytes | str, settings: Settings) -> Eingang | None:
    """Rohnutzlast der Bruecke in Text, Absender und Kanal zerlegen."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if settings.payload_format == "text":
        return Eingang(text=raw.strip(), sender="unbekannt", channel=None)
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None
    text = str(data.get(settings.json_path_text, "") or "").strip()
    sender = str(data.get(settings.json_path_sender, "") or "unbekannt").strip()
    channel = data.get(settings.json_path_channel)
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
        return prepare(antwort, self.settings.max_msg_len, self.settings.transliterate)
