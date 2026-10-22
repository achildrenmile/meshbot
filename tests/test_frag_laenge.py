"""!frag length: bytes on the air, re-asking before trimming, the asker's own limit.

The regression behind these tests (#at-ktn-bot, 22 Oct): "!frag Witz 90 Zeichen"
came back as "KI: Ein Funker fragt den anderen: 'Warum hast du dein
Repeater-Passwort geändert?' Antwortet er..." -- the punchline was cut off. The
model ignored the character limit, nothing re-asked, the router cut at the
character limit, and the firmware counts bytes, not characters.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.formatting import clamp, utf8_len  # noqa: E402
from meshbot.handlers import frag as h_frag  # noqa: E402
from meshbot.main import Bot  # noqa: E402

WITZ_LANG = ("Ein Funker fragt den anderen: Warum hast du dein Repeater-Passwort geändert? "
             "Antwortet er: Damit endlich mal jemand zurückruft und nicht nur mithört.")
WITZ_KURZ = "Warum ist der Funker so ruhig? Er hat Sendepause."


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def settings() -> Settings:
    return Settings(mqtt_host="test", bot_name="MeshBot", max_msg_len=124,
                    frag_enabled=True, frag_model="testmodell",
                    json_path_text="payload.text", json_path_sender="payload.sender",
                    json_path_channel="payload.channel_idx")


def folge(antworten: list, gesehen: list | None = None):
    """A gateway that gives the next answer each call. Entries: str or (str, finish_reason)."""
    it = iter(antworten)
    last = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if gesehen is not None:
            gesehen.append(body)
        try:
            e = next(it)
            last["e"] = e
        except StopIteration:
            e = last["e"]
        text, grund = (e, "stop") if isinstance(e, str) else e
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": grund}]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- byte budget ------------------------------------------------------------


def test_bytebudget_aus_firmware_und_knotenname(settings):
    # "AT-VL-Nötsch-ObsBot: " is 19 characters but 22 bytes (ö = 2).
    assert len("AT-VL-Nötsch-ObsBot: ") == 21
    assert utf8_len("AT-VL-Nötsch-ObsBot: ") == 22
    assert settings.max_text_bytes == 160
    assert settings.nutzlimit_bytes == 160 - 22
    assert h_frag.budget_bytes(settings) == 160 - 22 - 4      # minus "KI: "
    assert h_frag.budget(settings) == 96                       # characters stay as before


def test_umlaute_zaehlen_doppelt():
    assert utf8_len("äöü") == 6
    assert utf8_len("abc") == 3


def test_antwort_mit_umlauten_wird_wegen_der_bytes_neu_gefragt(settings):
    """90 characters pass the character limit, but 180 bytes do not fit the air."""
    zu_breit = "ä" * 90
    gesehen: list = []
    client = folge([WITZ_KURZ], gesehen)
    fertig = run(h_frag.antworten(client, settings, zu_breit, "witz"))
    assert fertig == f"KI: {WITZ_KURZ}"
    assert len(gesehen) == 1


# --- trimming ----------------------------------------------------------------


@pytest.mark.parametrize("limit", range(10, 60))
def test_kuerzen_zerschneidet_nie_ein_multibyte_zeichen(limit):
    text = "Größe äußerst überprüfen " * 6
    out = clamp(text, 200, limit)
    assert utf8_len(out) <= limit
    out.encode("utf-8").decode("utf-8")                       # valid UTF-8
    assert out.endswith("…")


def test_kuerzen_an_wortgrenze_mit_ellipse():
    out = clamp("Das ist ein ziemlich langer Satz mit vielen Wörtern darin", 200, 40)
    assert out.endswith("…") and utf8_len(out) <= 40
    assert not out.rstrip("…").endswith(" ")
    assert out.rstrip("…") in "Das ist ein ziemlich langer Satz mit vielen Wörtern darin"


def test_clamp_ohne_bytegrenze_verhaelt_sich_wie_vorher():
    assert clamp("a b " * 40, 20).endswith("…")
    assert len(clamp("a b " * 40, 20)) <= 20
    assert clamp("kurz", 20) == "kurz"


# --- the asker's own limit ---------------------------------------------------


@pytest.mark.parametrize("frage,rest,n", [
    ("Witz 90 Zeichen", "Witz", 90),
    ("erzähl einen Witz max 80 Zeichen", "erzähl einen Witz", 80),
    ("witz in 60 Buchstaben", "witz", 60),
    ("Witz", "Witz", None),
    ("Was ist 868 MHz", "Was ist 868 MHz", None),
])
def test_laengenhinweis(frage, rest, n):
    assert h_frag.laengenhinweis(frage) == (rest, n)


def test_hinweis_ist_obergrenze_nie_ueber_dem_budget(settings):
    assert h_frag.grenze_fuer(settings, None) == 96
    assert h_frag.grenze_fuer(settings, 90) == 86             # whole line is 90, "KI: " comes off
    assert h_frag.grenze_fuer(settings, 500) == 96
    assert h_frag.grenze_fuer(settings, 5) == h_frag.MIN_GRENZE


# --- re-asking ----------------------------------------------------------------


def test_zu_lange_antwort_wird_strenger_neu_gefragt(settings):
    gesehen: list = []
    client = folge([WITZ_LANG, WITZ_KURZ], gesehen)
    fertig = run(h_frag.antworten(client, settings, WITZ_LANG, "Witz"))
    assert fertig == f"KI: {WITZ_KURZ}"
    assert len(gesehen) == 2
    grenzen = [int(b["messages"][0]["content"].split("hoechstens ")[1].split(" ")[0]) for b in gesehen]
    assert grenzen == [int(96 * 0.8), int(96 * 0.65)]          # stricter every time
    # the too-long answer travels along as context
    assert gesehen[0]["messages"][-2] == {"role": "assistant", "content": h_frag.bereinigen(WITZ_LANG)}


def test_hoechstens_zwei_nachfragen_dann_kuerzen(settings):
    gesehen: list = []
    client = folge([WITZ_LANG], gesehen)
    fertig = run(h_frag.antworten(client, settings, WITZ_LANG, "Witz"))
    assert len(gesehen) == 2
    assert len(fertig) <= settings.nutzlimit
    assert utf8_len(fertig) <= settings.nutzlimit_bytes


def test_abgeschnitten_laut_finish_reason_wird_neu_gefragt(settings):
    """finish_reason == "length": short enough to pass the limit, but cut by the token ceiling."""
    halb = "Warum ist der Funker so ruhig? Er hat"
    gesehen: list = []
    client = folge([WITZ_KURZ], gesehen)
    roh = h_frag.Roh(halb)
    roh.abgeschnitten = True
    fertig = run(h_frag.antworten(client, settings, roh, "Witz"))
    assert fertig == f"KI: {WITZ_KURZ}"
    assert len(gesehen) == 1


def test_passende_antwort_kostet_keinen_zweiten_aufruf(settings):
    gesehen: list = []
    client = folge([WITZ_KURZ], gesehen)
    assert run(h_frag.antworten(client, settings, WITZ_KURZ, "Witz")) == f"KI: {WITZ_KURZ}"
    assert gesehen == []


def test_ausfall_beim_nachfragen_faellt_aufs_kuerzen_zurueck(settings):
    def kaputt(request):
        return httpx.Response(500, text="kaputt")
    client = httpx.AsyncClient(transport=httpx.MockTransport(kaputt))
    fertig = run(h_frag.antworten(client, settings, WITZ_LANG, "Witz"))
    assert fertig.startswith("KI: ") and len(fertig) <= settings.nutzlimit


def test_anfuehrungszeichen_fliegen_raus():
    assert h_frag.bereinigen('"Warum? Darum."') == "Warum? Darum."
    assert h_frag.bereinigen("„Warum? Darum.“") == "Warum? Darum."


# --- through the bot ----------------------------------------------------------


def payload(text: str) -> str:
    return json.dumps({"type": "EventType.CHANNEL_MSG_RECV",
                       "payload": {"type": "CHAN", "channel_idx": 3, "text": f"OE8TEST: {text}"}})


def test_mini_witz_kommt_mit_pointe_an(settings):
    b = Bot(settings)
    b.http = folge([WITZ_LANG, WITZ_KURZ])
    antwort = run(b.router.handle(payload("!frag Witz")))
    assert antwort == f"KI: {WITZ_KURZ}"
    assert "Sendepause" in antwort                              # the punchline
    assert "…" not in antwort


def test_witz_90_zeichen_nutzt_hinweis_als_obergrenze(settings):
    gesehen: list = []
    b = Bot(settings)
    b.http = folge([WITZ_LANG, WITZ_KURZ], gesehen)
    antwort = run(b.router.handle(payload("!frag Witz 90 Zeichen")))
    assert "Zeichen" not in gesehen[0]["messages"][1]["content"]   # hint is not part of the question
    assert "hoechstens 86 Zeichen" in gesehen[0]["messages"][0]["content"]
    assert antwort == f"KI: {WITZ_KURZ}"
    assert len(antwort) <= 90


def test_lange_sachfrage_bleibt_im_budget_und_in_den_bytes(settings):
    lang = ("Die Reichweite von 868 MHz haengt vom Gelaende ab: im Tal oft nur einige Kilometer, "
            "von Gipfeln dagegen weit ueber hundert Kilometer bei freier Sicht und guter Antenne.")
    b = Bot(settings)
    b.http = folge([lang])
    antwort = run(b.router.handle(payload("!frag wie weit traegt 868 MHz")))
    assert antwort.startswith("KI: ")
    assert len(antwort) <= settings.nutzlimit
    assert utf8_len(antwort) <= settings.nutzlimit_bytes


def test_prompt_verlangt_pointe_und_vollstaendigen_satz(settings):
    p = h_frag.systemprompt(h_frag.budget(settings))
    assert "Pointe" in p and "vollstaendiger Satz" in p and "Anfuehrungszeichen" in p
    assert "96" in p


def test_emojis_fliegen_raus():
    """The 22 Oct. joke ended in an emoji (4 bytes) and never arrived."""
    assert h_frag.bereinigen("Sie sind immer auf Sendung! 📡") == "Sie sind immer auf Sendung!"
    assert h_frag.bereinigen("Super 😄❤️ ja") == "Super ja"
    assert h_frag.bereinigen("Größe äöü ß") == "Größe äöü ß"
