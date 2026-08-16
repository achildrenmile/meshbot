"""Geometrie, Gelaendeprofil und die beiden kleinen Befehle darauf."""

import math

import pytest

from meshbot.handlers import geo

DOBRATSCH = (46.603101, 13.671223)
GERLITZEN = (46.67191, 13.89025)


def test_koordinatenpaare_aus_beliebigem_text():
    assert geo.parse_punkte("46.6,13.8 46.7,13.9") == [(46.6, 13.8), (46.7, 13.9)]
    assert geo.parse_punkte("von 46,6 13,8 nach 46,7 13,9") == [(46.6, 13.8), (46.7, 13.9)]
    assert geo.parse_punkte("46.67191,13.89025", 1) == [(46.67191, 13.89025)]


def test_zu_wenige_zahlen_ist_kein_treffer():
    assert geo.parse_punkte("46.6,13.8") is None
    assert geo.parse_punkte("kein wert hier") is None
    assert geo.parse_punkte("46 13", 1) is None          # ohne Dezimalstelle kein Koordinat


def test_unsinnige_koordinaten_abgelehnt():
    assert geo.parse_punkte("99.5,13.8", 1) is None
    assert geo.parse_punkte("46.6,200.5", 1) is None


def test_distanz_gegen_bekannten_wert():
    """Dobratsch–Gerlitzen sind 18,4 km, nachgerechnet an der Karte."""
    assert geo.distanz_km(DOBRATSCH, GERLITZEN) == pytest.approx(18.4, abs=0.2)
    assert geo.distanz_km(DOBRATSCH, DOBRATSCH) == pytest.approx(0.0)


def test_peilung_und_gegenpeilung():
    p = geo.peilung(DOBRATSCH, GERLITZEN)
    assert 55 < p < 75                                    # Gerlitzen liegt ONO
    zurueck = geo.peilung(GERLITZEN, DOBRATSCH)
    assert abs((p - zurueck) % 360 - 180) < 1


def test_himmelsrichtungen():
    assert geo.richtung(0) == "N"
    assert geo.richtung(90) == "O"
    assert geo.richtung(180) == "S"
    assert geo.richtung(359) == "N"
    assert geo.richtung(45) == "NO"


def test_zwischenpunkt_liegt_auf_der_strecke():
    mitte = geo.zwischenpunkt(DOBRATSCH, GERLITZEN, 0.5)
    a = geo.distanz_km(DOBRATSCH, mitte)
    b = geo.distanz_km(mitte, GERLITZEN)
    assert a == pytest.approx(b, abs=0.01)
    assert geo.zwischenpunkt(DOBRATSCH, GERLITZEN, 0.0) == pytest.approx(DOBRATSCH, abs=1e-9)


def test_fresnel_und_kruemmung_wachsen_mit_der_strecke():
    assert geo.fresnel_radius_m(5, 5, 10) > geo.fresnel_radius_m(1, 9, 10)
    assert geo.erdkruemmung_m(10, 10) > geo.erdkruemmung_m(2, 18)


def _profil(mitte_hoehe: float, n: int = 85) -> list[float]:
    """Flaches Tal mit einem Hügel in der Mitte."""
    hoehen = [500.0] * n
    hoehen[n // 2] = mitte_hoehe
    return hoehen


def test_freie_strecke_wird_als_frei_erkannt():
    """Zwei 80-m-Masten ueber flachem Gelaende, 20 km."""
    eng = geo.bewerte_profil(_profil(500), 20.0, 80, 80)
    assert eng["anteil"] >= 0.6
    assert "FREI" in geo.render_sicht(eng)


def test_zu_niedrige_masten_sind_nur_knapp():
    """30 m auf 20 km reichen nicht: Erdkruemmung und Fresnelzone fressen es auf.

    Genau der Fall, den Leute unterschaetzen -- freie Sicht heisst nicht freie
    Funkstrecke.
    """
    eng = geo.bewerte_profil(_profil(500), 20.0, 30, 30)
    assert 0 < eng["anteil"] < 0.6
    assert "KNAPP" in geo.render_sicht(eng)


def test_berg_in_der_mitte_blockiert():
    eng = geo.bewerte_profil(_profil(900), 20.0, 3, 3)
    text = geo.render_sicht(eng)
    assert eng["anteil"] < 0
    assert "BLOCKIERT" in text and "km10.0" in text


def test_knapper_streifschuss_heisst_knapp():
    """Geometrisch frei, aber die Fresnelzone ist zugebaut."""
    hoehen = _profil(500)
    eng_frei = geo.bewerte_profil(hoehen, 20.0, 60, 60)
    hoehe_grenzwertig = 500 + eng_frei["frei_m"] - 0.3 * eng_frei["radius"]
    eng = geo.bewerte_profil(_profil(hoehe_grenzwertig), 20.0, 60, 60)
    assert 0 < eng["anteil"] < 0.6
    assert "KNAPP" in geo.render_sicht(eng)


def test_nahbereich_wird_ausgeklammert():
    """Ein Buckel 100 m vor der Antenne ist Aufstellungssache, kein Streckenproblem."""
    n = 85
    hoehen = [500.0] * n
    hoehen[1] = 5000.0                       # direkt neben dem Standort
    eng = geo.bewerte_profil(hoehen, 20.0, 30, 30)
    assert eng["anteil"] > 0                 # nicht als blockiert gemeldet
    assert eng["km"] > 0.9


def test_prozent_wird_bei_100_gedeckelt():
    eng = geo.bewerte_profil(_profil(500), 20.0, 500, 500)
    assert "Fresnel 100%" in geo.render_sicht(eng)


def test_render_dist_nennt_beide_richtungen():
    text = geo.render_dist(DOBRATSCH, GERLITZEN)
    assert "18.4km" in text and "zurueck" in text


def test_render_hoehe_nennt_quelle():
    assert "1478m" in geo.render_hoehe(GERLITZEN, 1478.3)
    assert "EU-DEM" in geo.render_hoehe(GERLITZEN, 1478.3)
