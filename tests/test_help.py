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
    assert len(run(b.cmd_help("", "x"))) <= b.settings.nutzlimit


def test_uebersicht_nennt_alle_befehle_solange_es_geht():
    """Der Node haengt seinen Namen vorn dran — das Budget ist knapp, aber
    fuer die nackte Liste reicht es. Ein Themenmenue waere die schlechtere
    Antwort, solange die Namen noch hineinpassen."""
    b = bot()
    text = run(b.cmd_help("", "x"))
    fehlend = [c for c in b.router.handlers if c != "help" and c not in text]
    assert not fehlend, f"nicht in der Uebersicht: {fehlend}"


def test_nutzlimit_laesst_platz_fuer_den_absendernamen():
    b = bot()
    assert b.settings.nutzlimit == b.settings.max_msg_len - b.settings.sender_reserve
    assert b.settings.sender_reserve >= 23      # laengster Name im Netz + ": "


def test_antworten_bleiben_unter_dem_nutzlimit():
    """Der Router kuerzt auf das Nutzlimit, nicht auf das Firmwarelimit."""
    from meshbot.router import Router
    import json as _json

    async def lang(arg: str, sender: str) -> str:
        return "A" * 300
    b = bot()
    r = Router(b.settings, {"ping": lang})
    roh = _json.dumps({"payload": {"text": "OE8YML: !ping"}})
    assert len(run(r.handle(roh))) <= b.settings.nutzlimit


def test_uebersicht_faellt_auf_gruppen_zurueck_wenn_es_eng_wird():
    """Kein Abschneiden am Limit, sondern eine kuerzere Antwort."""
    b = bot()
    b.settings.max_msg_len = 84                 # Nutzlimit 60
    text = run(b.cmd_help("", "x"))
    assert len(text) <= 60 and "Themen:" in text


def test_gruppenhilfe_listet_die_gruppe():
    b = bot()
    text = run(b.cmd_help("standort", "x"))
    assert "!sicht" in text and "!hoehe" in text and "!dist" in text


def test_einzelhilfe_geht_vor_gruppenhilfe():
    b = bot()
    assert run(b.cmd_help("netz", "x")) == b.HILFE["netz"]      # !netz ist Befehl und Gruppe


def test_alle_hilfetexte_sind_kurz_genug():
    b = bot()
    zu_lang = {k: len(v) for k, v in b.HILFE.items() if len(v) > b.settings.nutzlimit}
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
        assert len(antwort) <= b.settings.nutzlimit
