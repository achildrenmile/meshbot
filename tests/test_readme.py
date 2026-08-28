"""The README has to keep pace with the code.

It did not, and for months. `!uwz` had been renamed `!warn` in August; `!az`,
`!gipfel`, `!quota` and `!version` were missing from the table entirely; and the
sentence "the directory ends at the state border, `!wx Innsbruck` stays unknown"
survived a release that made exactly that false.

Documentation drift is not caught by reading. It is caught by a test.

What is deliberately **not** checked here: prose. Whether an explanation is
still accurate cannot be asserted, and pretending otherwise would give false
confidence. What is checked is everything with a machine-readable counterpart --
command names, aliases, group names, the version number. That is where drift
actually starts.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot import version  # noqa: E402
from meshbot.config import Settings  # noqa: E402
from meshbot.main import Bot  # noqa: E402
from meshbot.router import ALIASES  # noqa: E402

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")


def _text() -> str:
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def _tabellenzeilen(text: str) -> list[str]:
    """Only the rows of the command table, not every line mentioning a `!`."""
    return [z for z in text.splitlines() if z.startswith("| `!")]


def _genannte_befehle(text: str) -> set[str]:
    return set(re.findall(r"`!([a-z]+)", " ".join(_tabellenzeilen(text))))


def test_jeder_befehl_steht_in_der_tabelle():
    """A command nobody can find is a command nobody uses."""
    b = Bot(Settings())
    fehlend = sorted(set(b.router.handlers) - _genannte_befehle(_text()))
    assert not fehlend, f"nicht in der README-Tabelle: {fehlend}"


def test_kein_erfundener_befehl_in_der_tabelle():
    """The other direction: the table must not promise anything that is gone.

    This is the half that let `!uwz` survive for months -- it was in the table
    long after the command had been renamed.
    """
    b = Bot(Settings())
    echte = set(b.router.handlers) | set(ALIASES)
    erfunden = sorted(_genannte_befehle(_text()) - echte)
    assert not erfunden, f"steht in der README, gibt es nicht: {erfunden}"


def test_aliase_in_der_tabelle_zeigen_auf_denselben_befehl():
    """`!berg` next to `!gipfel` is right, `!berg` next to `!wx` would be wrong."""
    falsch = []
    for zeile in _tabellenzeilen(_text()):
        spalten = zeile.split("|")
        if len(spalten) < 3:
            continue
        ziel = re.search(r"`!([a-z]+)", spalten[1])
        if ziel is None:
            continue
        for alias in re.findall(r"`!([a-z]+)`", spalten[2]):
            if ALIASES.get(alias) != ALIASES.get(ziel.group(1)):
                falsch.append(f"!{alias} -> {ALIASES.get(alias)}, steht bei !{ziel.group(1)}")
    assert not falsch, falsch


def test_versionsnummer_stimmt():
    """A README naming an old version sends people chasing the wrong build."""
    assert version.VERSION in _text(), f"Version {version.VERSION} fehlt in der README"


def test_alle_gruppen_sind_genannt():
    """`!help <thema>` only helps when the topics are documented."""
    text = _text().lower()
    fehlend = [g for g in Bot.GRUPPEN if g not in text]
    assert not fehlend, f"Gruppen fehlen in der README: {fehlend}"


@pytest.mark.parametrize("veraltet,grund", [
    ("!uwz", "im August 2026 zu !warn umbenannt"),
])
def test_umbenannte_befehle_werden_nicht_mehr_beworben(veraltet, grund):
    """They live on in VERALTET as input, but must not be documented as current.

    Named in the table would tell a reader to type them; the answer would be a
    pointer to the new name, and the airtime would be wasted twice.
    """
    assert veraltet not in " ".join(_tabellenzeilen(_text())), grund
