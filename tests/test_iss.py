"""Ueberflugrechnung. Gegenprobe rein geometrisch, ohne zweite Bibliothek."""

import asyncio
import math
from datetime import datetime, timezone

import httpx
import pytest

from meshbot.handlers import geo, iss

# Echter Datensatz vom 16.08.2026, damit der Test nicht ans Netz muss.
TLE = ("1 25544U 98067A   26228.18012382  .00004999  00000+0  97292-4 0  9998",
       "2 25544  51.6332   3.1747 0007602  51.3505 308.8163 15.49457398581051")
VILLACH = (46.6167, 13.85)
START = datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_tle_aus_dokument_mit_kopfzeile():
    text = "ISS (ZARYA)\n" + "\n".join(TLE) + "\n"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text)))
    assert run(iss.fetch_tle(client, "https://x")) == TLE


def test_dokument_ohne_tle_wirft():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="nix")))
    with pytest.raises(ValueError):
        run(iss.fetch_tle(client, "https://x"))


def test_ueberflug_ist_plausibel():
    p = iss.naechster_ueberflug(TLE, *VILLACH, START)
    assert p is not None
    assert p["max_el"] >= iss.MIN_ELEVATION
    assert 0.5 < p["dauer_min"] < 12          # laenger kann ein ISS-Pass nicht sein
    assert p["start"] <= p["hoehepunkt"]


def test_elevation_stimmt_mit_der_geometrie_ueberein():
    """Gegenprobe: Bodenabstand und Bahnhoehe muessen dieselbe Elevation ergeben."""
    from sgp4.api import Satrec
    p = iss.naechster_ueberflug(TLE, *VILLACH, START)
    sat = Satrec.twoline2rv(*TLE)
    jd = p["hoehepunkt"].timestamp() / 86400 + 2440587.5
    _, r, _ = sat.sgp4(math.floor(jd) + 0.5, jd - (math.floor(jd) + 0.5))
    g = iss._gmst(jd)
    x = r[0] * math.cos(g) + r[1] * math.sin(g)
    y = -r[0] * math.sin(g) + r[1] * math.cos(g)
    lat = math.degrees(math.atan2(r[2], math.hypot(x, y)))
    lon = math.degrees(math.atan2(y, x))
    hoehe = math.hypot(math.hypot(x, y), r[2]) - 6371
    assert 380 < hoehe < 460                  # ISS-Bahnhoehe
    d = geo.distanz_km(VILLACH, (lat, lon)) / 6371
    erwartet = math.degrees(math.atan2(math.cos(d) - 6371 / (6371 + hoehe), math.sin(d)))
    assert p["max_el"] == pytest.approx(erwartet, abs=1.5)


def test_kein_ueberflug_am_suedpol():
    """Die ISS kommt nie ueber 51,6 Grad Breite — am Pol kann nichts kommen."""
    assert iss.naechster_ueberflug(TLE, -89.0, 0.0, START, stunden=6) is None


def test_render_kurz_und_mit_richtungen():
    text = iss.render(iss.naechster_ueberflug(TLE, *VILLACH, START))
    assert len(text) <= 140
    assert text.startswith("ISS") and ">" in text and "min" in text


def test_kein_ueberflug_wird_gesagt():
    assert "kein Ueberflug" in iss.render(None)


def test_alte_bahndaten_werden_markiert():
    p = iss.naechster_ueberflug(TLE, *VILLACH, START)
    assert "~" in iss.render(p, alt=True)
    assert "~" not in iss.render(p, alt=False)
