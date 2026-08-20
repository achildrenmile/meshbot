"""!quota — wie viele Sendungen gehen diese Stunde noch?

Aliase: `!kontingent`, `!rest`. Der kurze Name ist die Hauptform, weil die
Uebersicht von `!help` alle Befehle in eine Nachricht bringen soll — mit
`kontingent` als Hauptnamen platzt sie und faellt auf ein Themenmenue zurueck.

Zwischen Bot und Funknetz sitzt das Gate des meshinfra-Stacks und laesst pro
Stunde nur eine feste Zahl Sendungen durch. Alles darueber wird **verworfen,
nicht gepuffert** — wer ins Limit laeuft, merkt es sonst gar nicht: Der Bot
schweigt, das Sendefenster meldet trotzdem Erfolg.

Der Stand kommt nicht aus einer Nachfrage, sondern liegt schon da: Das Gate
veroeffentlicht ihn retained auf `meshinfra/gate/quota`, der Bot hoert nur mit.
Die Abfrage selbst kostet deshalb keine Anfrage nach aussen — aber sehr wohl
**eine Sendung**, denn die Antwort geht durchs selbe Gate. Genau darum steht
das in der Antwort mit drin.

Zwei Bremsen, zwei Zahlen:

* **Gate** — geteiltes Kontingent fuer alles, was aus der IT ins Netz sendet:
  Bot, Sendefenster, Alarme
* **Bot** — eigener Token-Bucket nur fuer Botantworten, deutlich kuerzeres
  Fenster
"""

from __future__ import annotations

import json
from typing import Any


def parse(payload: bytes | str) -> dict[str, Any] | None:
    """Quota-Meldung des Gates lesen. None, wenn sie nicht brauchbar ist."""
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
    """Eine Zeile, Gate zuerst - das ist die Bremse, die wirklich beisst."""
    bot = f"Bot {bot_frei}/{bot_limit} pro {_dauer(bot_fenster_s)}"

    if gate is None:
        # Kein retained Wert: entweder ist das Gate nie gelaufen, seit der
        # Broker lebt, oder es veroeffentlicht auf einem anderen Topic. Beides
        # ist eine ehrliche Absage wert statt einer erfundenen Zahl.
        return f"Kontingent: Gate meldet nichts. {bot}"

    frei = int(gate.get("remaining", 0))
    limit = int(gate.get("limit", 0))
    fenster = _dauer(int(gate.get("fenster_s", 3600)))

    if frei <= 0:
        wartezeit = int(gate.get("frei_in_s", 0) or 0)
        wann = f", naechster Platz in {_dauer(wartezeit)}" if wartezeit else ""
        return f"Kontingent: 0/{limit} pro {fenster} - voll{wann}. {bot}"

    # Diese Antwort laeuft selbst durchs Gate. Wer '3 frei' liest und drei
    # Nachrichten plant, hat sich um eine vertan.
    return (f"Kontingent: {frei}/{limit} pro {fenster} frei "
            f"(inkl. dieser Antwort). {bot}")
