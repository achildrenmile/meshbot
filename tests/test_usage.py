"""Fehlt ein Argument, sagt der Bot was fehlt -- und zeigt ein Beispiel.

Der Bot schweigt bei einem *unbekannten* Befehl, das ist Absicht und spart
Sendezeit. Ein richtig getippter Befehl mit fehlendem Argument ist aber kein
Muell: Da fehlt eine Kleinigkeit, und ein Beispiel kostet weniger als eine
zweite Runde Raten.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.formatting import prepare  # noqa: E402
from meshbot.main import Bot  # noqa: E402
from meshbot.router import ALIASES  # noqa: E402

# Befehle, die ohne Argument nichts Sinnvolles tun koennen.
BRAUCHT_ARGUMENT = ["sota", "az", "sicht", "hoehe", "dist", "qth", "wo", "melde"]


def bot():
    b = Bot.__new__(Bot)
    b.settings = Settings()
    return b


@pytest.mark.parametrize("cmd", BRAUCHT_ARGUMENT)
def test_jeder_pflichtbefehl_hat_einen_hinweis(cmd):
    assert cmd in Bot.USAGE, f"{cmd} hat keinen USAGE-Eintrag"


@pytest.mark.parametrize("cmd", sorted(Bot.USAGE))
def test_hinweis_nennt_befehl_und_beispiel(cmd):
    text = Bot.USAGE[cmd]
    assert text.startswith(f"!{cmd} "), text
    assert "z.B." in text, f"{cmd} nennt kein Beispiel"


@pytest.mark.parametrize("cmd", sorted(Bot.USAGE))
def test_hinweis_passt_in_eine_nachricht(cmd):
    s = Settings()
    assert len(prepare(Bot.USAGE[cmd], s.nutzlimit, s.transliterate)) <= s.nutzlimit


@pytest.mark.parametrize("cmd", sorted(Bot.USAGE))
def test_beispiel_ist_ein_echter_befehl(cmd):
    """Ein Beispiel, das der Bot selbst nicht erkennt, ist schlimmer als keines."""
    beispiel = Bot.USAGE[cmd].split("z.B. ", 1)[1]
    for teil in beispiel.split(" oder "):
        wort = teil.strip().split()[0].lstrip("!").lower()
        assert wort in ALIASES, f"{cmd}: Beispiel nennt unbekannten Befehl !{wort}"


@pytest.mark.parametrize("cmd", BRAUCHT_ARGUMENT)
def test_leeres_argument_liefert_den_hinweis(cmd):
    """Der eigentliche Test: Handler ohne Argument aufrufen."""
    b = bot()
    handler = getattr(b, f"cmd_{cmd}")
    antwort = asyncio.run(handler("", "wer"))
    assert antwort == Bot.USAGE[cmd], f"!{cmd} ohne Argument: {antwort!r}"


def test_usage_kennt_auch_unbekannte_befehle():
    """Kein Absturz, wenn jemand usage() fuer etwas ohne Eintrag aufruft."""
    assert bot().usage("gibtsnicht") == "!gibtsnicht: Argument fehlt"
