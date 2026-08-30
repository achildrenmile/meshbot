"""!frag — a free question, answered by a language model.

The odd one out among the handlers. Every other command returns something
measured or computed: a station reading, an ephemeris, a polygon. This one
returns what a model believes, and a model can be confidently wrong.

Three consequences follow from that, and all three are load-bearing:

1. **The answer is marked.** Everything the bot sends goes out under the
   operator's callsign, and on the channel a sentence from a model looks exactly
   like a sentence from a measuring station. The `KI:` prefix is the only thing
   that tells them apart — it costs four of the hundred characters and is not
   negotiable.
2. **Failure means silence.** No cloud fallback, no error message on the air.
   If the box is down, !frag says nothing at all, and the rest of the bot keeps
   working.
3. **The output is scrubbed, not trusted.** The answer is later interpolated
   into a JSON template by string formatting (`main.on_message`). A backslash or
   a control character in the model's output would break that template and take
   the message with it — no other handler can produce those, this one can.

Inference runs locally on rag-node-01 via Ollama. No API key, no per-request
cost; the scarce resource here is CPU on a machine that also runs k3s.
"""

from __future__ import annotations

import re

import httpx

from ..formatting import transliterate

# Markdown, control characters and the backslash. The backslash is the dangerous
# one: it survives the quote replacement in on_message and breaks the JSON
# template. The rest is cosmetic -- asterisks and backticks are noise on a
# 100-character line.
#
# Tab, newline and carriage return are deliberately **not** in here. Deleting
# them would glue the words on either side together ("Zeile einsZeile zwei"),
# which is what a model answering in a list would produce. They survive to the
# `split()` below, which turns them into the single space they should be.
MUELL = re.compile(r"[*_`#\\\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Some models keep emitting a reasoning block even with thinking switched off.
# Cheaper to drop it here than to rely on the model obeying.
DENKBLOCK = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)

# Openings that waste the budget without saying anything. Only stripped at the
# very start, and only followed by a colon -- "Die Antwort auf deine Frage
# haengt davon ab" is a real answer and stays.
VORSPANN = re.compile(
    r"^(antwort|die antwort( lautet)?|kurz( gesagt)?|kurzfassung|hier( ist)?( die antwort)?)\s*:\s*",
    re.IGNORECASE,
)

# Ein Befehlsverweis in der Modellantwort: "Frag !netz, das zaehlt nach." Das
# Argument endet am ersten Satzzeichen -- danach kommt die Begruendung, nicht
# mehr der Befehl.
#
# Das `!` muss am Anfang oder nach einem Leerzeichen stehen, der Name mindestens
# zwei Zeichen haben. Sonst liest "Wow!Super" ein Kommando namens "super" --
# harmlos, solange nur ausgefuehrt wird, den Befehl gibt es ja nicht. Aber
# `erfundener_befehl` wuerde eine tadellose Antwort deswegen verwerfen.
VERWEIS = re.compile(r"(?:^|\s)!([a-zA-ZäöüÄÖÜ]{2,})\s*([^,.!?;]*)")


def verweis(text: str) -> tuple[str, str] | None:
    """Nennt die Antwort einen Befehl? Dann als (Name, Argument) zurueck.

    Das Modell antwortet auf Netz- und Bergfragen mit "Frag !netz, das zaehlt
    nach" -- es weiss also, welches Werkzeug gefragt waere, es kann es nur nicht
    bedienen. Der Bot kann. Statt den Fragenden auf einen zweiten Befehl zu
    schicken, fuehrt er ihn selbst aus und funkt das gemessene Ergebnis.

    Hier wird nur erkannt, nicht entschieden. Welche Befehle ausgefuehrt werden
    duerfen, weiss der Aufrufer -- der loest auch die Aliase auf.
    """
    m = VERWEIS.search(text)
    if m is None:
        return None
    return m.group(1).lower(), m.group(2).strip()


def erfundener_befehl(text: str, bekannt) -> str | None:
    """Nennt die Antwort einen Befehl, den es gar nicht gibt?

    Beobachtet im Kanal: auf `wo liegt villach` antwortete das Modell mit
    `Frag !ort villach, das zeigt nach.` -- `!ort` existiert nicht. Wer der
    Empfehlung folgt, bekommt Stille, denn unbekannte Befehle beantwortet der
    Bot bewusst nicht, und haelt ihn fuer kaputt.

    Der Bot kennt seine eigene Befehlsliste. Diese Pruefung ist damit
    deterministisch -- und deterministisch geht vor Prompt, das hat sich hier
    schon zweimal gezeigt.
    """
    ziel = verweis(text)
    if ziel is None:
        return None
    name, _ = ziel
    return None if name in bekannt else name


