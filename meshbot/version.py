"""The bot's version and what changed most recently.

Why a file and not a git description: nobody on the radio can see the
repository. Someone reporting "the bot is answering oddly" has to be able to say
**which** bot -- otherwise every investigation starts with guessing whether the
deployment even arrived.

`KURZ` is not a changelog but a radio message: it has to fit into one
transmission and answers exactly one question -- what is different since last
time. Everything detailed lives in the wiki.

Counting: major for a change that makes existing commands answer differently,
minor for a new command, patch for a fix.
"""

from __future__ import annotations

VERSION = "1.5.0"

# One line, fit for the radio network. No longer than necessary -- the prefix
# "MeshBot <version>: " comes off the budget.
KURZ = "!wx kennt Orte weltweit, !gipfel neu"

# The recent releases, newest first. Serves conversational debugging ("you are
# still on 1.4") and never goes on the air.
VERLAUF = [
    ("1.5.0", "2026-08-28", "!wx beantwortet Orte ausserhalb Kaerntens (Modell); "
                            "!gipfel getrennt von !wx; Wortgrenze statt Teilstring; "
                            "geratene Treffer bekommen ein Fragezeichen; Umlaute "
                            "werden gefunkt statt umschrieben; !version"),
    ("1.4.0", "2026-08-27", "!wx kennt Gipfel in zehn Laendern (SOTA-Liste)"),
    ("1.3.0", "2026-08-26", "Fehlende Argumente zeigen Verwendung und Beispiel"),
    ("1.2.0", "2026-08-25", "!wo loest Pfad-Hashes auf, Alias !pfad"),
]


def render() -> str:
    """`MeshBot 1.5.0: !gipfel neu, !wx raet nicht mehr`"""
    return f"MeshBot {VERSION}: {KURZ}"
