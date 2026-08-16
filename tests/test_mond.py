"""Mondrechnung, geprueft gegen die Werte des US Naval Observatory."""

from datetime import date, datetime, timedelta, timezone

import pytest

from meshbot.handlers import mond

VILLACH = (46.6167, 13.85)


def test_auf_und_untergang_gegen_usno():
    """USNO fuer 16.08.2026, 46.6167N 13.85O: Aufgang 08:28, Untergang 19:34 UTC."""
    w = mond.ereignisse(date(2026, 8, 16), *VILLACH)
    assert w["aufgang"].strftime("%H:%M") == "08:28"
    assert abs((w["untergang"] - datetime(2026, 8, 16, 19, 34, tzinfo=timezone.utc))
               .total_seconds()) < 120


@pytest.mark.parametrize("tag,anteil,zunehmend", [
    (date(2026, 8, 12), 0.02, True),      # Neumond 12.08. 17:37 UTC
    (date(2026, 8, 20), 0.50, True),      # Erstes Viertel 20.08. 02:46
    (date(2026, 8, 28), 0.99, True),      # Vollmond 28.08. 04:18
])
def test_phase_gegen_usno(tag, anteil, zunehmend):
    w = mond.ereignisse(tag, *VILLACH)
    assert w["anteil"] == pytest.approx(anteil, abs=0.08)


def test_fehlender_aufgang_ist_kein_fehler():
    """Der Mond geht taeglich ~50min spaeter auf — mal faellt ein Ereignis aus."""
    ohne = [t for t in (date(2026, 9, 1) + timedelta(days=i) for i in range(31))
            if mond.ereignisse(t, *VILLACH)["aufgang"] is None]
    assert ohne, "in einem Monat muss mindestens ein Tag ohne Aufgang liegen"
    w = mond.ereignisse(ohne[0], *VILLACH)
    assert "--:--" in mond.render(w)


def test_render_kurz_genug_und_lesbar():
    text = mond.render(mond.ereignisse(date(2026, 8, 16), *VILLACH))
    assert len(text) <= 140
    assert text.startswith("Mond:") and "zunehmend" in text


def test_vollmond_heisst_vollmond():
    assert "Vollmond" in mond.render({"aufgang": None, "untergang": None,
                                      "anteil": 0.99, "zunehmend": True})
    assert "Neumond" in mond.render({"aufgang": None, "untergang": None,
                                     "anteil": 0.01, "zunehmend": True})
