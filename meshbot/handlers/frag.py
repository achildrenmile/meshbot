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
        # The measured weak spot. Asked for the height of the Dobratsch (2166 m),
        # the installed models answered 412, 711, 1047, 1586, 1743 and 2764 --
        # six different wrong numbers, none of them flagged as uncertain. Local
        # facts are also exactly what gets asked on a Carinthian mesh.
        #
        # So the command is steered away from them. That is not a loss: the bot
        # already answers those from measurement, and a pointer to !gipfel is
        # worth more than a confident invention. What stays is what the models
        # are actually good at -- explaining a term.
        # The rule names no commands on purpose. An earlier version listed
        # !gipfel/!hoehe/!dist/!wx and gemma2:2b simply parroted the list: a
        # question about radio propagation came back as "Frag !dist 868 MHz",
        # and "Was bedeutet Fresnelzone?" as "Frag !Fresnelzone" -- an invented
        # command. The model was pattern-matching the list instead of applying
        # the rule, and lost the term explanations it had been good at.
        #
        # One example below carries the pointer instead. Showing it once works;
        # listing the options turns the command into a deflection machine.
        "Zu Hoehen und Entfernungen nennst du keine eigenen Zahlen -- die misst "
        "dieser Bot selbst. Begriffe erklaerst du dagegen normal. "
        # Measured: without few-shot examples gemma2:2b kept to the character
        # budget in two of three questions, with them in four of four. Roughly
        # eighty prompt tokens, which prefill handles in well under a second.
        #
        # The first example demonstrates the refusal, because showing the form is
        # more effective on a small model than describing it.
        "So sehen richtige Antworten aus. "
        "Frage: Wie hoch ist der Gerlitzen? "
        "Antwort: Frag !gipfel gerlitzen, das misst nach. "
        "Frage: Was bedeutet SNR? "
        "Antwort: Signal-Rausch-Abstand in dB, hoeher ist besser. "
        "Frage: Wer war Nikola Tesla? "
        "Antwort: Erfinder und Elektroingenieur, 1856-1943, Pionier des Wechselstroms."
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
