"""Mountains and places are two directories, not one.

Reported from the channel, three cases of the same disease: `!wx lienz` returned
"Sandegg - Lienzer", `!wx eckwand` returned "Bleckwand", `!wx hamburg` returned
"Haimburg". Every time, a loosening meant to find a mountain bent a place name
out of shape -- or the other way round.

The tests here pin down three promises:

1. **Whole word instead of substring.** What sits mid-word is not a match.
2. **A guess is marked as a guess.** One question mark, one character.
3. **Known gaps are called gaps.** Petzen is missing from the SOTA list;
   replacing it with "Pletzen" is not an answer but a mix-up.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.handlers import sota as h_sota  # noqa: E402
from meshbot.handlers import wx as h_wx  # noqa: E402
from meshbot.handlers import wxberg as h_berg  # noqa: E402

GIPFEL = h_sota.load_summits(Settings().summits_file)
IDX = h_berg.index(GIPFEL)


def _bot(ohne_ortsuche: bool = True):
    """A bot without network.

    `ohne_ortsuche` silences the place lookup by pretending it found nothing --
    exactly the behaviour when the source is down. The tests here check the
    **order** of the stages, not the external source.
    """
    from meshbot.main import Bot
    b = Bot.__new__(Bot)
    b.settings = Settings()
    b.stations = h_wx.load_stations(b.settings)
    b.summits = GIPFEL
    b.gipfel_index = IDX
    b.stale = {}
    for name in ("cache_wx", "cache_berg", "cache_geo"):
        setattr(b, name, {})
    if ohne_ortsuche:
        async def _stumm(arg, nur_exakt):
            return None
        b._wx_fremd = _stumm
    return b


def run(coro):
    return asyncio.run(coro)


# --- 1. Whole word instead of substring ---------------------------------

@pytest.mark.parametrize("frage,nicht", [
    ("eckwand", "Bleckwand"),          # sits mid-word
    ("lienz", "Sandegg - Lienzer"),    # sits at a word start but is not the word
])
def test_teiltreffer_mitten_im_wort_zaehlt_nicht(frage, nicht):
    g, stufe = h_berg.suche_stufe(IDX, frage, fuzzy=False)
    assert g is None, f"{frage} traf {g and g['name']}, erwartet kein sicherer Treffer"
    assert stufe != "exakt"
    # And even with guessing it must not pass through *unmarked*.
    g2, stufe2 = h_berg.suche_stufe(IDX, frage)
    if g2 is not None and g2["name"] == nicht:
        assert stufe2 == "geraten", "Fehltreffer muss wenigstens als geraten gelten"


@pytest.mark.parametrize("frage,erwartet", [
    ("hafner", "Großer Hafner"),        # a whole word inside a double name
    ("sonnblick", "Hoher Sonnblick"),
    ("speikkogel", "Großer Speikkogel"),
])
def test_ganzes_wort_findet_den_doppelnamen(frage, erwartet):
    """The stricter rule must not take the genuine partial hits with it."""
    g, stufe = h_berg.suche_stufe(IDX, frage, fuzzy=False)
    assert g is not None and g["name"] == erwartet
    assert stufe == "wort"


@pytest.mark.parametrize("frage,erwartet", [
    ("koralpe", "Großer Speikkogel"),   # SOTA names the highest point
    ("saualpe", "Ladinger Spitz"),
    ("glockner", "Großglockner"),       # single-word name, word boundary is no help
    ("obir", "Hochobir"),
])
def test_gelaeufiger_name_findet_den_sota_namen(frage, erwartet):
    g, stufe = h_berg.suche_stufe(IDX, frage, fuzzy=False)
    assert g is not None and g["name"] == erwartet
    assert stufe in ("exakt", "wort"), stufe


def test_jeder_alias_zeigt_auf_einen_vorhandenen_gipfel():
    """An alias into the void silently falls back to guessing -- worse than none."""
    for gelaeufig, sota in h_berg.ALIASE.items():
        assert sota in IDX, f"{gelaeufig} -> {sota} steht nicht im Verzeichnis"


# --- 2. A guess is recognisable as a guess ------------------------------

def test_geratener_gipfel_bekommt_ein_fragezeichen():
    werte = {"temperature_2m": 3.0, "relative_humidity_2m": 60,
             "wind_speed_10m": 10.0, "wind_direction_10m": 180}
    berg = h_berg.suche(IDX, "dobratsch")
    sicher = h_berg.render(berg, werte)
    unsicher = h_berg.render(berg, werte, geraten=True)
    assert "?" not in sicher
    assert "?" in unsicher
    # Exactly one character more expensive -- airtime is tight.
    assert len(unsicher) == len(sicher) + 1


def test_geratener_ort_bekommt_ein_fragezeichen():
    werte = {"TL": 21.0, "RF": 60, "FFAM": 3.0, "DD": 180}
    assert "?" not in h_wx.render("villach", werte)
    assert "Villach?" in h_wx.render("villach", werte, geraten=True)


def test_tippfehler_wird_als_geraten_gemeldet():
    """`vilach` still finds Villach -- but says that it guessed."""
    b = _bot()
    treffer = h_wx.resolve_place_stufe("vilach", b.stations, b.settings.default_location)
    assert treffer is not None
    assert treffer[2] == "geraten"
    assert h_wx.resolve_place_stufe("villach", b.stations,
                                    b.settings.default_location)[2] == "exakt"


# --- 3. Real places outside, real gaps inside ---------------------------

@pytest.mark.parametrize("ort", ["hamburg", "wien", "lienz", "muenchen", "ljubljana"])
def test_orte_ausserhalb_werden_nicht_auf_kaernten_geraten(ort):
    """From the channel: `!wx hamburg` returned Haimburg near Voelkermarkt.

    No similarity threshold separates these -- measured, `hamburg -> haimburg`
    scores 0.933 and is therefore higher than the genuine typo
    `vilach -> villach` (0.923). Hence a table.
    """
    b = _bot()
    assert h_wx.resolve_place_stufe(ort, b.stations, b.settings.default_location) is None
    antwort = h_wx.render_unbekannt(ort)
    assert "Kaernten" in antwort


def test_absage_fuer_ausserhalb_passt_ins_nutzlimit():
    b = _bot()
    for ort in sorted(h_wx.AUSSERHALB):
        assert len(h_wx.render_unbekannt(ort)) <= b.settings.nutzlimit, ort


@pytest.mark.parametrize("berg", ["petzen", "kornock", "falkert"])
def test_fehlende_berge_werden_nicht_durch_fremde_ersetzt(berg):
    """Petzen became "Pletzen", Kornock became "Koflernock" -- other mountains."""
    g, stufe = h_berg.suche_stufe(IDX, berg)
    assert g is None, f"{berg} wurde durch {g and g['name']} ersetzt"
    assert stufe == "fehlt"
    assert "SOTA" in h_berg.render_unbekannt(berg)


def test_kein_eintrag_in_fehlt_steht_auch_im_verzeichnis():
    """Otherwise the gap table locks out a mountain that exists."""
    for name in h_berg.FEHLT:
        assert name not in IDX, f"{name} steht im Verzeichnis, gehoert nicht in FEHLT"


# --- The separation itself ----------------------------------------------

def test_wx_bleibt_beim_ort_und_beim_sicheren_berg():
    """Checked without network: resolution only, not the fetch."""
    b = _bot()
    ort = h_wx.resolve_place_stufe("villach", b.stations, b.settings.default_location,
                                   fuzzy=False)
    assert ort is not None and ort[2] == "exakt"
    # Summit exact -- `!wx goldeck` was announced that way and must keep working
    assert h_berg.suche_stufe(IDX, "goldeck", fuzzy=False)[1] == "exakt"


def test_wx_raet_nicht_mehr_ueber_das_gipfelverzeichnis():
    """The second, similarity-based summit search is gone -- it was the culprit."""
    b = _bot()
    for frage in ("eckwand", "lienz"):
        antwort = run(b.cmd_wx(frage, "x"))
        assert "Bleckwand" not in antwort and "Lienzer" not in antwort, antwort


def test_gipfel_ist_ein_eigener_befehl_mit_hilfe_und_usage():
    from meshbot.main import Bot
    from meshbot.router import ALIASES

    b = _bot()
    assert ALIASES["berg"] == "gipfel"
    assert "gipfel" in Bot.HILFE and "gipfel" in Bot.USAGE
    assert any("gipfel" in liste for liste in Bot.GRUPPEN.values())
    # Without an argument comes the usage line, not silence.
    assert run(b.cmd_gipfel("", "x")) == b.usage("gipfel")


def test_summit_bleibt_bei_sota():
    """`!summit` predates !gipfel and still points at !sota."""
    from meshbot.router import ALIASES
    assert ALIASES["summit"] == "sota"


# --- Version ------------------------------------------------------------

def test_version_passt_in_eine_nachricht():
    from meshbot import version

    b = _bot()
    assert len(version.render()) <= b.settings.nutzlimit
    assert version.VERSION in version.render()


def test_version_nennt_nummer_und_aenderung():
    from meshbot import version

    b = _bot()
    antwort = run(b.cmd_version("", "x"))
    assert version.VERSION in antwort
    assert version.KURZ in antwort


def test_verlauf_beginnt_mit_der_aktuellen_version():
    """A version missing from the history cannot be traced during a rollout."""
    from meshbot import version

    assert version.VERLAUF[0][0] == version.VERSION


# --- Place lookup outside Carinthia -------------------------------------
#
# Checked without network: the candidate lists are the real answers of the
# geocoding API, trimmed to the fields the selection needs.

def _k(name, land, lat, lon, pop=0):
    return {"name": name, "country_code": land, "latitude": lat,
            "longitude": lon, "population": pop, "elevation": 100.0}


LIENZ = [_k("Lienz", "AT", 46.8289, 12.76903, 11572),
         _k("Lienz", "CH", 47.27728, 9.51627),
         _k("Lienzing", "DE", 48.0, 11.0)]

HAMBURG = [_k("Hamburg", "DE", 53.55, 9.99, 1845229),
           _k("Hamburg", "US", 42.72, -78.83, 9576),
           _k("Grafton", "US", 43.0, -78.0, 11527)]

PETZEN = [_k("Petzen", "DE", 52.3, 9.1),
          _k("Peca", "AT", 46.45, 14.77),
          _k("Petzenkirchen", "AT", 48.15, 15.11, 1311)]

VILACH = [_k("Vilachá", "ES", 42.5, -7.3, 5)]


def test_exakter_name_schlaegt_die_groessere_einwohnerzahl():
    """Chosen by population alone, "Petzen" is won by Petzenkirchen."""
    from meshbot.handlers import ortsuche as h_ort
    treffer = h_ort.waehle(PETZEN, "petzen")
    assert treffer["name"] != "Petzenkirchen"


def test_naehe_entscheidet_bei_gleichem_namen():
    """"Peca" is a mountain in the Karawanks and a place in Indonesia."""
    from meshbot.handlers import ortsuche as h_ort
    assert h_ort.waehle(LIENZ, "lienz")["country_code"] == "AT"
    peca = [_k("Peca", "ID", -6.9, 107.6), _k("Peca", "AT", 46.45, 14.77)]
    assert h_ort.waehle(peca, "peca")["country_code"] == "AT"


def test_einwohnerzahl_entscheidet_wenn_nichts_in_der_naehe_liegt():
    from meshbot.handlers import ortsuche as h_ort
    treffer = h_ort.waehle(HAMBURG, "hamburg")
    assert treffer["country_code"] == "DE" and treffer["population"] > 1_000_000


def test_ortsalias_findet_den_berg_statt_des_dorfes():
    """The Petzen is listed in the gazetteer as "Peca"."""
    from meshbot.handlers import ortsuche as h_ort
    assert h_ort.waehle(PETZEN, "petzen")["name"] == "Peca"


def test_nur_exakt_laesst_den_tippfehler_durch_zur_kaerntensuche():
    """Otherwise `vilach` becomes the Spanish Vilachá, population five.

    The exact stage must return **nothing** here so that `cmd_wx` can then ask
    the Carinthian place directory with typo tolerance.
    """
    from meshbot.handlers import ortsuche as h_ort
    assert h_ort.waehle(VILACH, "vilach", nur_exakt=True) is None
    # As the last stage the same hit may come back -- marked, though.
    assert h_ort.waehle(VILACH, "vilach", nur_exakt=False) is not None


def test_antwort_nennt_land_und_ist_als_modell_erkennbar():
    from meshbot.handlers import ortsuche as h_ort
    w = {"temperature_2m": 18.0, "relative_humidity_2m": 83,
         "wind_speed_10m": 11.0, "wind_direction_10m": 135}
    text = h_ort.render(HAMBURG[0], w)
    assert text.startswith("WX Hamburg (DE):")
    assert text.endswith("(Modell)")
    assert len(text) <= Settings().nutzlimit


# --- Umlauts -----------------------------------------------------------

def test_ortsnamen_werden_geschrieben_wie_der_ort_heisst():
    """The key stays umlaut-free, the answer does not.

    Lookup runs via "noetsch" so that whoever types no umlaut still finds it --
    what goes on the air is "Nötsch". The lookup key used to double as the
    display name, and the place was misspelled in every answer.
    """
    b = _bot()
    orte = b.stations["orte"]
    for schluessel, erwartet in [("noetsch", "Nötsch"), ("baerental", "Bärental"),
                                 ("voelkermarkt", "Völkermarkt")]:
        assert orte[schluessel].get("anzeige") == erwartet, schluessel


def test_umlaut_und_umschrift_finden_denselben_ort():
    b = _bot()
    mit = h_wx.resolve_place_stufe("nötsch", b.stations, b.settings.default_location)
    ohne = h_wx.resolve_place_stufe("noetsch", b.stations, b.settings.default_location)
    assert mit is not None and ohne is not None
    assert mit[0] == ohne[0]


def test_antwort_zeigt_den_anzeigenamen():
    werte = {"TL": 20.4, "RF": 82, "FFAM": 2.2, "DD": 90}
    text = h_wx.render("noetsch", werte, station="Bad Bleiberg", anzeige="Nötsch")
    assert text.startswith("WX Nötsch (Bad Bleiberg):")
    # Without a display name the key is used -- old files keep working.
    assert h_wx.render("noetsch", werte).startswith("WX Noetsch:")


def test_umlaute_werden_nicht_mehr_umgeschrieben():
    """The default and the shipped .env have to agree."""
    from meshbot.formatting import prepare
    s = Settings()
    assert s.transliterate is False
    assert "Großglockner" in prepare("WX Großglockner 3798m", s.nutzlimit, s.transliterate)


def test_sota_verbandskuerzel_bleiben_ascii():
    """"OE" is an association code, not a transliterated "Ö"."""
    from meshbot.main import Bot
    assert Settings().sota_default_assoc.startswith("OE")
    # The summit refs themselves: OE/KT-077, not Ö/KT-077.
    assert all(g["ref"][0] != "Ö" for g in GIPFEL)


def test_ohne_ortsuche_bleibt_die_kaerntensuche_erhalten():
    """If the external source fails, a Carinthian typo must not fail with it."""
    b = _bot(ohne_ortsuche=True)
    treffer = h_wx.resolve_place_stufe("vilach", b.stations, b.settings.default_location)
    assert treffer is not None and treffer[2] == "geraten"
