"""!events — the next upcoming event from a hand-maintained list.

No internet source: the list is a JSON file that someone curates. It is read
again whenever its modification time changes, so editing it (in the cluster a
mounted ConfigMap) takes effect without restarting the bot.

Exactly one event is answered: the first one that has not yet passed. A radio
message is expensive, a list of five dates is not what anybody asks for on the
air.

File format -- a list under "events", dates ISO, everything but datum and name
optional:

    {"events": [
      {"datum": "2026-11-14", "zeit": "10:00", "name": "Mesh-Treffen",
       "ort": "Villach", "bis": "2026-11-15"}
    ]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Event:
    start: date
    ende: date
    zeit: time | None
    name: str
    ort: str


def parse(daten: object) -> list[Event]:
    """Entries that cannot be read are skipped, not fatal: one typo in the file
    must not take the command down for everybody."""
    roh = daten.get("events", []) if isinstance(daten, dict) else []
    ergebnis: list[Event] = []
    for eintrag in roh:
        try:
            start = date.fromisoformat(str(eintrag["datum"]))
            ende = date.fromisoformat(str(eintrag.get("bis") or eintrag["datum"]))
            zeit = time.fromisoformat(str(eintrag["zeit"])) if eintrag.get("zeit") else None
            name = str(eintrag["name"]).strip()
            if not name or ende < start:
                raise ValueError("Name leer oder Ende vor Beginn")
            ergebnis.append(Event(start, ende, zeit, name, str(eintrag.get("ort") or "").strip()))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("event_ungueltig", eintrag=str(eintrag)[:80], fehler=str(exc))
    return sorted(ergebnis, key=lambda e: (e.start, e.zeit or time.min, e.name))


class EventListe:
    def __init__(self, pfad: Path) -> None:
        self.pfad = Path(pfad)
        self._mtime: float | None = None
        self._events: list[Event] = []

    def aktuell(self) -> list[Event]:
        try:
            mtime = self.pfad.stat().st_mtime
        except OSError:
            self._mtime, self._events = None, []
            return self._events
        if mtime != self._mtime:
            try:
                self._events = parse(json.loads(self.pfad.read_text(encoding="utf-8")))
                self._mtime = mtime
            except (OSError, ValueError) as exc:
                # Keep the last good list; a half-written file must not wipe it.
                log.warning("events_nicht_lesbar", fehler=str(exc))
        return self._events


def naechstes(events: list[Event], jetzt: datetime) -> Event | None:
    """First event not yet over. Without a time it counts for the whole day, with
    a time only until that time; a multi-day event until the end of its last day."""
    for e in events:
        if e.ende > e.start or e.zeit is None:
            if e.ende >= jetzt.date():
                return e
        elif datetime.combine(e.start, e.zeit) >= jetzt.replace(tzinfo=None):
            return e
    return None


def render(e: Event | None, heute: date) -> str:
    if e is None:
        return "Events: keine geplant"
    if e.start <= heute <= e.ende and e.ende > e.start:
        wann = f"laeuft bis {e.ende:%d.%m.}"
    else:
        tage = (e.start - heute).days
        wann = "heute" if tage == 0 else "morgen" if tage == 1 else f"in {tage} Tagen"
        datum = f"{e.start:%d.%m.}" + (f"-{e.ende:%d.%m.}" if e.ende > e.start else "")
        wann = f"{datum}{' ' + e.zeit.strftime('%H:%M') if e.zeit else ''} ({wann})"
    return f"Event: {e.name}{', ' + e.ort if e.ort else ''} - {wann}"


def jetzt_lokal(tz_offset_h: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=tz_offset_h)
