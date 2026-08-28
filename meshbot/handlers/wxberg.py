"""Summit weather — the `!gipfel` command, and `!wx` for unambiguous names.

GeoSphere's weather stations end at the national border, and there is rarely one
on a summit anyway. Mountains therefore get a **model value** from Open-Meteo,
computed at summit elevation.

**That is not a measurement, and the answer says so** -- it ends in `(Modell)`.
This is more honest than a number that looks like a thermometer at the summit
cross. Someone who misses the difference plans a tour on a model and takes it
for a measurement.

The summit directory comes from the SOTA list and covers Austria, Italy,
Slovenia, Germany, Switzerland, Croatia, Czechia, Slovakia, Hungary and Poland
-> `tools/build_gipfel.py`.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

import httpx

from .wx import normalisiere

# Wind direction as in !wx, so both answers read the same way.
RICHTUNGEN = ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

# No air pressure. Open-Meteo reports it as `pressure_msl`, reduced to sea
# level -- on a summit that is not a value from up there but a regional number
# that looks the same everywhere. It cost nine characters and pushed the answer
# past 60, which is exactly where the delivery rate on the network drops from
# 92 to 47 per cent.
FELDER = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
          "wind_direction_10m")

# The SOTA list names the **highest point**, local usage names the **massif**.
# Someone typing "Koralpe" means the Grosser Speikkogel -- there is nothing
# under "Koralpe". Without this table such a query lands in the similarity
# search and comes back with some unrelated mountain.
#
# Verified entries only: every key on the right must exist in the directory,
# otherwise the lookup silently falls back to guessing.
ALIASE = {
    "koralpe": "grosser speikkogel",
    "koralm": "grosser speikkogel",
    "saualpe": "ladinger spitz",
    "kellerwand": "hohe warte",
    "coglians": "hohe warte",
    # Single-word names where the word boundary does not help: "Glockner" sits
    # inside "Grossglockner", "Obir" inside "Hochobir". Multi-word names such as
    # "Grosser Hafner" or "Hoher Sonnblick" need no alias -- the whole-word
    # stage finds those by itself.
    "glockner": "grossglockner",
    "obir": "hochobir",
    # The Hochstuhl is in the list under its Slovenian name (S5/KA-001,
    # 2236 m). Same mountain; the border runs across it.
    "hochstuhl": "stol",
    "stou": "stol",
}

# Mountains that exist -- just not in the SOTA list. It only carries summits
# with enough prominence; Carinthia has 282 entries there, and Petzen, Kornock,
# Falkert and the Koschuta are not among them.
#
# Without this table the similarity search guesses: "Petzen" became "Pletzen"
# (a different mountain), "Kornock" became "Koflernock". An alias does not help
# -- there is nothing for it to point at. So tell the truth instead.
#
# Only what is genuinely missing belongs here: the Hochstuhl looked like a
# candidate for a long time and is in fact present, under "Stol". A wrong entry
# here locks out a summit that exists -- which is why a test checks every name
# against the directory.
FEHLT = {"petzen", "peca", "kornock", "falkert", "koschuta", "kosuta"}


def index(gipfel: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Name directory, normalised like the place directory.

    Several summits share names -- on a duplicate the higher one wins. Someone
    asking for "Hochwart" means the mountain, not the hillock next to it.
    """
    aus: dict[str, dict[str, Any]] = {}
    for g in gipfel:
        for teil in _namensvarianten(g["name"]):
            k = normalisiere(teil)
            if not k:
                continue
            alt = aus.get(k)
            if alt is None or (g.get("alt") or 0) > (alt.get("alt") or 0):
                aus[k] = g
    return aus


def _namensvarianten(name: str) -> list[str]:
    """`Villacher Alpe (Dobratsch)` is findable under either name.

    The same goes for `Matterhorn/Mont Cervin/Monte Cervino` and for hyphenated
    double names: whoever types the Slovenian or Italian name means the same
    mountain.
    """
    varianten = [name]
    if "(" in name and ")" in name:
        vorne = name[: name.index("(")].strip()
        drin = name[name.index("(") + 1: name.rindex(")")].strip()
        varianten += [vorne, drin]
    for trenner in ("/", " – ", " - "):
        if trenner in name:
            varianten += [t.strip() for t in name.split(trenner)]
    return [v for v in varianten if v]


def suche_stufe(verzeichnis: dict[str, dict[str, Any]], begriff: str,
                fuzzy: bool = True) -> tuple[dict[str, Any] | None, str]:
    """Exact, then whole word, then similarity -- and reports which stage hit.

    The stage is `exakt`, `wort`, `geraten` or `keiner`. The first two are
    knowledge; `geraten` is a guess and is marked as such in the answer.

    **Whole word, not substring.** It used to be enough for the query to appear
    anywhere in the name -- including mid-word. That turned "Eckwand" into
    "Bl-eckwand" and "Lienz" into "Sandegg - Lienz-er". Measured against two
    dozen real summit queries the stricter rule costs exactly one hit
    (`glockner` -> Grossglockner); every other query matched exactly anyway.
    """
    k = normalisiere(begriff)
    if not k:
        return None, "keiner"
    if k in FEHLT:
        return None, "fehlt"
    k = ALIASE.get(k, k)
    if k in verzeichnis:
        return verzeichnis[k], "exakt"
    treffer = [g for name, g in verzeichnis.items() if f" {k} " in f" {name} "]
    if treffer:
        return max(treffer, key=lambda g: g.get("alt") or 0), "wort"
    if not fuzzy:
        return None, "keiner"
    nah = get_close_matches(k, list(verzeichnis), n=1, cutoff=0.82)
    return (verzeichnis[nah[0]], "geraten") if nah else (None, "keiner")


