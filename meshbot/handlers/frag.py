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

from ..formatting import clamp, transliterate, utf8_len

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
    """`.../v1/chat/completions` -> `.../health/liveliness`.

    LiteLLM's liveliness probe (docs.litellm.ai/docs/proxy/health): answers
    without touching a model or the database, unauthenticated, so the
    watchdog costs no GPU time on a machine that is busy with other things.
    Deliberately not `/health/readiness` -- that one *does* check the
    gateway's own database connection, which is a different failure mode than
    "is the gateway process up at all".
    """
    basis = settings.ollama_url.split("/v1/")[0].rstrip("/")
    return f"{basis}/health/liveliness"


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


def laengenregeln(grenze: int) -> str:
    """The length and form rules, shared by both prompts (plain and tool loop).

    The model overshoots a stated limit, so the code checks and re-asks
    (`antworten`); this text only makes the first attempt likelier to fit.
    A joke is the worst case: setup and punchline must both fit, and a cut
    joke is worse than no joke -- hence the explicit "shorter joke" rule.
    """
    ziel = max(20, int(grenze * 0.85))
    return (
        f"Deine Antwort ist ein vollstaendiger Satz oder Witz mit hoechstens {grenze} Zeichen, "
        f"am besten um die {ziel}. Sie wird nicht gekuerzt und darf nie mittendrin enden. "
        "Keine Anfuehrungszeichen, kein Markdown. "
        "Bei Witzen: ein ganz kurzer Witz, Aufbau und Pointe zusammen innerhalb der Grenze, "
        "die Pointe ist Pflicht. Lieber ein kuerzerer Witz als eine fehlende Pointe. "
        "Erfinde bei Witzen einen neuen, wiederhole die Beispiele nicht. "
    )


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
        "Antworte auf Deutsch, in einer einzigen Zeile. "
        "Keine Aufzaehlung, kein Vorspann, keine Rueckfrage. "
        "Nenne nur die Antwort selbst. "
        + laengenregeln(grenze) +
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
        "Frage: Erzaehl einen Witz. "
        "Antwort: Was macht die Antenne beim Arzt? Sie klagt ueber Stehwellen. "
        "Frage: Kurzer Funkerwitz. "
        "Antwort: Warum ist der Funker so ruhig? Er hat Sendepause. "
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


class Roh(str):
    """Model output plus whether the gateway reported it as cut off.

    A str, so everything that treated the answer as text keeps working.
    OpenAI schema (via LiteLLM): `finish_reason == "length"` means the token
    ceiling hit mid-answer -- the text is unusable as it stands.
    """

    abgeschnitten: bool = False


def als_roh(daten: dict) -> Roh:
    wahl = (daten.get("choices") or [{}])[0]
    text = Roh(str(wahl.get("message", {}).get("content", "") or "").strip())
    text.abgeschnitten = wahl.get("finish_reason") == "length"
    return text


async def fetch(client: httpx.AsyncClient, settings, frage: str,
                grenze: int | None = None, vorher: str | None = None) -> Roh:
    """One request through the LiteLLM gateway. Raises on anything unusable --
    the caller stays silent.

    Deliberately not routed through `Bot._mit_retry`: with `http_retries=1` a
    timeout would turn 20 seconds into 40, and by then the answer is worthless
    anyway. The gateway itself also has `num_retries: 0` for the same reason
    (docs/llm-gateway.md in dcsetup) -- there is exactly one GPU behind it.
    Re-asking about the *length* is a different thing and lives in `antworten`.

    `max_tokens` is the real cost ceiling in the OpenAI schema (LiteLLM maps it
    to Ollama's native `num_predict`). `vorher` is a previous answer that was
    too long: it goes back as context together with a stricter limit.

    No `think` field here on purpose: verified (2026-09-27) that LiteLLM's
    `ollama_chat` provider does not translate it.
    """
    grenze = grenze if grenze is not None else budget(settings)
    headers = {"Authorization": f"Bearer {settings.litellm_api_key}"} if settings.litellm_api_key else {}
    nachrichten = [
        {"role": "system", "content": systemprompt(grenze)},
        {"role": "user", "content": frage},
    ]
    if vorher is not None:
        nachrichten += [
            {"role": "assistant", "content": vorher},
            {"role": "user", "content": (
                f"Das war zu lang ({len(vorher)} Zeichen). Schreibe es neu mit hoechstens "
                f"{grenze} Zeichen, als vollstaendigen Satz, bei einem Witz mit Pointe, "
                "ohne Anfuehrungszeichen. Nur den Text.")},
        ]
    resp = await client.post(
        settings.ollama_url,
        headers=headers,
        json={
            "model": settings.frag_model,
            "stream": False,
            "max_tokens": settings.frag_num_predict,
            # Low, not zero: zero makes small models repeat themselves when
            # a question has no good answer. Jokes get more, otherwise the
            # model tells the example from the prompt every time.
            "temperature": 0.8 if "witz" in frage.lower() else 0.3,
            "messages": nachrichten,
        },
        timeout=settings.frag_timeout_s,
    )
    resp.raise_for_status()
    text = als_roh(resp.json())
    if not text:
        raise ValueError("leere Antwort vom Modell")
    return text


