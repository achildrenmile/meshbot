"""Command recognition and dispatch.

Stance: **when in doubt, do not send.** An unknown command, a foreign sender id,
a duplicate or a tripped limit lead to silence, not to an error message — every
answer costs airtime in a shared band.
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
    "warn": "warn", "warnung": "warn",
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
    "wo": "wo", "node": "wo", "pfad": "wo", "hash": "wo", "path": "wo",
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
    # "berg" is also the name of a help group. No conflict: cmd_help resolves
    # groups before aliases, and the *command* !berg still works. "summit" is
    # already taken by !sota and stays there.
    "gipfel": "gipfel", "berg": "gipfel", "peak": "gipfel",
    "version": "version", "ver": "version", "stand": "version",
    "frag": "frag", "frage": "frag", "ask": "frag", "ki": "frag",
}


# Renamed commands. They are no longer executed but answered with a pointer to
# the new name -- silence would be the worse answer here: someone who knows the
# old command would otherwise conclude the bot is broken.
VERALTET = {
    "uwz": "!uwz heisst jetzt !warn. Gleiche Daten (GeoSphere Austria), neuer Name",
}


def getippter_name(text: str) -> str | None:
    """The command name as typed — before resolution through ALIASES."""
    text = text.strip()
    if not text.startswith("!"):
        return None
    teile = text[1:].split(maxsplit=1)
    return teile[0].lower() if teile else None


def dig(data: dict[str, Any], pfad: str) -> Any:
    """Fetch a nested value: `payload.text` descends two levels.

    The bridge wraps the event, the payload text sits one level below. A dotted
    path keeps that configurable instead of assuming the format.
    """
    wert: Any = data
    for teil in pfad.split("."):
        if not isinstance(wert, dict):
            return None
        wert = wert.get(teil)
    return wert


def split_sender_prefix(text: str) -> tuple[str | None, str]:
    """`"AT-Node: !help"` -> `("AT-Node", "!help")`.

    On channel messages MeshCore prefixes the sender name, so the command does
    not start with `!`. The app performs the same split.
    """
    stelle = text.find(": ")
    if 0 < stelle < 50:
        name = text[:stelle]
        if not any(z in name for z in ":[]!"):
            return name, text[stelle + 2:].strip()
    return None, text


def parse_payload(raw: bytes | str, settings: Settings) -> Eingang | None:
    """Split the bridge's raw payload into text, sender and channel."""
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

    # On channel messages the sender is in the text, not in a field of its own.
    name, text = split_sender_prefix(text)
    if not sender:
        sender = name or "unbekannt"

    return Eingang(text=text, sender=sender, channel=None if channel is None else str(channel))


def parse_command(text: str) -> tuple[str, str] | None:
    """`!wx villach` → `("wx", "villach")`. No prefix, no command."""
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
        """Returns the finished answer, or None when staying silent."""
        if not self.enabled:
            return None

        eingang = parse_payload(raw, self.settings)
        if eingang is None or not eingang.text:
            return None

        # Never take our own messages for a command — that would be a loop.
        if eingang.sender.strip().lower() == self.settings.bot_name.lower():
            return None

        if self.settings.channel_filter and eingang.channel not in (None, self.settings.channel_filter):
            return None

        getippt = getippter_name(eingang.text)
        befehl = parse_command(eingang.text)
        if befehl is None and getippt not in VERALTET:
            return None
        name, argument = befehl if befehl else (getippt, "")

        # From here on it is established that a command came in for us. Without
        # this line there is no telling afterwards whether a request never
        # arrived or the answer was lost on the way back -- both look identical
        # in the log, namely like nothing at all.
        log.info("befehl", sender=eingang.sender, cmd=name,
                 arg=argument[:24], kanal=eingang.channel)

        if self.dedup.is_duplicate(eingang.sender, eingang.text):
            log.info("duplikat", sender=eingang.sender, cmd=name)
            return None
        if not self.sender_limiter.allow(eingang.sender):
            log.info("absenderlimit", sender=eingang.sender, cmd=name)
            return None
        if not self.global_bucket.allow():
            log.info("globales_limit", cmd=name)
            return None

        # Old name: a pointer instead of execution. Only here, so duplicates and
        # limits apply to it as well -- otherwise it would be the cheapest way to
        # flood the network.
        if getippt in VERALTET:
            self.served += 1
            log.info("veralteter_befehl", cmd=getippt, sender=eingang.sender)
            return prepare(VERALTET[getippt], self.settings.nutzlimit, self.settings.transliterate)

        handler = self.handlers.get(name)
        if handler is None:
            return None
        try:
            antwort = await handler(argument, eingang.sender)
        except Exception as exc:                       # never take the service down
            log.exception("handler_fehler", cmd=name, error=str(exc))
            return None
        if not antwort:
            return None

        self.served += 1
        return prepare(antwort, self.settings.nutzlimit, self.settings.transliterate)
