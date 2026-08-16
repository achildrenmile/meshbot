"""Hilfe und Befehlserkennung — die Teile, die mit jedem neuen Befehl brechen."""

import asyncio

from meshbot.config import Settings
from meshbot.main import Bot
from meshbot.router import ALIASES, parse_command


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def bot() -> Bot:
    return Bot(Settings())


def test_jeder_befehl_hat_einen_hilfetext():
    b = bot()
    ohne = set(b.router.handlers) - set(b.HILFE)
    assert not ohne, f"kein !help fuer: {sorted(ohne)}"


def test_jeder_befehl_steht_in_einer_gruppe():
    """Sonst taucht er in der Uebersicht nicht auf und ist praktisch unsichtbar."""
    b = bot()
    gruppiert = {c for g in b.GRUPPEN.values() for c in g}
    fehlt = set(b.router.handlers) - gruppiert - {"help"}
    assert not fehlt, f"in keiner Gruppe: {sorted(fehlt)}"


def test_jeder_befehl_ist_ueber_einen_alias_erreichbar():
    b = bot()
    erreichbar = set(ALIASES.values())
    assert not set(b.router.handlers) - erreichbar


def test_uebersicht_passt_in_eine_nachricht():
    b = bot()
    assert len(run(b.cmd_help("", "x"))) <= b.settings.max_msg_len


def test_uebersicht_faellt_auf_gruppen_zurueck_wenn_es_eng_wird():
    """Kein Abschneiden am Limit, sondern eine kuerzere Antwort."""
    b = bot()
    b.settings.max_msg_len = 60
    text = run(b.cmd_help("", "x"))
    assert len(text) <= 140 and "Themen:" in text


def test_gruppenhilfe_listet_die_gruppe():
    b = bot()
    text = run(b.cmd_help("standort", "x"))
    assert "!sicht" in text and "!hoehe" in text and "!dist" in text


def test_einzelhilfe_geht_vor_gruppenhilfe():
    b = bot()
    assert run(b.cmd_help("netz", "x")) == b.HILFE["netz"]      # !netz ist Befehl und Gruppe


def test_alle_hilfetexte_sind_kurz_genug():
    b = bot()
    zu_lang = {k: len(v) for k, v in b.HILFE.items() if len(v) > b.settings.max_msg_len}
    assert not zu_lang, f"zu lang: {zu_lang}"


def test_neue_aliase_werden_aufgeloest():
    assert parse_command("!los 46.6,13.8 46.7,13.9")[0] == "sicht"
    assert parse_command("!entfernung 46.6,13.8 46.7,13.9")[0] == "dist"
    assert parse_command("!moon")[0] == "mond"
    assert parse_command("!seehoehe 46.6,13.8")[0] == "hoehe"
    assert parse_command("!solar")[0] == "dx"
    assert parse_command("!sat")[0] == "iss"


def test_fehlende_argumente_erklaeren_sich():
    b = bot()
    for cmd in ("sicht", "hoehe", "dist"):
        antwort = run(b.router.handlers[cmd]("", "x"))
        assert antwort.startswith(f"!{cmd}"), antwort
        assert len(antwort) <= b.settings.max_msg_len
