"""Summit weather for mountains on both sides of the border.

Two things have to hold: the mountain is found, including under a different
spelling -- and the answer is recognisable as a **model value**. A computed
number that looks like a measurement is worse than none.
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
    """Mountains do not end at the national border -- neither does the directory."""
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
    """Hard truncation would give `Punta Penia – Marmolad` -- not a mountain, a typo."""
    g = h_berg.suche(IDX, frage)
    assert h_berg.render(g, WERTE).startswith(f"WX {name} ")


def test_kurzname_laesst_kurze_namen_in_ruhe():
    assert h_berg.kurzname("Triglav") == "Triglav"
    assert h_berg.kurzname("Ein sehr langer Berg ohne jede Trennung im Namen").endswith("Berg")


def test_index_nimmt_bei_namensgleichheit_den_hoeheren():
    doppelt = [{"ref": "X/AA-001", "name": "Testberg", "alt": 800, "lat": 46.0, "lon": 13.0},
               {"ref": "X/AA-002", "name": "Testberg", "alt": 2000, "lat": 46.1, "lon": 13.1}]
    assert h_berg.suche(h_berg.index(doppelt), "testberg")["alt"] == 2000


# --- Order: certain before guessed ---------------------------------------

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
    """Reported from the channel: `!wx hochstein` returned "Hohenstein St Veit".

    Hochstein is a mountain in East Tyrol, Hohenstein a Carinthian hamlet -- and
    at 0.8 similarity the hamlet used to win, because the place directory was
    asked first. A match that fits exactly must beat one that merely sounds
    similar.
    """
    from meshbot.handlers import wx as h_wx

    b = _bot()
    # Starting point: the similarity search really does hit the hamlet.
    geraten = h_wx.resolve_place("hochstein", b.stations, b.settings.default_location)
    assert geraten is not None and "hohenstein" in geraten[0]
    # Asked exactly, the place directory does not know it ...
    assert h_wx.resolve_place("hochstein", b.stations, b.settings.default_location,
                              fuzzy=False) is None
    # ... but the summit directory does.
    berg = h_berg.suche(IDX, "hochstein", fuzzy=False)
    assert berg is not None and berg["name"] == "Hochstein"


def test_echter_ort_bleibt_beim_ort():
    """Villach is a place and stays one -- measured beats computed."""
    from meshbot.handlers import wx as h_wx

    b = _bot()
    assert h_wx.resolve_place("villach", b.stations, b.settings.default_location,
                              fuzzy=False) is not None


def test_tippfehler_im_ortsnamen_geht_weiterhin():
    """`vilach` may still find Villach -- only on the second attempt now."""
    from meshbot.handlers import wx as h_wx

    b = _bot()
    assert h_wx.resolve_place("vilach", b.stations, b.settings.default_location,
                              fuzzy=False) is None
    treffer = h_wx.resolve_place("vilach", b.stations, b.settings.default_location)
    assert treffer is not None and "villach" in treffer[0]


def test_bei_namensgleichheit_gewinnt_der_hoechste_berg():
    """There are three Hochsteins: 904 m, 2183 m and 2827 m in East Tyrol."""
    assert h_berg.suche(IDX, "hochstein", fuzzy=False)["alt"] == 2827


def test_station_auf_dem_gipfel_schlaegt_das_modell():
    """On the Dobratsch, the station "Villacher Alpe" measures 200 m from the summit."""
    from meshbot.handlers import wx as h_wx

    stationen = h_wx.load_stations(Settings()).get("stationen", [])
    berg = h_berg.suche(IDX, "dobratsch")
    st = h_berg.station_am_gipfel(stationen, berg)
    assert st is not None and st["name"] == "Villacher Alpe"


def test_talstation_gilt_nicht_als_gipfelstation():
    """Proximity alone is not enough -- 1500 m lower is different weather."""
    berg = {"name": "Testgipfel", "alt": 2500, "lat": 46.6, "lon": 13.7, "ref": "X/AA-001"}
    tal = [{"id": "1", "name": "Talstation", "lat": 46.6, "lon": 13.7, "hoehe": 600.0}]
    assert h_berg.station_am_gipfel(tal, berg) is None


def test_ferne_station_auf_gleicher_hoehe_gilt_auch_nicht():
    berg = {"name": "Testgipfel", "alt": 2500, "lat": 46.6, "lon": 13.7, "ref": "X/AA-001"}
    weit = [{"id": "1", "name": "Anderer Berg", "lat": 47.2, "lon": 13.7, "hoehe": 2480.0}]
    assert h_berg.station_am_gipfel(weit, berg) is None


# --- Length: half the battle on the radio network -------------------------

@pytest.mark.parametrize("frage", ["goldeck", "latschur", "marmolada", "triglav",
                                   "matterhorn", "grossglockner", "hochstein"])
def test_gipfelantwort_bleibt_unter_60_zeichen(frage):
    """Measured on 2026-08-26 across twelve hours of radio traffic:

        up to 59 characters   11 of 12 arrived   (92 %)
        from  60 characters    7 of 15 arrived   (47 %)

    At 60 to 62 characters the summit answers sat squarely in the bad class.
    Air pressure was dropped for it -- as `pressure_msl` it was reduced to sea
    level anyway and therefore said nothing about the summit.
    """
    s = Settings()
    g = h_berg.suche(IDX, frage)
    text = prepare(h_berg.render(g, WERTE), s.nutzlimit, s.transliterate)
    assert len(text) <= 59, f"{len(text)} Zeichen: {text}"


def test_kein_luftdruck_in_der_gipfelantwort():
    g = h_berg.suche(IDX, "triglav")
    assert "hPa" not in h_berg.render(g, {**WERTE, "pressure_msl": 1013.0})


# --- Hoehe im Messzweig ----------------------------------------------------

from meshbot.handlers import wx as h_wx  # noqa: E402


def test_gipfel_mit_station_behaelt_die_hoehe():
    """`!gipfel` darf die Hoehe nicht dort verlieren, wo die Antwort am besten ist.

    Steht eine Station praktisch am Gipfel -- hoechstens 3 km entfernt und 300 m
    Hoehenunterschied -- gewinnt die Messung ueber das Modell. Richtig so.
    Gerendert wurde sie aber ueber den Ortsrenderer, und der kennt keine Hoehe.

    Ergebnis: `!gipfel gerlitzen` antwortete `WX Gerlitzen 1909m: …` aus dem
    Modell, `!gipfel dobratsch` dagegen `WX Dobratsch (Villacher Alpe): …` aus
    der Messung. Derselbe Befehl, zwei Formen -- und die Hoehe fehlte
    ausgerechnet dort, wo die Antwort am meisten wert war. Sie ist das Einzige,
    was !gipfel von !wx unterscheidet.
    """
    werte = {"TL": 12.5, "RF": 84, "FFAM": 3.3, "DD": 90, "P": 790}
    mit = h_wx.render("Dobratsch", werte, station="Villacher Alpe", hoehe=2166)
    assert mit.startswith("WX Dobratsch 2166m (Villacher Alpe): ")


def test_orte_bekommen_keine_hoehe():
    """Ein Ort hat keine einzelne Hoehe, die zu nennen sich lohnt."""
    werte = {"TL": 12.5, "RF": 84}
    assert h_wx.render("Villach", werte).startswith("WX Villach: ")


def test_gipfel_mit_hoehe_bleibt_im_zeichenlimit():
    """Sechs Zeichen mehr duerfen die Antwort nicht ueber die Grenze schieben."""
    werte = {"TL": -12.5, "RF": 100, "FFAM": 27.8, "DD": 315, "P": 1013}
    lang = h_wx.render("Hochalmspitze", werte, station="Sonnblick", hoehe=3360)
    assert len(lang) <= 100, f"{len(lang)}: {lang}"
