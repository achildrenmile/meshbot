"""!events: exactly the next event, by date."""

import json
from datetime import date, datetime

from meshbot.handlers import events as ev


def _liste(*eintraege):
    return ev.parse({"events": list(eintraege)})


JETZT = datetime(2026, 10, 16, 14, 0)


def test_naechstes_nach_datum_nicht_nach_reihenfolge_in_der_datei():
    liste = _liste({"datum": "2026-12-01", "name": "Spaet"},
                   {"datum": "2026-10-20", "name": "Frueh"},
                   {"datum": "2026-11-05", "name": "Mitte"})
    assert ev.naechstes(liste, JETZT).name == "Frueh"


def test_vergangene_werden_uebersprungen():
    liste = _liste({"datum": "2026-10-01", "name": "Alt"}, {"datum": "2026-11-01", "name": "Neu"})
    assert ev.naechstes(liste, JETZT).name == "Neu"


def test_heute_ohne_zeit_zaehlt_den_ganzen_tag():
    liste = _liste({"datum": "2026-10-16", "name": "Heute"})
    assert ev.naechstes(liste, datetime(2026, 10, 16, 23, 30)).name == "Heute"


def test_heute_mit_vergangener_zeit_ist_vorbei():
    liste = _liste({"datum": "2026-10-16", "zeit": "10:00", "name": "Morgens"},
                   {"datum": "2026-10-17", "name": "Morgen"})
    assert ev.naechstes(liste, JETZT).name == "Morgen"


def test_mehrtaegiges_event_laeuft():
    liste = _liste({"datum": "2026-10-15", "bis": "2026-10-17", "name": "Camp"},
                   {"datum": "2026-10-20", "name": "Danach"})
    e = ev.naechstes(liste, JETZT)
    assert e.name == "Camp"
    assert "laeuft bis 17.10." in ev.render(e, date(2026, 10, 16))


def test_keine_events():
    assert ev.naechstes([], JETZT) is None
    assert ev.render(None, JETZT.date()) == "Events: keine geplant"


def test_render_kurz_und_lesbar():
    e = _liste({"datum": "2026-11-14", "zeit": "10:00", "name": "Mesh-Treffen", "ort": "Villach"})[0]
    text = ev.render(e, date(2026, 10, 16))
    assert text == "Event: Mesh-Treffen, Villach - 14.11. 10:00 (in 29 Tagen)"
    assert "morgen" in ev.render(e, date(2026, 11, 13))
    assert "heute" in ev.render(e, date(2026, 11, 14))


def test_kaputter_eintrag_kippt_nicht_die_liste():
    liste = _liste({"datum": "kein-datum", "name": "X"}, {"name": "ohne Datum"},
                   {"datum": "2026-11-01", "bis": "2026-10-01", "name": "Ende vor Anfang"},
                   {"datum": "2026-11-02", "name": "Gut"})
    assert [e.name for e in liste] == ["Gut"]


def test_datei_wird_bei_aenderung_neu_gelesen(tmp_path):
    f = tmp_path / "events.json"
    f.write_text(json.dumps({"events": [{"datum": "2026-11-01", "name": "A"}]}))
    el = ev.EventListe(f)
    assert [e.name for e in el.aktuell()] == ["A"]
    f.write_text(json.dumps({"events": [{"datum": "2026-11-01", "name": "B"}]}))
    import os
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert [e.name for e in el.aktuell()] == ["B"]


def test_kaputte_datei_behaelt_letzte_gute_liste(tmp_path):
    f = tmp_path / "events.json"
    f.write_text(json.dumps({"events": [{"datum": "2026-11-01", "name": "A"}]}))
    el = ev.EventListe(f)
    el.aktuell()
    f.write_text("{halb")
    import os
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert [e.name for e in el.aktuell()] == ["A"]


def test_fehlende_datei_ist_leere_liste(tmp_path):
    assert ev.EventListe(tmp_path / "gibt-es-nicht.json").aktuell() == []
