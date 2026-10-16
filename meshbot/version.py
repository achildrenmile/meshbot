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

VERSION = "1.10.0"

# One line, fit for the radio network. No longer than necessary -- the prefix
# "MeshBot <version>: " comes off the budget.
KURZ = "!frag kann im Web nachsehen"

# The recent releases, newest first. Serves conversational debugging ("you are
# still on 1.4") and never goes on the air.
VERLAUF = [
    ("1.10.0", "2026-10-16", "!frag bekommt das Werkzeug suche: Websuche ueber eigene SearXNG-Instanz (SEARCH_URL), nur Domain, Titel und kurzer Auszug"),
    ("1.9.4", "2026-10-16", "Werkzeug wissen sucht mit Modell-Suchwort und Originalfrage zusammen; das Modell liess sonst Woerter wie LV8 weg"),
    ("1.9.3", "2026-10-16", "Werkzeug wissen erkennt Wortformen (gleicher Wortanfang ab 5 Buchstaben, ss=ß): benenne, Benennung, nennen finden denselben Eintrag"),
    ("1.9.2", "2026-10-16", "Treffer des Werkzeugs wissen gehen unveraendert als Antwort raus (kein Umformulieren durch das Modell); ein Treffer braucht mindestens zwei Stichworte"),
    ("1.9.1", "2026-10-16", "!frag bekommt das Werkzeug wissen: gepflegte Regeln mit Quelle aus WISSEN_FILE, z.B. wie Repeater im MeshCore-Netz benannt werden"),
    ("1.9.0", "2026-10-16", "!frag kann (FRAG_WERKZEUGE=true) lesende Bot-Befehle "
                            "selbst aufrufen (Wetter, Netz, Mond, ISS, DX, Relais, "
                            "Termine ...), hoechstens 3 Modellrunden, feste Whitelist; "
                            "Modelle ohne Werkzeugunterstuetzung antworten wie bisher"),
    ("1.8.0", "2026-10-16", "Neu: !events (Aliase !event !termine) nennt genau den "
                            "naechsten Termin aus einer gepflegten JSON-Liste "
                            "(EVENTS_FILE). Die Datei wird bei Aenderung neu "
                            "gelesen, ein Neustart ist nicht noetig"),
    ("1.7.2", "2026-09-27", "Kein auf dem Funk sichtbarer Unterschied: !frag "
                            "spricht jetzt mit einem LiteLLM-Gateway "
                            "(OpenAI-kompatibles /v1/chat/completions) statt "
                            "direkt mit Ollama. Modell (gemma3:4b, ueber den "
                            "Alias chat-small) und Antwortverhalten "
                            "unveraendert"),
    ("1.7.1", "2026-09-19", "Kein auf dem Funk sichtbarer Unterschied: der Bot "
                            "veroeffentlicht sein eigenes globales Ratelimit "
                            "jetzt zusaetzlich retained per MQTT "
                            "(meshinfra/bot/quota), analog zum Gate. Grundlage "
                            "fuer eine externe Anzeige (Home Assistant), ob das "
                            "Bot-Limit gerade erreicht ist"),
    ("1.7.0", "2026-08-30", "Direktnachrichten: wer den Bot direkt anschreibt, "
                            "bekommt die Antwort direkt zurueck statt ueber den "
                            "Kanal. Eine Kanalantwort flutet und beschaeftigt "
                            "jeden der 35 Repeater; eine DM laeuft ueber einen "
                            "bekannten Pfad. Der Kanal bleibt, weil dort alle "
                            "mitlesen. Umgekehrt geht es nicht: eine "
                            "Kanalnachricht traegt keine Absenderadresse"),
    ("1.6.0", "2026-08-29", "!frag beantwortet freie Fragen ueber ein Sprachmodell "
                            "auf rag-node-01; die Antwort traegt das Praefix KI:, weil "
                            "sie als einzige im Bot nicht gemessen ist; eigene Bremsen "
                            "(2 pro Absender/15min, 100/Tag) und ein eigener Notaus "
                            "'frag off'; faellt der Dienst aus, schweigt der Befehl"),
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
