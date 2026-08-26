"""Zustellmessung und Zweitversuch.

Der Bot hoert seine eigene Antwort zurueck, wenn ein Repeater sie wiederholt.
Bleibt das Echo aus, gilt sie als nicht angekommen -- und wird einmal
wiederholt.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.zustellung import Zustellung, schluessel  # noqa: E402

ANTWORT = "WX Goldeck 2142m: 12.1C, 66%, Wind 6km/h W (Modell)"


def z(**kw):
    return Zustellung(frist_s=25, **kw)


def test_echo_zaehlt_als_zugestellt():
    t = z()
    t.gesendet(ANTWORT)
    assert t.echo(f"AT-VL-Noetsch-Observer: {ANTWORT}") is not None
    assert t.quote()["zugestellt"] == 1
    assert not t.offen


def test_fremder_verkehr_ist_kein_echo():
    t = z()
    t.gesendet(ANTWORT)
    assert t.echo("!wx villach") is None
    assert t.quote()["zugestellt"] == 0


def test_ohne_echo_wird_einmal_wiederholt_und_dann_gebucht():
    t = z(zweitversuche=1)
    t.gesendet(ANTWORT)
    faellig = t.faellig(jetzt=t.offen[schluessel(ANTWORT)].gesendet + 30)
    assert len(faellig) == 1 and t.darf_wiederholen(faellig[0])
    # zweiter Versuch, diesmal ohne Wiederholung
    t.gesendet(ANTWORT, versuch=2)
    faellig = t.faellig(jetzt=t.offen[schluessel(ANTWORT)].gesendet + 30)
    assert not t.darf_wiederholen(faellig[0])
    assert t.quote()["verloren"] == 1


def test_zweitversuch_abschaltbar():
    t = z(zweitversuche=0)
    t.gesendet(ANTWORT)
    faellig = t.faellig(jetzt=t.offen[schluessel(ANTWORT)].gesendet + 30)
    assert not t.darf_wiederholen(faellig[0])
    assert t.quote()["verloren"] == 1


def test_vor_ablauf_der_frist_passiert_nichts():
    t = z()
    t.gesendet(ANTWORT)
    assert t.faellig(jetzt=t.offen[schluessel(ANTWORT)].gesendet + 5) == []
    assert t.offen


def test_messung_schreibt_laenge_und_ergebnis(tmp_path):
    datei = tmp_path / "zustellung.jsonl"
    t = z(datei=datei)
    t.gesendet(ANTWORT)
    t.echo(ANTWORT)
    t.gesendet("kurz")
    t.faellig(jetzt=t.offen[schluessel("kurz")].gesendet + 30)
    zeilen = [json.loads(x) for x in datei.read_text(encoding="utf-8").splitlines()]
    assert zeilen[0]["ereignis"] == "zugestellt" and zeilen[0]["laenge"] == len(ANTWORT)
    assert zeilen[1]["ereignis"] == "wiederholt" and zeilen[1]["laenge"] == 4


def test_kaputte_datei_stoert_den_betrieb_nicht(tmp_path):
    """Die Messung darf nie wichtiger sein als der Dienst."""
    t = z(datei=tmp_path / "gibt" / "es" / "nicht.jsonl")
    t.gesendet(ANTWORT)
    assert t.echo(ANTWORT) is not None          # kein Absturz trotz Schreibfehler


def test_quote_rechnet_ohne_die_wiederholten():
    t = z(zweitversuche=0)
    for i in range(3):
        t.gesendet(f"antwort {i}")
        t.echo(f"antwort {i}")
    t.gesendet("verlorene antwort")
    t.faellig(jetzt=t.offen[schluessel("verlorene antwort")].gesendet + 30)
    assert t.quote() == {"zugestellt": 3, "verloren": 1, "wiederholt": 0, "quote": 75}