# A sentence end: a dot followed by whitespace or the end of the text, and not
# preceded by a single letter.
#
# Both halves earn their keep. Without the lookahead, "2.5 km" ends a sentence
# after the 2. Without the lookbehind, "z.B." does -- and an answer cut there
# reads as though the bot broke off mid-thought.
#
# The lookbehind is two characters wide because that is what distinguishes the
# two cases: in "z.B." the dot is preceded by a letter that itself follows a dot
# or a space; in "Sicht." it is preceded by a letter that follows another letter.
SATZENDE = re.compile(r"(?<![.\s][A-Za-zÄÖÜäöü])[.!?](?=\s|$)")


# Sagt das Modell selbst, dass es passt? Dann lohnt ein Nachschlagen.
WEISS_NICHT = re.compile(r"wei(ss|ß) ich nicht", re.IGNORECASE)

# Woerter, die fuer den Abgleich Frage <-> Artikeltitel nichts hergeben.
FUELLWOERTER = frozenset((
    "was", "wer", "wie", "wo", "welche", "welcher", "welches", "wieviel",
    "viele", "viel", "ist", "sind", "war", "hat", "haben", "der", "die", "das",
    "ein", "eine", "einen", "und", "oder", "bei", "von", "vom", "fuer", "für",
    "bedeutet", "heisst", "heißt", "gibt", "steht", "stehen", "genau",
))


def weiss_nicht(text: str) -> bool:
    return WEISS_NICHT.search(text) is not None


def _woerter(text: str) -> set[str]:
    # Umlaute vereinheitlichen, sonst findet "woerthersee" den "Woerthersee"
    # nicht. Genau dieser Fall ist im Betrieb aufgetreten: der Artikel war da,
    # die Pruefung liess ihn durchfallen. Der Rest des Bots normalisiert
    # ebenso -- siehe die umlautfreie Ortssuche.
    roh = re.findall(r"[\wäöüß]+", transliterate(text.lower()))
    return {w for w in roh if len(w) >= 4 and w not in FUELLWOERTER}


def passt_zur_frage(frage: str, titel: str) -> bool:
    """Teilen Frage und gefundener Artikel ueberhaupt ein Wort?

    Die Volltextsuche der Wikipedia liefert bei Fachbegriffen Unsinn, und zwar
    ohne jedes Zeichen von Unsicherheit: die Suche nach "Spreading Factor LoRa"
    ergab **Rheumatoide Arthritis** und **Elon Musk**. Einen solchen Treffer
    ungeprueft zu funken waere schlimmer als das ehrliche "weiss ich nicht",
    das er ersetzen soll.

    Ein gemeinsames Wort ist eine grobe Huerde, aber eine deterministische --
    und sie haette beide Fehlgriffe gestoppt, waehrend sie "Einwohner
    Klagenfurt" gegen "Klagenfurt am Woerthersee" durchlaesst.
    """
    return bool(_woerter(frage) & _woerter(titel))


async def nachschlagen(client: httpx.AsyncClient, settings, frage: str,
                       grenze: int | None = None) -> str | None:
    """Erster Satz des passendsten Wikipedia-Artikels, oder nichts.

    Bewusst **ohne** zweiten Modelldurchlauf: der Text wird nicht
    zusammengefasst, sondern abgeschnitten. Eine Zusammenfassung durch dasselbe
    4B-Modell brächte genau das zurück, was hier vermieden werden soll -- nur
    diesmal mit einer Quelle daneben, die es glaubwuerdig aussehen laesst.

    Wirft nie. Jeder Fehler bedeutet: es bleibt beim "weiss ich nicht".
    """
    try:
        resp = await client.get(
            settings.wikipedia_such_url,
            params={"action": "query", "format": "json", "list": "search",
                    "srlimit": 1, "srsearch": frage},
            timeout=settings.wikipedia_timeout_s,
        )
        resp.raise_for_status()
        treffer = resp.json().get("query", {}).get("search", [])
        if not treffer:
            return None
        titel = treffer[0]["title"]
        if not passt_zur_frage(frage, titel):
            return None

        resp = await client.get(
            settings.wikipedia_auszug_url.format(titel=titel.replace(" ", "_")),
            timeout=settings.wikipedia_timeout_s,
        )
        resp.raise_for_status()
        daten = resp.json()
    except Exception:
        return None

    # Eine Begriffsklaerung ist keine Antwort, sondern eine Rueckfrage.
    if daten.get("type") == "disambiguation":
        return None
    auszug = " ".join(str(daten.get("extract", "")).split())
    if not auszug:
        return None
    m = SATZENDE.search(auszug)
    satz = auszug[: m.end()] if m else auszug
    # Der erste Satz eines Artikels ist oft laenger als eine Funknachricht:
    # "Klagenfurt am Woerthersee ist eine Grossstadt im Sueden Oesterreichs
    # sowie die Landeshauptstadt ..." sind 145 Zeichen. Ohne Kuerzung hier
    # schneidet clamp() ihn spaeter mitten durch.
    return kuerzen_am_satzende(satz, grenze) if grenze is not None else satz