# "90 Zeichen", "max 80 Buchstaben", "in 60 chars" -- the asker's own limit.
LAENGENHINWEIS = re.compile(
    r"\b(?:(?:max(?:imal)?\.?|hoechstens|höchstens|bis(?: zu)?|in|unter|ca\.?|etwa)\s+)?"
    r"(\d{1,3})\s*(?:zeichen|buchstaben|chars?|characters)\b", re.IGNORECASE)

MIN_GRENZE = 20
RETRIES = 2
# Per retry the limit given to the model shrinks to this share of the original.
STRENGER = (0.8, 0.65)


def laengenhinweis(frage: str) -> tuple[str, int | None]:
    """Split "Witz 90 Zeichen" into ("Witz", 90). The hint is not part of the question."""
    m = LAENGENHINWEIS.search(frage)
    if m is None:
        return frage, None
    rest = " ".join((frage[: m.start()] + " " + frage[m.end():]).split())
    return rest, int(m.group(1))


def grenze_fuer(settings, hinweis: int | None = None) -> int:
    """Characters for the model text. The asker's number is the size of the
    whole line on his screen, so the prefix comes off; never above the budget."""
    g = budget(settings)
    if hinweis is not None:
        g = min(g, max(MIN_GRENZE, hinweis - len(settings.frag_praefix) - 1))
    return g


def budget_bytes(settings) -> int:
    """Bytes left to the model after the node name and the `KI:` prefix."""
    return settings.nutzlimit_bytes - utf8_len(settings.frag_praefix + " ")


def passt(text: str, grenze: int, grenze_bytes: int) -> bool:
    return len(text) <= grenze and utf8_len(text) <= grenze_bytes


def bereinigen(text: str) -> str:
    """Scrub model output: reasoning block, junk, preamble, quotation marks."""
    text = DENKBLOCK.sub("", text)
    text = MUELL.sub("", text)
    text = " ".join(text.split())
    text = VORSPANN.sub("", text).strip(" \"'„“”‚‘’»«")
    if not text:
        raise ValueError("nach dem Aufraeumen blieb nichts uebrig")
    return text


async def antworten(client: httpx.AsyncClient, settings, roh: str, frage: str,
                    grenze: int | None = None, nachfragen: bool = True) -> str:
    """Raw model output to the finished line, guaranteed to fit.

    Too long (characters or UTF-8 bytes) or cut off by the token ceiling: ask
    again, at most `RETRIES` times, each time with a stricter limit and the
    too-long answer as context. Only then trim -- whole sentences first, then at
    a word boundary with "…", never through a multibyte character.
    """
    grenze = grenze if grenze is not None else budget(settings)
    gbytes = budget_bytes(settings)
    text = bereinigen(roh)

    def zu_lang() -> bool:
        return getattr(roh, "abgeschnitten", False) or not passt(text, grenze, gbytes)

    for versuch in range(RETRIES if nachfragen else 0):
        if not zu_lang():
            break
        strenger = max(MIN_GRENZE, int(grenze * STRENGER[versuch]))
        try:
            roh = await fetch(client, settings, frage, strenger, vorher=text)
            text = bereinigen(roh)
        except Exception:
            break
    if zu_lang():
        text = kuerzen_am_satzende(text, grenze)
        text = clamp(text, grenze, gbytes)
    return f"{settings.frag_praefix} {text}"


def render(text: str, praefix: str, grenze: int | None = None) -> str:
    """Model output to a line fit for the air.

    Order matters: the reasoning block goes first (it may contain the very
    characters removed in the next step), then the junk, then the preamble --
    which can only be recognised once the markdown around it is gone. The
    sentence trim comes last, on clean text, because a stray markdown dot would
    otherwise pass for a sentence end.

    Plain scrub plus sentence trim. Operation goes through `antworten`, which
    re-asks before it trims anything.
    """
    text = bereinigen(text)
    if grenze is not None:
        text = kuerzen_am_satzende(text, grenze)
    return f"{praefix} {text}"
