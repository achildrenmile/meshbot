"""!quota — how many transmissions are left this hour?

Aliases: `!kontingent`, `!rest`. The short name is the primary one because the
`!help` overview should fit every command into one message — with `kontingent`
as the primary name it bursts and falls back to a topic menu.

Between the bot and the radio network sits the meshinfra stack's gate, which
lets only a fixed number of transmissions through per hour. Anything beyond that
is **discarded, not buffered** — otherwise running into the limit goes
unnoticed: the bot stays silent while the send window still reports success.

The reading does not come from a query, it is already there: the gate publishes
it retained on `meshinfra/gate/quota` and the bot merely listens. Asking
therefore costs no outbound request — but it does cost **one transmission**,
because the answer goes through the same gate. Which is exactly why the answer
says so.

Two brakes, two numbers:

* **Gate** — shared quota for everything transmitting from the IT side into the
  network: bot, send window, alarms
* **Bot** — its own token bucket for bot answers alone, with a much shorter
  window
"""

from __future__ import annotations

import json
from typing import Any


def parse(payload: bytes | str) -> dict[str, Any] | None:
    """Read the gate's quota message. None when it is not usable."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    try:
        daten = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(daten, dict) or "remaining" not in daten:
        return None
    return daten


def _dauer(sekunden: int) -> str:
    if sekunden < 60:
        return f"{sekunden}s"
    if sekunden < 3600:
        return f"{sekunden // 60}min"
    return f"{sekunden // 3600}h{sekunden % 3600 // 60:02d}"


def render(gate: dict[str, Any] | None, bot_frei: int, bot_limit: int,
           bot_fenster_s: int) -> str:
    """One line, gate first - that is the brake that actually bites."""
    bot = f"Bot {bot_frei}/{bot_limit} pro {_dauer(bot_fenster_s)}"

    if gate is None:
        # No retained value: either the gate has never run since the broker came
        # up, or it publishes on a different topic. Both deserve an honest
        # refusal rather than an invented number.
        return f"Kontingent: Gate meldet nichts. {bot}"

    frei = int(gate.get("remaining", 0))
    limit = int(gate.get("limit", 0))
    fenster = _dauer(int(gate.get("fenster_s", 3600)))

    if frei <= 0:
        wartezeit = int(gate.get("frei_in_s", 0) or 0)
        wann = f", naechster Platz in {_dauer(wartezeit)}" if wartezeit else ""
        return f"Kontingent: 0/{limit} pro {fenster} - voll{wann}. {bot}"

    # This answer goes through the gate itself. Reading "3 frei" and planning
    # three messages is off by one.
    return (f"Kontingent: {frei}/{limit} pro {fenster} frei "
            f"(inkl. dieser Antwort). {bot}")