def budget(settings) -> int:
    """Characters left to the model once the `KI:` prefix is paid for."""
    return settings.nutzlimit - len(settings.frag_praefix) - 1


def probe_url(settings) -> str:
    """`.../api/chat` -> `.../api/version`.

    Deliberately `version` and not `tags` or a real request: it answers without
    touching a model, so the watchdog costs no CPU on a machine that is busy
    with other things.
    """
    basis = settings.ollama_url.split("/api/")[0].rstrip("/")
    return f"{basis}/api/version"


async def erreichbar(client: httpx.AsyncClient, settings) -> bool:
    """Is the inference host answering at all? Never raises."""
    try:
        resp = await client.get(probe_url(settings), timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def kuerzen_am_satzende(text: str, grenze: int) -> str:
    """Drop whole sentences rather than cutting through one.

    `clamp()` in the router is the guarantee that the message fits; this is the
    attempt to make what fits readable. A model that answers in three sentences
    when it was asked for one gets its last sentences dropped instead of its
    last words, which reads like a short answer rather than like a broken one.

    Only applied when at least half the budget survives. Otherwise a first
    sentence of "Ja." would throw away eighty perfectly good characters that
    `clamp()` would have kept.
    """
    if len(text) <= grenze:
        return text
    letztes = None
    for treffer in SATZENDE.finditer(text[:grenze]):
        letztes = treffer
    if letztes is not None and letztes.end() >= grenze * 0.5:
        return text[: letztes.end()]
    return text


def systemprompt(grenze: int) -> str:
    """The instruction the model gets. Short on purpose.

    Every token here is paid for twice: once in prefill time on a CPU, and once
    in the context the model has to hold. Long style guides make the answer
    slower without making it shorter.

    The character limit is stated as a number rather than as "kurz" because
    models treat "kurz" as a suggestion and a digit as a rule. It is not a
    guarantee either -- `clamp()` in the router remains the actual enforcement.
    """
    return (
        "Du beantwortest Fragen im Funknetz CarinthiaMesh. "
        f"Antworte auf Deutsch, in einer einzigen Zeile, hoechstens {grenze} Zeichen. "
        "Kein Markdown, keine Aufzaehlung, kein Vorspann, keine Rueckfrage. "
        "Nenne nur die Antwort selbst. "
        "Wenn du es nicht sicher weisst, antworte genau: weiss ich nicht. "
        "Zum Zustand dieses Netzes und zu Hoehen in der Region nennst du keine "
        "eigenen Zahlen, sondern verweist auf den Befehl, der es misst. "
        # Alles Weitere sind Beispiele, und das ist Absicht.
        #
        # Gemessen: Regeln in Prosa ueberliest dieses Modell, Beispiele befolgt es.
        # Eine Fassung mit einem Fachglossar als Fliesstext und einer Liste
        # erlaubter Befehle war messbar schlechter als gar keine -- das Modell
        # antwortete auf "was ist SNR" mit "Frag !netz, das erklaert
        # Signal-Rausch-Abstand" und haengte denselben Verweis an die
        # Einwohnerzahl von Klagenfurt. Es kopiert das haeufigste Muster im
        # Prompt, statt zwischen Regeln abzuwaegen.
        #
        # Daher: kein Glossar in Prosa, keine Befehlsliste. Die Fachbegriffe, die
        # im Kanal tatsaechlich gefragt wurden, stehen als Frage-Antwort-Paare
        # da, und die Mehrheit der Beispiele beantwortet, statt zu verweisen --
        # sonst wird Verweisen zum Standardverhalten.
        "So sehen richtige Antworten aus. "
        "Frage: Was bedeutet SF8? "
        "Antwort: Spreading Factor bei LoRa, hoeher heisst mehr Reichweite, aber langsamer. "
        "Frage: Was bedeutet SNR? "
        "Antwort: Signal-Rausch-Abstand in dB, hoeher ist besser. "
        "Frage: Was ist RSSI? "
        "Antwort: Empfangspegel in dBm, naeher an null ist besser. "
        "Frage: Was bedeutet Duty Cycle? "
        "Antwort: Erlaubter Sendezeitanteil, im 868-MHz-Band 10 Prozent pro Stunde. "
        "Frage: Wie viele Einwohner hat Graz? "
        "Antwort: Rund 300.000. "
        "Frage: Wer war Nikola Tesla? "
        "Antwort: Erfinder und Elektroingenieur, 1856-1943, Pionier des Wechselstroms. "
        "Frage: Wie viele Repeater gibt es in Kaernten? "
        "Antwort: Frag !netz, das zaehlt nach. "
        "Frage: Wie hoch ist der Gerlitzen? "
        "Antwort: Frag !gipfel gerlitzen, das misst nach. "
        # Ortsfragen. Im Kanal beobachtet: "wo liegt klagenfurt" wurde mit
        # "Liegt im Burgenland" beantwortet, "in welchem bundesland liegt
        # villach" mit "Slovenien". Beides selbstsicher und falsch.
        #
        # Das Ziel ist hier nicht die richtige Antwort, sondern das Eingestaendnis:
        # sagt das Modell "weiss ich nicht", greift der Wikipedia-Rueckfall und
        # der beantwortet genau diese Frageform richtig.
        "Frage: Wo liegt Villach? "
        "Antwort: weiss ich nicht."
        # Hier stand ein Gegenbeispiel ("Welche Repeater stehen auf dem
        # Dobratsch? -- weiss ich nicht"), das den Fehlgriff auf !netz
        # verhindern sollte. Gemessen: wirkungslos, das Modell verwies weiter
        # auf !netz. Wieder entfernt, weil jedes Beispiel Prefill-Zeit bei
        # jeder einzelnen Anfrage kostet.
        #
        # Diese Unterscheidung -- "wie viele" gegen "welche" -- liegt jetzt im
        # Code, siehe `Bot._frag_ausfuehren`. Was sich deterministisch pruefen
        # laesst, gehoert nicht in einen Prompt.
    )


async def fetch(client: httpx.AsyncClient, settings, frage: str) -> str:
    """One request to Ollama. Raises on anything unusable — the caller stays silent.

    Deliberately not routed through `Bot._mit_retry`: with `http_retries=1` a
    timeout would turn 20 seconds into 40, and by then the answer is worthless
    anyway. One attempt, then nothing.

    `num_predict` is the real cost ceiling. It bounds not just the length of the
    answer but the time the machine spends on it, so a model that ignores the
    system prompt and starts an essay cannot occupy the service.
    """
    grenze = budget(settings)
    resp = await client.post(
        settings.ollama_url,
        json={
            "model": settings.frag_model,
            "stream": False,
            # Qwen-style models reason by default. Thinking tokens are generated
            # at the same speed as any other and would multiply the latency for
            # a one-line answer.
            "think": False,
            "options": {
                "num_predict": settings.frag_num_predict,
                # Low, not zero: zero makes small models repeat themselves when
                # a question has no good answer.
                "temperature": 0.3,
            },
            "messages": [
                {"role": "system", "content": systemprompt(grenze)},
                {"role": "user", "content": frage},
            ],
        },
        timeout=settings.frag_timeout_s,
    )
    resp.raise_for_status()
    daten = resp.json()
    # Ollama reports several of its own failures as HTTP 200 with an `error`
    # field -- a model that cannot be loaded, or one the OOM killer took down
    # mid-request. `raise_for_status` sees nothing wrong with those, and without
    # this check the caller logs "leere Antwort vom Modell" for what is really
    # "the machine ran out of memory". Both end in silence on the air; only one
    # of them tells you where to look.
    fehler = daten.get("error")
    if fehler:
        raise ValueError(f"Ollama meldet: {fehler}")
    text = str(daten.get("message", {}).get("content", "")).strip()
    if not text:
        raise ValueError("leere Antwort vom Modell")
    return text


def render(text: str, praefix: str, grenze: int | None = None) -> str:
    """Model output to a line fit for the air.

    Order matters: the reasoning block goes first (it may contain the very
    characters removed in the next step), then the junk, then the preamble --
    which can only be recognised once the markdown around it is gone. The
    sentence trim comes last, on clean text, because a stray markdown dot would
    otherwise pass for a sentence end.

    `grenze` is optional so the scrubbing can be tested on its own. In
    operation it is always given: without it an over-long answer falls through
    to `clamp()` and gets cut mid-sentence.
    """
    text = DENKBLOCK.sub("", text)
    text = MUELL.sub("", text)
    text = " ".join(text.split())
    text = VORSPANN.sub("", text).strip(" \"'")
    if not text:
        raise ValueError("nach dem Aufraeumen blieb nichts uebrig")
    if grenze is not None:
        text = kuerzen_am_satzende(text, grenze)
    return f"{praefix} {text}"
