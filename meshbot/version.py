"""Version des Bots und was sich zuletzt geaendert hat.

Warum eine Datei und keine Git-Beschreibung: Im Funknetz sieht niemand das
Repository. Wer meldet "der Bot antwortet komisch", muss sagen koennen, **welcher**
Bot -- sonst wird jede Fehlersuche zum Ratespiel darueber, ob die Ausrollung
ueberhaupt angekommen ist.

`KURZ` ist kein Changelog, sondern eine Funkmeldung: Sie muss in eine Nachricht
passen und beantwortet genau eine Frage -- was ist seit dem letzten Mal anders.
Alles Ausfuehrliche steht im Wiki.

Zaehlweise: Vorne bei einer Umstellung, die bestehende Befehle anders antworten
laesst, in der Mitte bei einem neuen Befehl, hinten bei einer Reparatur.
"""

from __future__ import annotations

VERSION = "1.5.0"

# Eine Zeile, Funknetz-tauglich. Nicht laenger als noetig -- der Kopf
# "MeshBot <version>: " geht davon ab.
KURZ = "!wx kennt Orte weltweit, !gipfel neu"

# Die letzten Stufen, neueste zuerst. Dient der Fehlersuche im Gespraech
# ("du hast noch 1.4") und wird nicht gefunkt.
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