def suche(verzeichnis: dict[str, dict[str, Any]], begriff: str,
          fuzzy: bool = True) -> dict[str, Any] | None:
    """Like `suche_stufe` without the stage -- for callers that do not need it."""
    return suche_stufe(verzeichnis, begriff, fuzzy)[0]


def station_am_gipfel(stationen: list[dict[str, Any]], gipfel: dict[str, Any],
                      km: float = 3.0, hoehendiff: float = 300.0) -> dict[str, Any] | None:
    """Is there a weather station practically on this summit?

    On a few mountains somebody really does measure -- on the Dobratsch the
    station "Villacher Alpe" stands 200 m from the summit cross and 49 m lower.
    Computing a model there would be absurd: **measured beats computed.**

    Both conditions must hold. Proximity alone is not enough -- a valley station
    can be close in a straight line and still lie 1500 m lower, where it
    measures different weather.
    """
    bester = None
    for st in stationen:
        if st.get("hoehe") is None:
            continue
        if abs(st["hoehe"] - gipfel["alt"]) > hoehendiff:
            continue
        d = _entfernung_km(gipfel["lat"], gipfel["lon"], st["lat"], st["lon"])
        if d <= km and (bester is None or d < bester[0]):
            bester = (d, st)
    return bester[1] if bester else None


def _entfernung_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


async def fetch(client: httpx.AsyncClient, url: str, gipfel: dict[str, Any]) -> dict[str, Any]:
    """Model values for the summit position, computed at summit elevation.

    `elevation` is the whole point: without that parameter the model answers for
    the mean elevation of its grid cell, which on a summit is routinely several
    hundred metres too low.
    """
    r = await client.get(url, params={
        "latitude": gipfel["lat"], "longitude": gipfel["lon"],
        "elevation": gipfel["alt"], "current": ",".join(FELDER),
        "wind_speed_unit": "kmh",
    }, timeout=20.0)
    r.raise_for_status()
    daten = r.json().get("current") or {}
    if daten.get("temperature_2m") is None:
        raise ValueError("Modell ohne Temperatur")
    return daten


def render_unbekannt(begriff: str) -> str:
    """No summit by that name -- and that is useful information.

    The SOTA list only carries summits with enough prominence; Carinthia has 282
    entries. Petzen, Kornock and Falkert are missing from it. This used to
    return the most similar foreign mountain; now it returns the truth plus a
    way to get values anyway.
    """
    name = " ".join(begriff.split())[:20] or "Nix"
    if normalisiere(begriff) in FEHLT:
        # Not a typo but a gap in the source. That deserves a different answer
        # than "never heard of it".
        return f"{name.title()}: fehlt der SOTA-Liste. Mit Position gehts: !wx 46.6 13.8"
    return f"{name.title()}: kein Gipfel in der SOTA-Liste. Position geht: !wx 46.6 13.8"


def kurzname(name: str, grenze: int = 22) -> str:
    """Contract long double names down to the mountain name.

    `Punta Penia – Marmolada` or `Matterhorn/Mont Cervin/Monte Cervino` would
    otherwise blow the message. Hard truncation is not an option -- that yields
    `Punta Penia – Marmolad`, which is not a mountain but a typo. The
    **shortest** of the name variants is used instead: usually the name the
    mountain is known by.
    """
    if len(name) <= grenze:
        return name
    varianten = [v for v in _namensvarianten(name) if 3 < len(v) <= grenze]
    return min(varianten, key=len) if varianten else name[:grenze].rsplit(" ", 1)[0]


def render(gipfel: dict[str, Any], w: dict[str, Any], stale: bool = False,
           geraten: bool = False) -> str:
    """One line, same order as !wx -- plus elevation and the model marker.

    `geraten` appends a question mark to the mountain name. The SOTA list does
    not know every mountain -- Petzen, Kornock and Falkert are missing entirely.
    For those the similarity search used to silently find a foreign summit:
    "Kornock" became "Koflernock", "Petzen" became "Pletzen".
    """
    marker = "~" if stale else ""
    name = kurzname(gipfel["name"]) + ("?" if geraten else "")
    teile = [f"WX {name} {gipfel['alt']}m: {marker}"]
    werte = []
    if w.get("temperature_2m") is not None:
        werte.append(f"{w['temperature_2m']:.1f}C")
    if w.get("relative_humidity_2m") is not None:
        werte.append(f"{int(w['relative_humidity_2m'])}%")
    if w.get("wind_speed_10m") is not None:
        wind = f"Wind {w['wind_speed_10m']:.0f}km/h"
        if w.get("wind_direction_10m") is not None:
            wind += " " + RICHTUNGEN[round(w["wind_direction_10m"] / 22.5) % 16]
        werte.append(wind)
    return teile[0] + ", ".join(werte) + " (Modell)"
