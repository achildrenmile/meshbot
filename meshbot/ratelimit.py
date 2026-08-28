"""Token bucket and duplicate detection.

Two brakes: one global one for the radio network, one per sender against
individual heavy users. When either trips, **nothing** happens — no answer, no
error message. A refusal costs exactly as much airtime as an answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Classic token bucket: `limit` grants per `window` seconds."""

    limit: int
    window: float
    _tokens: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.limit)
        self._last = time.monotonic()

    def _nachfuellen(self, now: float) -> None:
        # Never negative: the caller may pass a timestamp from before the bucket
        # was created — the first grant would otherwise be lost.
        elapsed = max(0.0, now - self._last)
        self._last = max(now, self._last)
        self._tokens = min(float(self.limit), self._tokens + elapsed * self.limit / self.window)

    def verfuegbar(self, now: float | None = None) -> int:
        """How many grants are available right now, **without** spending one.

        For `!kontingent`: checking must not cost the same as sending.
        """
        self._nachfuellen(time.monotonic() if now is None else now)
        return int(self._tokens)

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._nachfuellen(now)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class SenderLimiter:
    """One bucket per sender, cleaned up on access."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._buckets: dict[str, TokenBucket] = {}
        self._seen: dict[str, float] = {}

    def allow(self, sender: str) -> bool:
        now = time.monotonic()
        self._seen[sender] = now
        for key, last in list(self._seen.items()):     # forget old senders
            if now - last > self.window * 10:
                self._seen.pop(key, None)
                self._buckets.pop(key, None)
        bucket = self._buckets.setdefault(sender, TokenBucket(self.limit, self.window))
        return bucket.allow(now)


class Deduplicator:
    """Same command from the same sender within the window = duplicate.

    On a mesh, receiving something more than once is the normal case, not the
    exception: the same packet arrives via several repeaters.
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
