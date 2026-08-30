"""!frag — the command that can produce something nobody checked.

Every other handler is tested for whether it renders the right value. This one
is tested mostly for what it must **not** let through: markdown into a
100-character line, a backslash into the JSON template that carries the answer to
the bridge, a reasoning block onto the air, an unlimited number of requests onto
a machine that also runs other services.

The one that would actually break production is the backslash. `main.on_message`
builds the outgoing payload by string-formatting the answer into a JSON template.
No other handler can emit a backslash; a language model can, and the message
would be lost with a parse error at the far end.
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
from meshbot.handlers import frag as h_frag  # noqa: E402
from meshbot.main import Bot  # noqa: E402
from meshbot.router import parse_command  # noqa: E402


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def payload(text: str, sender: str = "OE8TEST") -> str:
    return json.dumps({
        "type": "EventType.CHANNEL_MSG_RECV",
        "payload": {"type": "CHAN", "channel_idx": 3, "text": f"{sender}: {text}"},
    })


@pytest.fixture
def settings() -> Settings:
    # max_msg_len 124 mirrors the production host: 124 - 24 reserve = 100
    # characters, the budget the answers actually have to fit into. The default
    # of 140 would let lines through that the node rejects.
    return Settings(mqtt_host="test", bot_name="MeshBot", max_msg_len=124,
                    frag_enabled=True, frag_model="testmodell",
                    json_path_text="payload.text", json_path_sender="payload.sender",
                    json_path_channel="payload.channel_idx")


def antwortet(text: str, status: int = 200):
    """A stand-in Ollama that always says the same thing."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="kaputt")
        return httpx.Response(200, json={"message": {"role": "assistant", "content": text}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- the handler on its own ------------------------------------------------


def test_praefix_kommt_davor(settings):
    client = antwortet("Rund 5 km im Tal")
    roh = run(h_frag.fetch(client, settings, "wie weit traegt 868 MHz"))
    assert h_frag.render(roh, settings.frag_praefix) == "KI: Rund 5 km im Tal"


def test_backslash_und_markdown_fliegen_raus():
    """The backslash is the one that matters -- it would break the JSON template.

    `main.on_message` formats the answer into `{"channel": 3, "message": "..."}`
    by string substitution. A backslash survives the quote replacement there and
    turns the payload into something the bridge cannot parse; the answer is then
    lost without any handler having failed.
    """
    roh = "**Ja**, siehe `C:\\Pfad` und _hier_"
    fertig = h_frag.render(roh, "KI:")
    assert "\\" not in fertig
    assert "*" not in fertig and "`" not in fertig and "_" not in fertig
    assert fertig == "KI: Ja, siehe C:Pfad und hier"


def test_steuerzeichen_und_zeilenumbrueche_werden_zu_einer_zeile():
    assert h_frag.render("Zeile eins\nZeile zwei\r\n\tdrei", "KI:") == "KI: Zeile eins Zeile zwei drei"


def test_denkblock_wird_entfernt():
    """Some models keep reasoning even with thinking switched off."""
    roh = "<think>Der Nutzer fragt nach der Reichweite. Ich ueberlege...</think>Etwa 5 km"
    assert h_frag.render(roh, "KI:") == "KI: Etwa 5 km"


def test_vorspann_wird_entfernt():
    """"Antwort:" costs eight characters and says nothing."""
    assert h_frag.render("Antwort: 2864 Meter", "KI:") == "KI: 2864 Meter"
    assert h_frag.render("Die Antwort lautet: 2864 Meter", "KI:") == "KI: 2864 Meter"


def test_echter_satz_der_mit_antwort_beginnt_bleibt_stehen():
    """The preamble filter must not eat a real sentence.

    "Die Antwort haengt davon ab" is the answer, not an introduction to one --
    which is why only the form with a colon is stripped.
    """
    assert h_frag.render("Die Antwort haengt vom Gelaende ab", "KI:") \
        == "KI: Die Antwort haengt vom Gelaende ab"


def test_leere_antwort_wirft(settings):
    client = antwortet("")
    with pytest.raises(ValueError):
        run(h_frag.fetch(client, settings, "irgendwas"))


def test_ollama_fehler_bei_status_200_wird_benannt(settings):
    """Ollama reports its own failures as HTTP 200 with an `error` field.

    Observed on rag-node-01: gpt-oss:20b gets killed by the OOM killer and the
    request comes back 200 with
    `{"error": "llama-server process has terminated: signal: killed"}`.
    Both this and a genuinely empty answer end in silence on the air -- but only
    one of them tells you the machine ran out of memory.
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"error": "llama-server process has terminated: signal: killed"})))
    with pytest.raises(ValueError) as exc:
        run(h_frag.fetch(client, settings, "irgendwas"))
    assert "signal: killed" in str(exc.value)


def test_antwort_nur_aus_muell_wirft():
    with pytest.raises(ValueError):
        h_frag.render("**__``**", "KI:")


def test_fachbegriffe_stehen_als_beispiel_im_systemprompt(settings):
    """The glossary is the one part of the prompt that measurably rescued answers.

    Without it, `!frag was bedeutet SF5/SF8` went on the air as "Signal-Kraft-Faktor,
    hoeher ist besser" -- SF is the Spreading Factor, and higher is not better but
    slower. In front of an audience of radio amateurs, under the operator's callsign.

    As prose the same terms were ignored; only as question-and-answer pairs did the
    model reproduce them. So this checks the *form* too: whoever shortens the prompt
    later should see this test fail before the network does.
    """
    p = h_frag.systemprompt(h_frag.budget(settings))
    for begriff in ("Spreading Factor", "Signal-Rausch-Abstand", "Empfangspegel",
                    "Sendezeitanteil"):
        assert begriff in p, f"{begriff} fehlt im Systemprompt"
    assert p.count("Frage:") >= 6, "zu wenige Beispiele -- Regeln allein wirken nicht"


def test_verweisbeispiele_nennen_verschiedene_befehle(settings):
    """One command in the examples turns into that command for everything.

    With `!gipfel` as the only pointer, "wie ist der verkehr im netz" came back as
    `Frag !gipfel verkehr`. With a list of allowed commands in prose instead, the
    model answered `Frag !netz, das erklaert Signal-Rausch-Abstand` to *what is SNR*.
    Two examples with two different commands is what actually worked.
    """
    p = h_frag.systemprompt(h_frag.budget(settings))
    assert "!netz" in p and "!gipfel" in p
    # Antworten muessen ueberwiegen, sonst wird Verweisen zum Standardverhalten.
    assert p.count("Frage:") - p.count("Antwort: Frag !") >= 4


def test_zeichengrenze_steht_als_zahl_im_systemprompt(settings):
    """A model treats "kurz" as a suggestion and a number as a rule."""
    # 100 usable minus "KI:" and the space after it.
    assert h_frag.budget(settings) == 96
    assert "96" in h_frag.systemprompt(h_frag.budget(settings))


# --- what happens when the answer is too long ------------------------------


def test_ganze_saetze_fliegen_raus_statt_halber(settings):
    """Two sentences that fit beat three that get cut through."""
    roh = ("Im Tal sind es 2 bis 5 km. Mit freier Sicht auf einen Berg deutlich mehr. "
           "Bei Regen und dichtem Wald wird es spuerbar weniger, oft unter einem Kilometer.")
    fertig = h_frag.render(roh, "KI:", h_frag.budget(settings))
    assert fertig == "KI: Im Tal sind es 2 bis 5 km. Mit freier Sicht auf einen Berg deutlich mehr."
    assert len(fertig) <= settings.nutzlimit
    assert "…" not in fertig


def test_ein_langer_satz_faellt_auf_clamp_zurueck(settings):
    """Nothing to trim at: the router cuts at the word boundary, as before."""
    b = Bot(settings)
    b.http = antwortet("Das haengt sehr stark vom Gelaende und von der Antennenhoehe ab "
                       "und laesst sich pauschal ueberhaupt nicht seriös beantworten")
    antwort = run(b.router.handle(payload("!frag reichweite")))
    assert len(antwort) <= settings.nutzlimit
    assert antwort.endswith("…")


def test_erster_satz_zu_kurz_wird_nicht_bevorzugt():
    """"Ja." must not throw away eighty good characters that clamp would keep."""
    roh = "Ja. " + "Und zwar deshalb, weil das Gelaende die Reichweite bestimmt " * 3
    fertig = h_frag.render(roh, "KI:", 96)
    assert not fertig.startswith("KI: Ja. Und") or len(fertig) > 20
    assert fertig != "KI: Ja."


def test_dezimalzahl_ist_kein_satzende():
    """Cutting "2.5 km" after the 2 would change the number, not just the length."""
    roh = ("Im Tal etwa 2.5 km und bei freier Sicht deutlich weiter. "
           "Im dichten Wald bleibt oft weniger als ein Kilometer uebrig.")
    fertig = h_frag.render(roh, "KI:", 96)
    assert fertig == "KI: Im Tal etwa 2.5 km und bei freier Sicht deutlich weiter."


def test_abkuerzung_ist_kein_satzende():
    """A cut after "z.B." reads as though the bot broke off mid-thought."""
    roh = ("Etwa fuenf Kilometer, z.B. zwischen Villach und Arnoldstein bei freier Sicht. "
           "Sonst deutlich weniger, oft unter einem Kilometer.")
    fertig = h_frag.render(roh, "KI:", 96)
    assert fertig.endswith("bei freier Sicht.")
    assert not fertig.endswith("z.B.")


# --- through the bot -------------------------------------------------------


def test_abgeschaltet_schweigt(settings):
    b = Bot(settings.model_copy(update={"frag_enabled": False}))
    b.http = antwortet("Eine tadellose Antwort")
    assert run(b.cmd_frag("wie weit traegt 868 MHz", "OE8TEST")) is None


def test_zu_lange_antwort_wird_am_wortende_gekappt(settings):
    """The system prompt is a request, clamp() is the enforcement."""
    b = Bot(settings)
    b.http = antwortet("Das haengt sehr stark vom Gelaende ab und laesst sich pauschal "
                       "ueberhaupt nicht beantworten, weil jede Strecke anders liegt")
    antwort = run(b.router.handle(payload("!frag wie weit traegt 868 MHz")))
    assert antwort.startswith("KI: ")
    assert len(antwort) <= settings.nutzlimit == 100
    assert antwort.endswith("…")


def test_ausfall_schweigt(settings):
    """No cloud fallback and no error message -- the command simply says nothing."""
    b = Bot(settings)
    b.http = antwortet("", status=500)
    assert run(b.cmd_frag("wie weit traegt 868 MHz", "OE8TEST")) is None


def test_zweite_gleiche_frage_kommt_aus_dem_cache(settings):
    """A cached answer costs no CPU on rag-node-01, so it costs no allowance either."""
    aufrufe = []

    def handler(request: httpx.Request) -> httpx.Response:
        aufrufe.append(request)
        return httpx.Response(200, json={"message": {"content": "Etwa 5 km"}})

    b = Bot(settings)
    b.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    erst = run(b.cmd_frag("wie weit traegt 868 MHz", "OE8TEST"))
    # Different case, different spacing -- the same question.
    zweit = run(b.cmd_frag("Wie weit  traegt 868 MHz", "OE8ANDER"))
    assert erst == zweit == "KI: Etwa 5 km"
    assert len(aufrufe) == 1


def test_drittes_frag_desselben_absenders_schweigt(settings):
    """Two per sender per 15 minutes, stricter than the four every command gets."""
    b = Bot(settings)
    b.http = antwortet("Etwa 5 km")
    assert run(b.cmd_frag("frage eins", "OE8TEST")) is not None
    assert run(b.cmd_frag("frage zwei", "OE8TEST")) is not None
    assert run(b.cmd_frag("frage drei", "OE8TEST")) is None
    # Somebody else is not affected by it.
    assert run(b.cmd_frag("frage vier", "OE8ANDER")) is not None


def test_tageslimit_schweigt(settings):
    b = Bot(settings.model_copy(update={"frag_tageslimit": 1}))
    b.http = antwortet("Etwa 5 km")
    assert run(b.cmd_frag("frage eins", "OE8TEST")) is not None
    assert run(b.cmd_frag("frage zwei", "OE8ANDER")) is None


def test_ohne_frage_kommt_die_verwendung(settings):
    b = Bot(settings)
    b.http = antwortet("sollte nie gefragt werden")
    assert run(b.cmd_frag("", "OE8TEST")).startswith("!frag <frage>")


def test_zu_lange_frage_wird_gekappt(settings):
    """The question is billed as prefill time on a CPU. It is not unlimited."""
    gesehen = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"message": {"content": "ok"}})

    b = Bot(settings)
    b.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(b.cmd_frag("x " * 400, "OE8TEST"))
    assert len(gesehen[0]) <= settings.frag_frage_max


def test_aliase_zeigen_auf_frag():
    for alias in ("!frag", "!frage", "!ask", "!ki"):
        assert parse_command(alias)[0] == "frag"


def test_eigener_notaus_laesst_den_rest_laufen(settings):
    """`frag off` must not silence the weather along with the AI."""
    b = Bot(settings)
    b.http = antwortet("Etwa 5 km")
    b.on_admin(b"frag off")
    assert b.frag_enabled is False
    assert b.router.enabled is True
    assert run(b.cmd_frag("frage eins", "OE8TEST")) is None
    b.on_admin(b"frag on")
    assert run(b.cmd_frag("frage zwei", "OE8TEST")) is not None


# --- ausfuehren statt verweisen --------------------------------------------


def test_verweis_wird_erkannt():
    assert h_frag.verweis("KI: Frag !netz, das zaehlt nach.") == ("netz", "")
    assert h_frag.verweis("KI: Frag !gipfel dobratsch, das misst nach.") == ("gipfel", "dobratsch")
    assert h_frag.verweis("KI: LoRa ist eine Funktechnologie.") is None


def test_netzfrage_wird_ausgefuehrt_statt_verwiesen(settings):
    """Die gemessene Antwort schlaegt den Verweis auf einen zweiten Befehl."""
    b = Bot(settings)
    b.http = antwortet("Frag !netz, das zaehlt nach.")

    async def netz(arg, sender):
        return "Netz KTN: 29/33 aktiv, Weiterl. 2578/1h"

    b.router.handlers["netz"] = netz

    a = run(b.cmd_frag("wie viele repeater gibt es", "OE8TEST"))
    assert a == "Netz KTN: 29/33 aktiv, Weiterl. 2578/1h"
    # Kein KI: -- das ist gemessen, nicht geraten.
    assert not a.startswith("KI:")


def test_bergfrage_reicht_das_argument_durch(settings):
    gesehen = []
    b = Bot(settings)
    b.http = antwortet("Frag !gipfel dobratsch, das misst nach.")

    async def gipfel(arg, sender):
        gesehen.append(arg)
        return "WX Dobratsch 2166m: 9.0C, 79%"

    b.router.handlers["gipfel"] = gipfel
    assert run(b.cmd_frag("wie hoch ist der dobratsch", "OE8TEST")) == "WX Dobratsch 2166m: 9.0C, 79%"
    assert gesehen == ["dobratsch"]


def test_nicht_ausfuehrbarer_befehl_bleibt_verweis(settings):
    """!dist will Koordinaten. "villach klagenfurt" waere nur eine Verwendungszeile."""
    b = Bot(settings)
    b.http = antwortet("Frag !dist villach klagenfurt, das misst nach.")
    a = run(b.cmd_frag("wie weit ist villach von klagenfurt", "OE8TEST"))
    assert a == "KI: Frag !dist villach klagenfurt, das misst nach."


def test_verwendungszeile_gilt_nicht_als_antwort(settings):
    b = Bot(settings)
    b.http = antwortet("Frag !gipfel, das misst nach.")

    async def gipfel(arg, sender):
        return "!gipfel <berg> - z.B. !gipfel dobratsch"

    b.router.handlers["gipfel"] = gipfel
    a = run(b.cmd_frag("wie hoch ist der berg", "OE8TEST"))
    assert a == "KI: Frag !gipfel, das misst nach."


def test_handlerfehler_faellt_auf_die_modellantwort_zurueck(settings):
    b = Bot(settings)
    b.http = antwortet("Frag !netz, das zaehlt nach.")

    async def kaputt(arg, sender):
        raise RuntimeError("Karten-API weg")

    b.router.handlers["netz"] = kaputt
    assert run(b.cmd_frag("wie viele repeater", "OE8TEST")) == "KI: Frag !netz, das zaehlt nach."


def test_ausgefuehrtes_wird_nicht_zwischengespeichert(settings):
    """Ein Messwert von jetzt ist in einer Stunde ein anderer."""
    b = Bot(settings)
    b.http = antwortet("Frag !netz, das zaehlt nach.")

    async def netz(arg, sender):
        return "Netz KTN: 29/33 aktiv"

    b.router.handlers["netz"] = netz
    run(b.cmd_frag("wie viele repeater", "OE8TEST"))
    assert "wie viele repeater" not in b.cache_frag


# --- watchdog for the inference host ---------------------------------------


def test_probe_url_zeigt_auf_version(settings):
    """`version` answers without loading a model -- the probe must cost nothing.

    Derived from the configured URL rather than written out, so moving the
    service to another host or port does not need a test change.
    """
    assert h_frag.probe_url(settings) == settings.ollama_url.replace("/api/chat", "/api/version")
    assert h_frag.probe_url(
        Settings(mqtt_host="x", ollama_url="http://host:9/api/chat/")) == "http://host:9/api/version"


def test_probe_meldet_erreichbar_und_weg(settings):
    da = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"version": "0.33.1"})))
    weg = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(502, text="")))
    assert run(h_frag.erreichbar(da, settings)) is True
    assert run(h_frag.erreichbar(weg, settings)) is False


def test_probe_schluckt_verbindungsfehler(settings):
    """A watchdog that throws is worse than no watchdog."""
    def kaputt(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = httpx.AsyncClient(transport=httpx.MockTransport(kaputt))
    assert run(h_frag.erreichbar(client, settings)) is False


def test_toter_dienst_macht_den_container_nicht_ungesund(settings):
    """!wx must not go down because the AI did."""
    from meshbot.health import zustand

    b = Bot(settings)
    b.mqtt.connected = True
    b.frag_erreichbar = False
    ok, body = zustand(b, settings.health_mqtt_grace_s)
    assert ok is True
    assert body["frag"] == "weg"


def test_healthz_meldet_aus_wenn_frag_aus_ist(settings):
    from meshbot.health import zustand

    b = Bot(settings.model_copy(update={"frag_enabled": False}))
    b.mqtt.connected = True
    assert zustand(b, settings.health_mqtt_grace_s)[1]["frag"] == "aus"


def test_hilfe_bewirbt_frag_nur_wenn_es_an_ist(settings):
    """A listed but dead command looks like a broken bot.

    Unknown commands are answered with silence, so somebody who reads !frag in
    the overview and types it gets nothing back. With the switch off the command
    must not appear in the overview at all -- and that is how the first
    deployment runs.
    """
    aus = Bot(settings.model_copy(update={"frag_enabled": False}))
    an = Bot(settings)

    assert "frag" not in aus._uebersicht()
    assert "frag" not in run(aus.cmd_help("sonst", "x"))
    assert "!frag" in run(an.cmd_help("sonst", "x"))

    # The count in the overview has to follow along, not just the list.
    assert aus._uebersicht().startswith("24 Befehle")
    assert an._uebersicht().startswith("25 Befehle")


def test_einzelhilfe_zu_frag_bleibt_erreichbar(settings):
    """`!help frag` still answers when off -- somebody read about it somewhere."""
    aus = Bot(settings.model_copy(update={"frag_enabled": False}))
    assert "!frag" in run(aus.cmd_help("frag", "x"))


def test_pause_schaltet_frag_nicht_ein(settings):
    """The two switches are independent, in both directions."""
    b = Bot(settings.model_copy(update={"frag_enabled": False}))
    b.on_admin(b"resume")
    assert b.frag_enabled is False
