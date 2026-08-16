"""Tests. Schwerpunkt: Der Bot darf nie zu viel senden und nie zu lang.

Die harte Eigenschaft, die alles andere trägt: **keine Antwort überschreitet
MAX_MSG_LEN** — geprüft über alle Handler hinweg mit langen Eingaben.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.formatting import clamp, prepare, transliterate  # noqa: E402
from meshbot.handlers import relais as h_relais  # noqa: E402
from meshbot.handlers import sota as h_sota  # noqa: E402
from meshbot.handlers import uwz as h_uwz  # noqa: E402
from meshbot.handlers import wx as h_wx  # noqa: E402
from meshbot.ratelimit import Deduplicator, SenderLimiter, TokenBucket  # noqa: E402
from meshbot.router import Router, dig, parse_command, parse_payload, split_sender_prefix  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return settings_echt()


def payload(text: str, sender: str = "OE8TEST", channel: str = "3") -> str:
    """Nutzlast im Format der Bruecke: Ereignis aussen, Nutztext eine Ebene tiefer,
    Absendername als Praefix im Text — so kommt es tatsaechlich an."""
    return json.dumps({
        "type": "EventType.CHANNEL_MSG_RECV",
        "payload": {"type": "CHAN", "SNR": 11.5, "channel_idx": int(channel),
                    "text": f"{sender}: {text}"},
    })


def settings_echt() -> Settings:
    return Settings(mqtt_host="test", bot_name="MeshBot",
                    json_path_text="payload.text", json_path_sender="payload.sender",
                    json_path_channel="payload.channel_idx")


# --- Formatierung ---------------------------------------------------------

def test_clamp_haelt_grenze_ein():
    for laenge in (10, 40, 140):
        assert len(clamp("Wort " * 100, laenge)) <= laenge


def test_clamp_schneidet_an_wortgrenze():
    assert clamp("Villach Klagenfurt Spittal", 16).endswith("…")


def test_transliteration():
    assert transliterate("Nötsch Grüße Kärnten") == "Noetsch Gruesse Kaernten"


def test_prepare_kombiniert_beides():
    text = prepare("Kärnten " * 50, 40, True)
    assert len(text) <= 40 and "ä" not in text


# --- Befehlserkennung -----------------------------------------------------

@pytest.mark.parametrize("text,erwartet", [
    ("!wx villach", ("wx", "villach")),
    ("!WETTER Klagenfurt", ("wx", "Klagenfurt")),
    ("!uwz", ("uwz", "")),
    ("!warn", ("uwz", "")),
    ("!sota oe/kt-048", ("sota", "oe/kt-048")),
    ("!rpt 2m villach", ("relais", "2m villach")),
    ("!hilfe", ("help", "")),
])
def test_parse_command(text, erwartet):
    assert parse_command(text) == erwartet


@pytest.mark.parametrize("text", ["wx villach", "!unbekannt", "", "!", "hallo leute"])
def test_parse_command_ignoriert_alles_andere(text):
    assert parse_command(text) is None


def test_parse_payload_echte_bruecke(settings):
    """Genau die Nutzlast, die meshcore-mqtt auf message/channel/<n> legt."""
    roh = ('{"type": "EventType.CHANNEL_MSG_RECV", "payload": {"type": "CHAN", '
           '"SNR": 11.5, "channel_idx": 3, "path_len": 64, "txt_type": 0, '
           '"sender_timestamp": 1786889382, "text": "AT-achildrenmile: !help"}}')
    e = parse_payload(roh, settings)
    assert e is not None
    assert e.text == "!help"                 # Praefix entfernt
    assert e.sender == "AT-achildrenmile"    # Absender aus dem Praefix
    assert e.channel == "3"


def test_dig_holt_verschachtelt():
    assert dig({"payload": {"text": "hallo"}}, "payload.text") == "hallo"
    assert dig({"payload": {}}, "payload.text") is None
    assert dig({"text": "flach"}, "text") == "flach"


@pytest.mark.parametrize("roh,name,rest", [
    ("AT-Node: !help", "AT-Node", "!help"),
    ("OE8YML: !wx villach", "OE8YML", "!wx villach"),
    ("!ping", None, "!ping"),                       # ohne Praefix
    ("http://x: !ping", None, "http://x: !ping"),   # kein Name
])
def test_split_sender_prefix(roh, name, rest):
    assert split_sender_prefix(roh) == (name, rest)


def test_parse_payload_kaputt(settings):
    assert parse_payload("kein json", settings) is None


# --- Bremsen --------------------------------------------------------------

def test_tokenbucket_begrenzt():
    b = TokenBucket(limit=2, window=600)
    assert b.allow() and b.allow()
    assert not b.allow()


def test_senderlimit_trennt_absender():
    lim = SenderLimiter(limit=1, window=300)
    assert lim.allow("A") and not lim.allow("A")
    assert lim.allow("B")


def test_dedup_erkennt_wiederholung():
    d = Deduplicator(window=60)
    assert not d.is_duplicate("A", "!wx villach")
    assert d.is_duplicate("A", "!WX  Villach")     # Gross/Klein und Leerzeichen egal
    assert not d.is_duplicate("B", "!wx villach")


# --- Router --------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def router(settings, antwort="Testantwort") -> Router:
    async def handler(arg: str, sender: str) -> str:
        return antwort
    return Router(settings, {"ping": handler, "wx": handler, "help": handler})


def test_router_antwortet_auf_befehl(settings):
    assert run(router(settings).handle(payload("!ping"))) == "Testantwort"


def test_router_schweigt_bei_unbekanntem_befehl(settings):
    assert run(router(settings).handle(payload("!gibtsnicht"))) is None


def test_router_schweigt_bei_eigener_nachricht(settings):
    assert run(router(settings).handle(payload("!ping", sender="MeshBot"))) is None


def test_router_schweigt_bei_duplikat(settings):
    r = router(settings)
    assert run(r.handle(payload("!ping"))) == "Testantwort"
    assert run(r.handle(payload("!ping"))) is None


def test_router_haelt_globales_limit_ein(settings):
    s = settings.model_copy(update={"global_limit": 2, "dedup_window_s": 0, "sender_limit": 99})
    r = router(s)
    antworten = [run(r.handle(payload(f"!ping {i}"))) for i in range(5)]
    assert sum(1 for a in antworten if a) == 2


def test_router_kill_switch(settings):
    r = router(settings)
    r.enabled = False
    assert run(r.handle(payload("!ping"))) is None


def test_router_channel_filter(settings):
    s = settings.model_copy(update={"channel_filter": "3"})
    r = router(s)
    assert run(r.handle(payload("!ping", channel="9"))) is None


def test_router_kuerzt_lange_antwort(settings):
    r = router(settings, antwort="A" * 500)
    antwort = run(r.handle(payload("!ping")))
    assert antwort is not None and len(antwort) <= settings.max_msg_len


def test_router_ueberlebt_handler_fehler(settings):
    async def kaputt(arg: str, sender: str) -> str:
        raise RuntimeError("Quelle weg")
    r = Router(settings, {"ping": kaputt})
    assert run(r.handle(payload("!ping"))) is None


# --- Handler: Ausgabeformat ----------------------------------------------

def test_wx_render():
    text = h_wx.render("villach", {"TL": 4.2, "RF": 78, "FFAM": 3.3, "DD": 270, "P": 1013})
    assert text.startswith("WX Villach:") and "4.2C" in text and "W" in text
    assert len(text) <= 140


def test_wx_render_stale_markiert():
    assert "~" in h_wx.render("villach", {"TL": 1.0}, stale=True)


def test_wx_ortsaufloesung_toleriert_tippfehler():
    stations = {"villach": {"station_id": "1", "lat": 46.6, "lon": 13.8}}
    treffer = h_wx.resolve_place("vilach", stations, "villach")
    assert treffer is not None and treffer[0] == "villach"


def test_uwz_leer():
    assert h_uwz.render([]) == "UWZ KTN: keine Warnungen aktiv"


def test_uwz_sortiert_nach_stufe_und_kuerzt():
    warnungen = [
        {"stufe": 1, "typ": 2, "ende": "16.08.2026 18:00", "gebiete": ["Gailtal"]},
        {"stufe": 2, "typ": 1, "ende": "16.08.2026 20:00", "gebiete": ["Oberkaernten"]},
        {"stufe": 1, "typ": 5, "ende": "", "gebiete": ["Lavanttal"]},
        {"stufe": 1, "typ": 6, "ende": "", "gebiete": ["Zentralraum"]},
    ]
    text = h_uwz.render(warnungen)
    assert text.index("ORANGE") < text.index("GELB")
    assert "+2 weitere" in text          # nur zwei passen in eine Nachricht
    assert len(text) <= 140


@pytest.mark.parametrize("eingabe,erwartet", [
    ("oe/kt-048", "OE/KT-048"),
    ("KT-048", "OE/KT-048"),
    ("kt48", "OE/KT-048"),
    ("OE/KT-8", "OE/KT-008"),
])
def test_sota_normalisierung(eingabe, erwartet):
    assert h_sota.normalise(eingabe, "OE/KT") == erwartet


def test_sota_ungueltig():
    assert h_sota.normalise("völliger unsinn", "OE/KT") is None


@pytest.mark.parametrize("roh,erwartet", [
    ("46.60 13.67", (46.60, 13.67)),
    ("46.6031, 13.6712", (46.6031, 13.6712)),
    ("46,6031, 13,6712", (46.6031, 13.6712)),        # deutsches Dezimalkomma
    ("47.0744  12.6942", (47.0744, 12.6942)),
    ("geo:46.6031,13.6712", (46.6031, 13.6712)),
    ("https://maps.google.com/?q=46.6031,13.6712&z=15", (46.6031, 13.6712)),
    ("Position: 46.6031 / 13.6712", (46.6031, 13.6712)),
    ("\U0001F4CD 46.6031 13.6712", (46.6031, 13.6712)),
])
def test_sota_koordinaten_erkennen(roh, erwartet):
    assert h_sota.parse_coords(roh) == erwartet


@pytest.mark.parametrize("roh", ["kt-048", "OE/KT-048", "", "999.9 13.6", "46.6031", "villach"])
def test_sota_keine_koordinaten(roh):
    assert h_sota.parse_coords(roh) is None


def test_sota_naechster_gipfel_mit_richtung():
    gipfel = [
        {"ref": "OE/KT-072", "name": "Dobratsch", "alt": 2166, "pts": 8, "akt": 90, "lat": 46.6031, "lon": 13.6712},
        {"ref": "OE/KT-001", "name": "Grossglockner", "alt": 3798, "pts": 10, "akt": 40, "lat": 47.0744, "lon": 12.6942},
    ]
    treffer = h_sota.nearest(gipfel, 46.6035, 13.6700, limit=2)
    assert treffer[0]["ref"] == "OE/KT-072"          # der Glockner liegt weiter als 25 km
    assert len(treffer) == 1
    text = h_sota.render_nearest(treffer)
    assert "OE/KT-072" in text and "m " in text and len(text) <= 140


def test_sota_ohne_gipfel_in_reichweite():
    assert h_sota.render_nearest([]) == "SOTA: kein Gipfel in 25km"


def test_echte_gipfeldaten_ladbar():
    """Der lokale Bestand traegt !sota auch ohne Internet."""
    s = Settings(mqtt_host="test")
    gipfel = h_sota.load_summits(s.summits_file)
    assert len(gipfel) > 1000
    treffer = h_sota.nearest(gipfel, 46.6031, 13.6712, limit=1)
    assert treffer and treffer[0]["ref"] == "OE/KT-072"   # Villacher Alpe


def test_sota_render_nicht_gefunden():
    assert h_sota.render("OE/KT-999", None) == "SOTA: OE/KT-999 nicht gefunden"


def test_relais_suche_nach_distanz():
    daten = [
        {"call": "OE8XKK", "ort": "Dobratsch", "band": "2m", "tx": 145.6875, "shift": -600, "lat": 46.60, "lon": 13.67},
        {"call": "OE1XXX", "ort": "Wien", "band": "2m", "tx": 145.7, "shift": -600, "lat": 48.2, "lon": 16.3},
    ]
    treffer = h_relais.suche(daten, "2m", 46.61, 13.85, limit=2)
    assert treffer[0]["call"] == "OE8XKK"
    text = h_relais.render("2m", "villach", treffer)
    assert "OE8XKK" in text and len(text) <= 150


def test_echte_relaisdaten_ladbar():
    """Die mitgelieferte Datei muss brauchbar sein, sonst faellt der Befehl aus."""
    s = Settings(mqtt_host="test")
    daten = h_relais.load_relais(s.relais_file)
    assert len(daten) > 50
    assert all({"call", "band", "lat", "lon"} <= set(r) for r in daten[:20])
    oe8 = [r for r in daten if r["call"].startswith("OE8")]
    assert oe8, "keine Kaerntner Relais im Datenbestand"


def test_echte_stationsdaten_ladbar():
    s = Settings(mqtt_host="test")
    stationen = h_wx.load_stations(s)
    assert "villach" in stationen and "klagenfurt" in stationen
    assert all("station_id" in v for v in stationen.values())


# --- Eigenschaft ueber alles ---------------------------------------------

@pytest.mark.parametrize("roh", [
    "WX " + "Sehr langer Ortsname " * 20,
    "UWZ KTN: " + "ORANGE Sturm (Oberkaernten bis 18h), " * 10,
    "2m b. Villach: " + "OE8XKK Dobratsch 145.6875 -0.6 (12km) | " * 8,
    "SOTA OE/KT-048 " + "Name " * 40,
])
def test_keine_antwort_ueberschreitet_das_limit(roh):
    assert len(prepare(roh, 140, True)) <= 140
