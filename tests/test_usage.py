"""When an argument is missing, the bot says what -- and shows an example.

The bot stays silent on an *unknown* command; that is deliberate and saves
airtime. But a correctly typed command with a missing argument is not garbage:
something small is missing, and an example costs less than a second round of
guessing.
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

# Commands that cannot do anything sensible without an argument.
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
    """An example the bot itself does not recognise is worse than none."""
    beispiel = Bot.USAGE[cmd].split("z.B. ", 1)[1]
    for teil in beispiel.split(" oder "):
        wort = teil.strip().split()[0].lstrip("!").lower()
        assert wort in ALIASES, f"{cmd}: Beispiel nennt unbekannten Befehl !{wort}"


@pytest.mark.parametrize("cmd", BRAUCHT_ARGUMENT)
def test_leeres_argument_liefert_den_hinweis(cmd):
    """The actual test: call the handler without an argument."""
    b = bot()
    handler = getattr(b, f"cmd_{cmd}")
    antwort = asyncio.run(handler("", "wer"))
    assert antwort == Bot.USAGE[cmd], f"!{cmd} ohne Argument: {antwort!r}"


def test_usage_kennt_auch_unbekannte_befehle():
    """No crash when usage() is called for something without an entry."""
    assert bot().usage("gibtsnicht") == "!gibtsnicht: Argument fehlt"


def test_netz_bleibt_auch_bei_grossen_zahlen_im_limit():
    """The answer sat at 99 of 100 characters -- one more digit in the daily
    traffic would have truncated the busiest repeater."""
    from meshbot.handlers import netz as h_netz

    s = Settings()
    text = h_netz.render({
        "aktiv_1h": 199, "aktiv_24h": 198, "gesamt": 200,
        "weiter_1h": 999999, "weiter_24h": 9999999,
        "top": ("AT-VL-Ein-langer-Repeatername", 999999),
    })
    assert len(prepare(text, s.nutzlimit, s.transliterate)) <= s.nutzlimit
    assert "…" not in prepare(text, s.nutzlimit, s.transliterate), text
