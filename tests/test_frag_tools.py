"""!frag mit Werkzeugen: Whitelist, Rundenlimit, Fehlertexte, Rueckfall."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.handlers import frag_tools as t  # noqa: E402


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def settings() -> Settings:
    return Settings(mqtt_host="test", frag_model="m", litellm_api_key="k")


def call(name: str, args: dict, id_: str = "c1") -> dict:
    return {"id": id_, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def antwort(content: str = "", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def client(antworten: list[dict], gesehen: list[dict]) -> httpx.AsyncClient:
    it = iter(antworten)

    def handler(req: httpx.Request) -> httpx.Response:
        gesehen.append(json.loads(req.content))
        return httpx.Response(200, json=next(it))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def wx(arg: str, sender: str) -> str | None:
    return f"Villach 12C ({arg})"


def test_schema_nur_vorhandene_befehle():
    s = t.schema(["wetter", "mond"])
    assert [x["function"]["name"] for x in s] == ["wetter", "mond"]
    assert "ort" in s[0]["function"]["parameters"]["properties"]
    assert s[1]["function"]["parameters"]["properties"] == {}


def test_werkzeug_wird_aufgerufen_und_antwort_kommt_zurueck():
    gesehen: list[dict] = []
    c = client([antwort(tool_calls=[call("wetter", {"ort": "Villach"})]),
                antwort("Villach hat 12 Grad")], gesehen)
    text, benutzt = run(t.fetch(c, settings(), "Wie warm?", 100, "OE8X", {"wx": wx}))
    assert text == "Villach hat 12 Grad"
    assert benutzt == ["wetter"]
    assert [m["role"] for m in gesehen[1]["messages"]][-2:] == ["assistant", "tool"]
    assert gesehen[1]["messages"][-1]["content"] == "Villach 12C (Villach)"


def test_ohne_handler_kein_werkzeug_angeboten():
    gesehen: list[dict] = []
    c = client([antwort("ok")], gesehen)
    run(t.fetch(c, settings(), "x", 100, "OE8X", {"wx": wx}))
    namen = [x["function"]["name"] for x in gesehen[0]["tools"]]
    assert namen == ["wetter"]


def test_unbekanntes_werkzeug_wird_nicht_ausgefuehrt():
    gesehen: list[dict] = []
    c = client([antwort(tool_calls=[call("frag", {})]), antwort("nein")], gesehen)
    text, benutzt = run(t.fetch(c, settings(), "x", 100, "OE8X", {"wx": wx, "frag": wx}))
    assert benutzt == []
    assert gesehen[1]["messages"][-1]["content"] == "Unbekanntes Werkzeug"


def test_rundenlimit_letzte_runde_ohne_tools_und_abbruch():
    gesehen: list[dict] = []
    schleife = [antwort(tool_calls=[call("wetter", {"ort": "a"}, f"c{i}")]) for i in range(t.MAX_RUNDEN)]
    c = client(schleife, gesehen)
    try:
        run(t.fetch(c, settings(), "x", 100, "OE8X", {"wx": wx}))
        assert False, "haette werfen muessen"
    except ValueError:
        pass
    assert len(gesehen) == t.MAX_RUNDEN
    assert "tools" in gesehen[0] and "tools" not in gesehen[-1]


def test_werkzeugfehler_und_leer_gehen_als_text_zurueck():
    async def kaputt(arg, sender):
        raise RuntimeError("boom")

    async def leer(arg, sender):
        return None

    assert run(t.ausfuehren("wetter", "", "s", kaputt)) == "Fehler: Werkzeug nicht verfuegbar"
    assert run(t.ausfuehren("wetter", "", "s", leer)) == "Kein Ergebnis"


def test_ergebnis_wird_gekuerzt():
    async def lang(arg, sender):
        return "x " * 1000

    assert len(run(t.ausfuehren("wetter", "", "s", lang))) <= t.ERGEBNIS_MAX


def test_argument_defensiv():
    assert t._argument("wetter", "kein json") == ""
    assert t._argument("wetter", json.dumps({"ort": "Villach"})) == "Villach"
    assert t._argument("mond", json.dumps({"ort": "x"})) == ""
    assert len(t._argument("wetter", json.dumps({"ort": "a" * 500}))) == 80


def test_leere_antwort_wirft():
    c = client([antwort("")], [])
    try:
        run(t.fetch(c, settings(), "x", 100, "OE8X", {"wx": wx}))
        assert False
    except ValueError:
        pass


def test_wissen_treffer_geht_direkt_raus():
    gesehen: list[dict] = []

    async def wissen(arg, sender):
        return "Original-Text (quelle)"

    c = client([antwort(tool_calls=[call("wissen", {"thema": "repeater name"})])], gesehen)
    text, benutzt = run(t.fetch(c, settings(), "x", 100, "OE8X", {"wissen": wissen}))
    assert text == "Original-Text (quelle)" and benutzt == ["wissen"]
    assert len(gesehen) == 1
