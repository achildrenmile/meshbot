"""Token-Bucket und Duplikaterkennung.

Zwei Bremsen: eine global fuer das Funknetz, eine pro Absender gegen einzelne
Vielsender. Wird eine ausgeloest, passiert **nichts** — keine Antwort, keine
Fehlermeldung. Eine Absage kostet genauso viel Sendezeit wie eine Antwort.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Klassischer Token-Bucket: `limit` Freigaben je `window` Sekunden."""

    limit: int
    window: float
    _tokens: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.limit)
        self._last = time.monotonic()

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        # Nie negativ: Der Aufrufer kann einen Zeitstempel mitgeben, der vor der
        # Erzeugung des Buckets liegt — dann waere die erste Freigabe verloren.
        elapsed = max(0.0, now - self._last)
        self._last = max(now, self._last)
        self._tokens = min(float(self.limit), self._tokens + elapsed * self.limit / self.window)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class SenderLimiter:
    """Ein Bucket je Absender, aufgeräumt wird beim Zugriff."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._buckets: dict[str, TokenBucket] = {}
        self._seen: dict[str, float] = {}

    def allow(self, sender: str) -> bool:
        now = time.monotonic()
        self._seen[sender] = now
        for key, last in list(self._seen.items()):     # alte Absender vergessen
            if now - last > self.window * 10:
                self._seen.pop(key, None)
                self._buckets.pop(key, None)
        bucket = self._buckets.setdefault(sender, TokenBucket(self.limit, self.window))
        return bucket.allow(now)


class Deduplicator:
    """Gleicher Befehl vom gleichen Absender innerhalb des Fensters = Duplikat.

    Im Mesh ist Mehrfachempfang der Normalfall, nicht die Ausnahme: Dasselbe
    Paket kommt ueber mehrere Repeater herein.
    """

    def __init__(self, window: float) -> None:
        self.window = window
        self._seen: dict[tuple[str, str], float] = {}

    def is_duplicate(self, sender: str, text: str) -> bool:
        now = time.monotonic()
        key = (sender.strip().lower(), " ".join(text.split()).lower())
        for k, ts in list(self._seen.items()):
            if now - ts > self.window:
                del self._seen[k]
        if key in self._seen:
            return True
        self._seen[key] = now
        return False
