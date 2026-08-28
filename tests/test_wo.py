"""!wo and the path hash.

The case at hand: the app shows `<Unknown Repeater d733>` and you are standing
out in the field without internet. The hash is the start of the public key —
nothing more is needed to name the node.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.formatting import prepare  # noqa: E402
from meshbot.handlers import wo as h_wo  # noqa: E402
from meshbot.router import parse_command  # noqa: E402

JETZT = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def node(name, key, verkehr=0, lat=46.6, lon=13.8, alter_h=1):
    return {
        "name": name,
        "public_key": key,
        "relay_count_24h": verkehr,
        "lat": lat,
        "lon": lon,
        "last_seen": (JETZT - timedelta(hours=alter_h)).isoformat().replace("+00:00", "Z"),
    }


NODES = [
    node("AT-K-Maria Saaler Berg", "d733b2aa" + "0" * 56, 963, 46.6664, 14.3442),
    node("AT-VL-GoeriacherAlm", "a92bd7cc" + "0" * 56, 2290),
    node("AT-HE-Waidegg", "1ee68644" + "0" * 56, 11),
    node("AT-K-Annabichl", "1e0c1122" + "0" * 56, 41),
    node("AT-WO-V3", "c2f36399" + "0" * 56, 902),
    node("AT-DEAD-Beispiel", "77aa0000" + "0" * 56, 5),
]


def test_hash_trifft_genau_einen():
    text = h_wo.antwort(NODES, "d733", JETZT)
    assert text.startswith("Pfad d733 = AT-K-Maria Saaler Berg")
    assert "963/24h" in text


def test_hash_ist_case_egal():
    assert h_wo.antwort(NODES, "D733", JETZT) == h_wo.antwort(NODES, "d733", JETZT)


@pytest.mark.parametrize("hashwert", ["a92b", "a92bd7", "a92bd7cc"])
def test_laengerer_hash_trifft_denselben_knoten(hashwert):
    assert "AT-VL-GoeriacherAlm" in h_wo.antwort(NODES, hashwert, JETZT)


def test_kollision_nennt_alle_und_den_staerksten_zuerst():
    """A one-byte hash means two nodes on `1e` — exactly what the wiki warns about."""
    text = h_wo.antwort(NODES, "1e", JETZT)
    assert text.startswith("Pfad 1e: 2 Treffer")
    assert text.index("AT-K-Annabichl") < text.index("AT-HE-Waidegg")   # 41 before 11


def test_kollision_nennt_hoechstens_drei():
    viele = [node(f"AT-X-{i}", f"ab{i:06d}" + "0" * 56, i) for i in range(9)]
    text = h_wo.antwort(viele, "ab", JETZT)
    assert text.startswith("Pfad ab: 9 Treffer")
    assert text.count("AT-X-") == 3


def test_hexbegriff_ohne_hashtreffer_faellt_auf_die_namenssuche_zurueck():
    """`dead` is valid hex and still a name here. The hit decides, not the shape."""
    text = h_wo.antwort(NODES, "dead", JETZT)
    assert text.startswith("AT-DEAD-Beispiel")


def test_unbekannter_hash_sagt_hash_und_erfindet_nichts():
    text = h_wo.antwort(NODES, "beef", JETZT)
    assert text == "Pfad beef: kein Knoten mit diesem Hash"


def test_hexbegriff_nimmt_keinen_aehnlichkeitstreffer():
    """`beef` and a node "Bergfee" — fuzzy would hit, and that would be wrong.

    Typing hex means a hash. A plausible-looking wrong answer is worse than
    "never heard of it".
    """
    nodes = NODES + [node("Bergfee", "99110000" + "0" * 56, 3)]
    assert h_wo.antwort(nodes, "beef", JETZT) == "Pfad beef: kein Knoten mit diesem Hash"
    # Without the hex shape, the similarity search still applies.
    assert h_wo.antwort(nodes, "bergfe", JETZT).startswith("Bergfee")


def test_name_geht_weiterhin():
    assert h_wo.antwort(NODES, "goeriacher", JETZT).startswith("AT-VL-GoeriacherAlm")


@pytest.mark.parametrize("begriff", ["d", "d7331122334455", "xyzz", "d7 33", ""])
def test_keine_hashform(begriff):
    assert not h_wo.ist_hashform(begriff)


@pytest.mark.parametrize("alias", ["!pfad d733", "!hash d733", "!path d733", "!wo d733", "!node d733"])
def test_aliase_landen_beim_selben_befehl(alias):
    assert parse_command(alias) == ("wo", "d733")


def test_antwort_haelt_das_zeichenlimit():
    s = Settings()
    lang = [node("AT-VL-Ein-sehr-langer-Knotenname-am-Berg-%02d" % i, f"ab{i:06d}" + "0" * 56, i)
            for i in range(9)]
    for begriff in ("ab", "d733", "beef", "x" * 80):
        text = prepare(h_wo.antwort(lang + NODES, begriff, JETZT), s.nutzlimit, s.transliterate)
        assert len(text) <= s.nutzlimit


def test_help_kennt_die_aliase():
    """A published alias without help is half a release.

    !pfad is in the wiki and the README. Anyone typing `!help pfad` because of
    that must not get the general overview.
    """
    import asyncio

    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings()
    for alias in ("pfad", "hash", "path", "node", "!pfad", "PFAD"):
        text = asyncio.run(Bot.cmd_help(bot, alias, "wer"))
        assert text == Bot.HILFE["wo"], alias


def test_help_gruppe_schlaegt_gleichnamigen_alias():
    """`wetter` is an alias for !wx and a group name. The group is meant."""
    import asyncio

    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings()
    text = asyncio.run(Bot.cmd_help(bot, "wetter", "wer"))
    assert text.startswith("Wetter: ")
    assert "!vorhersage" in text


@pytest.mark.parametrize("grenze", [100, 116, 140])
def test_uebersicht_bleibt_brauchbar(grenze):
    """At every character limit, !help must still be of help.

    Since MAX_MSG_LEN=124 (100 characters for the bot) the flat list no longer
    fits -- 167-byte packets did not arrive reliably on the network. The group
    form is therefore the normal case and has to hold up as one: all groups
    named, a pointer to !help <thema>, within the limit.
    """
    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings(max_msg_len=grenze + 24)
    assert bot.settings.nutzlimit == grenze
    alle = [c for gruppe in Bot.GRUPPEN.values() for c in gruppe]
    text = bot._uebersicht()
    assert len(text) <= grenze
    # Either every command is listed, or every group -- never a stump.
    vollstaendig = all(c in text for c in alle) or all(g in text for g in Bot.GRUPPEN)
    assert vollstaendig, text


def test_uebersicht_nennt_die_anzahl_wenn_die_liste_nicht_passt():
    """Five group names on their own read like an error message."""
    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings(max_msg_len=124)
    text = bot._uebersicht()
    # Compute the count from the groups instead of writing it down: otherwise
    # the test falls over on every new command without anything being broken.
    # Deduplicated, because !gipfel appears in two groups.
    anzahl = len({c for g in Bot.GRUPPEN.values() for c in g})
    assert text.startswith(f"{anzahl} Befehle in {len(Bot.GRUPPEN)} Gruppen:")


def test_help_netz_liefert_gruppe_und_befehl():
    """`netz` is command and group. The command used to win, leaving the group unreachable."""
    import asyncio

    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings()
    text = asyncio.run(Bot.cmd_help(bot, "netz", "wer"))
    assert text.startswith("Netz: ")
    for cmd in Bot.GRUPPEN["netz"]:
        assert "!" + cmd in text
    assert "Zustand des Mesh" in text          # the command text is appended
    assert text.count("!netz") == 1, text      # but the name only once
    assert len(text) <= bot.settings.nutzlimit


def test_help_kollision_faellt_auf_die_gruppe_zurueck_wenn_es_nicht_passt():
    """If both do not fit in one message, the group wins -- never a stump."""
    import asyncio

    from meshbot.main import Bot

    bot = Bot.__new__(Bot)
    bot.settings = Settings(max_msg_len=24 + 40)      # nutzlimit 40
    text = asyncio.run(Bot.cmd_help(bot, "netz", "wer"))
    assert text.startswith("Netz: ")
    assert "Zustand des Mesh" not in text
