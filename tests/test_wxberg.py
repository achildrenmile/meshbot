"""Gipfelwetter fuer Berge diesseits und jenseits der Grenze.

Zwei Dinge muessen stimmen: Der Berg wird gefunden, auch mit anderer
Schreibweise -- und die Antwort ist als **Modellwert** erkennbar. Eine
gerechnete Zahl, die aussieht wie eine Messung, ist schlimmer als keine.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.formatting import prepare  # noqa: E402
from meshbot.handlers import sota as h_sota  # noqa: E402
from meshbot.handlers import wxberg as h_berg  # noqa: E402

GIPFEL = h_sota.load_summits(Settings().summits_file)
IDX = h_berg.index(GIPFEL)

WERTE = {"temperature_2m": -3.4, "relative_humidity_2m": 77,
         "wind_speed_10m": 42.0, "wind_direction_10m": 231, "pressure_msl": 1013.0}


@pytest.mark.parametrize("frage,erwartet", [
    ("triglav", "S5"),            # Slowenien
    ("zugspitze", "DL"),          # Deutschland
    ("matterhorn", "HB"),         # Schweiz
    ("marmolada", "I"),           # Italien
    ("grossglockner", "OE"),      # Oesterreich, ss statt ß
    ("Großglockner", "OE"),
    ("dobratsch", "OE"),          # Klammername
    ("villacher alpe", "OE"),     # derselbe Berg, anderer Name
    ("mangart", "S5"),
])
def test_gipfel_werden_ueber_die_grenze_gefunden(frage, erwartet):
    g = h_berg.suche(IDX, frage)
    assert g is not None, frage
    assert g["ref"].split("/")[0] == erwartet, g


def test_mindestens_zehn_verbaende_im_verzeichnis():
    """Berge enden nicht an der Staatsgrenze -- das Verzeichnis auch nicht."""
    verbaende = {g["ref"].split("/")[0] for g in GIPFEL}
    for a in ("OE", "I", "S5", "DL", "HB"):
        assert a in verbaende, f"{a} fehlt"
    assert len(GIPFEL) > 5000


def test_unbekannter_berg_wird_nicht_geraten():
    assert h_berg.suche(IDX, "hintertupfing") is None
    assert h_berg.suche(IDX, "") is None


def test_antwort_ist_als_modell_gekennzeichnet():
    g = h_berg.suche(IDX, "triglav")
    text = h_berg.render(g, WERTE)
    assert "(Modell)" in text, text
    assert "2864m" in text


def test_antwort_nennt_hoehe_temperatur_wind():
    g = h_berg.suche(IDX, "zugspitze")
    text = h_berg.render(g, WERTE)
    assert "-3.4C" in text and "77%" in text and "42km/h" in text and "SW" in text


def test_stale_wird_markiert():
    g = h_berg.suche(IDX, "triglav")
    assert "~" in h_berg.render(g, WERTE, stale=True)


@pytest.mark.parametrize("frage", ["triglav", "matterhorn", "marmolada", "zugspitze",
                                   "dobratsch", "mangart", "visoki kanin"])
def test_antwort_passt_in_eine_nachricht(frage):
    s = Settings()
    g = h_berg.suche(IDX, frage)
    text = prepare(h_berg.render(g, WERTE), s.nutzlimit, s.transliterate)
    assert len(text) <= s.nutzlimit, text
    assert "…" not in text, text


@pytest.mark.parametrize("frage,name", [
    ("matterhorn", "Matterhorn"),           # Matterhorn/Mont Cervin/Monte Cervino
    ("marmolada", "Marmolada"),             # Punta Penia – Marmolada
    ("dobratsch", "Dobratsch"),             # Villacher Alpe (Dobratsch)
])
def test_langer_doppelname_wird_auf_den_bergnamen_gekuerzt(frage, name):
    """Hart abschneiden ergaebe `Punta Penia – Marmolad` -- kein Berg, ein Tippfehler."""
    g = h_berg.suche(IDX, frage)
    assert h_berg.render(g, WERTE).startswith(f"WX {name} ")


def test_kurzname_laesst_kurze_namen_in_ruhe():
    assert h_berg.kurzname("Triglav") == "Triglav"
    assert h_berg.kurzname("Ein sehr langer Berg ohne jede Trennung im Namen").endswith("Berg")


def test_index_nimmt_bei_namensgleichheit_den_hoeheren():
    doppelt = [{"ref": "X/AA-001", "name": "Testberg", "alt": 800, "lat": 46.0, "lon": 13.0},
               {"ref": "X/AA-002", "name": "Testberg", "alt": 2000, "lat": 46.1, "lon": 13.1}]
    assert h_berg.suche(h_berg.index(doppelt), "testberg")["alt"] == 2000


# --- Reihenfolge: Sicheres vor Geratenem ---------------------------------

def _bot():
    from meshbot.handlers import wx as h_wx
    from meshbot.main import Bot
    b = Bot.__new__(Bot)
    b.settings = Settings()
    b.stations = h_wx.load_stations(b.settings)
    b.summits = GIPFEL
    b.gipfel_index = IDX
    return b


def test_exakter_berg_schlaegt_geratenen_ort():
    """Aus dem Kanal gemeldet: `!wx hochstein` lieferte "Hohenstein St Veit".

    Hochstein ist ein Berg in Osttirol, Hohenstein ein Kaerntner Weiler --
    und auf 0.8 Aehnlichkeit gewann frueher der Weiler, weil das
    Ortsverzeichnis zuerst gefragt wurde. Ein Treffer, der genau passt, muss
    vor einem gewinnen, der nur aehnlich klingt.
    """
    from meshbot.handlers import wx as h_wx

    b = _bot()
    # Ausgangslage: die Aehnlichkeitssuche trifft wirklich den Weiler.
    geraten = h_wx.resolve_place("hochstein", b.stations, b.settings.default_location)
    assert geraten is not None and "hohenstein" in geraten[0]
    # Exakt gefragt kennt ihn das Ortsverzeichnis nicht ...
    assert h_wx.resolve_place("hochstein", b.stations, b.settings.default_location,
                              fuzzy=False) is None
    # ... das Gipfelverzeichnis schon.
    berg = h_berg.suche(IDX, "hochstein", fuzzy=False)
    assert berg is not None and berg["name"] == "Hochstein"


def test_echter_ort_bleibt_beim_ort():
    """Villach ist ein Ort und bleibt einer -- gemessen schlaegt gerechnet."""
    from meshbot.handlers import wx as h_wx

    b = _bot()
    assert h_wx.resolve_place("villach", b.stations, b.settings.default_location,
                              fuzzy=False) is not None


def test_tippfehler_im_ortsnamen_geht_weiterhin():
    """`vilach` darf weiterhin Villach finden -- nur eben erst im zweiten Anlauf."""
    from meshbot.handlers import wx as h_wx

    b = _bot()
    assert h_wx.resolve_place("vilach", b.stations, b.settings.default_location,
                              fuzzy=False) is None
    treffer = h_wx.resolve_place("vilach", b.stations, b.settings.default_location)
    assert treffer is not None and "villach" in treffer[0]


def test_bei_namensgleichheit_gewinnt_der_hoechste_berg():
    """Hochstein gibt es dreimal: 904 m, 2183 m und 2827 m in Osttirol."""
    assert h_berg.suche(IDX, "hochstein", fuzzy=False)["alt"] == 2827


def test_station_auf_dem_gipfel_schlaegt_das_modell():
    """Auf dem Dobratsch misst die Station "Villacher Alpe", 200 m vom Gipfel."""
    from meshbot.handlers import wx as h_wx

    stationen = h_wx.load_stations(Settings()).get("stationen", [])
    berg = h_berg.suche(IDX, "dobratsch")
    st = h_berg.station_am_gipfel(stationen, berg)
    assert st is not None and st["name"] == "Villacher Alpe"


def test_talstation_gilt_nicht_als_gipfelstation():
    """Naehe allein genuegt nicht -- 1500 m tiefer ist anderes Wetter."""
    berg = {"name": "Testgipfel", "alt": 2500, "lat": 46.6, "lon": 13.7, "ref": "X/AA-001"}
    tal = [{"id": "1", "name": "Talstation", "lat": 46.6, "lon": 13.7, "hoehe": 600.0}]
    assert h_berg.station_am_gipfel(tal, berg) is None


def test_ferne_station_auf_gleicher_hoehe_gilt_auch_nicht():
    berg = {"name": "Testgipfel", "alt": 2500, "lat": 46.6, "lon": 13.7, "ref": "X/AA-001"}
    weit = [{"id": "1", "name": "Anderer Berg", "lat": 47.2, "lon": 13.7, "hoehe": 2480.0}]
    assert h_berg.station_am_gipfel(weit, berg) is None
