# MeshBot — a command bot for CarinthiaMesh

Listens on a MeshCore channel, recognises commands with a `!` prefix, fetches data
from the outside world and answers **in a single short message**.

Channel: **`#at-ktn-bot`**, slot 3 on the node.

The bot does not attach to the radio. It attaches to the MQTT broker of the existing
[meshinfra](https://github.com/achildrenmile/meshinfra) stack, so it is not another
TCP client on the node — a companion only tolerates two of those.

> The bot answers in **German**: it serves a Carinthian radio network, and that is
> the language on the channel. This README is English so the design is readable to
> anyone; the code comments and the wiki stay German.

---

## The one rule

**Airtime is the scarcest resource on the network.** Everything else follows from it:

- Answers are capped at **140 characters**, hard, before sending
- **On request only**, never unprompted
- **At most 12 answers per 10 minutes** network-wide, **4 commands per 5 minutes** per
  sender
- Over the limit, unknown command, or duplicate: **silence**. A refusal costs exactly
  as much airtime as an answer
- Delivery goes through meshinfra's **existing rate-limit gate** (`tx/chan`), not
  around it — that gate's own brakes apply on top
- Both limits are **token buckets**: `limit` answers back to back, then one every
  `window/limit` seconds. Globally 12 at once, then one every 50 s; per sender 4 at
  once, then one every 75 s

**The character limit is not the whole story.** The node prefixes its own name
(`AT-VI-KFHQ: `), and those characters count towards the firmware limit even though
the bot never sees them. `SENDER_RESERVE` (24) keeps room for them; without it the
node rejects the finished message with `error_code 2` and the answer vanishes without
a trace. The bot's usable budget is `MAX_MSG_LEN - SENDER_RESERVE`.

## Commands

| Command | Alias | Answer |
|---|---|---|
| `!wx <place\|lat lon>` | `!wetter` | `WX Villach: 31.8C, 32%, Wind 12km/h W, 956hPa` |
| `!wx <place abroad>` | | `WX Lienz (AT): 21.9C, 77%, Wind 1km/h NNO (Modell)` |
| `!gipfel <summit>` | `!berg` | `WX Triglav 2864m: 9.0C, 79%, Wind 7km/h WNW (Modell)` |
| `!warn [place\|lat lon]` | `!warnung` | `WARN Waidegg (Kirchbach): GELB Gewitter (bis 22:00)` |
| `!vorhersage <place>` | `!morgen`, `!fc` | `24h Villach: 18 bis 26C, 11mm Regen, Boeen 38km/h` |
| `!lawine` | `!avalanche` | `Lawine KTN: Stufe 3 erheblich (ab Waldgrenze)` |
| `!sota <ref>` | `!summit` | `OE/KT-048 Rinsennock 2334m, 10Pkt` |
| `!sota <lat> <lon>` | | `OE/KT-072 Villacher Alpe 2166m 8Pkt (88m NW) \| …` |
| `!az <lat lon>` | `!zone` | `AZ OE/KT-048 Rinsennock 2334m: JA - 30m bis zum Rand, 10Pkt` |
| `!spot [assoc]` | `!spots` | `OE8XXX OE/KT-048 14.062 CW 12min` |
| `!sonne [place\|lat lon]` | `!sun` | `Sonne: auf 06:04, unter 20:15, dunkel 20:48 (noch 1h03)` |
| `!mond [place\|lat lon]` | `!moon` | `Mond: auf 10:28, unter 21:33, zunehmend 17%` |
| `!sicht <lat,lon> <lat,lon>` | `!los` | `Sicht 18.4km: FREI, Fresnel 100% (enger bei km17.5, 1347m)` |
| `!hoehe <lat,lon>` | `!seehoehe` | `Hoehe 46.6719,13.8902: 1478m (EU-DEM 25m)` |
| `!dist <lat,lon> <lat,lon>` | `!entfernung` | `37.9km, Peilung 312 NW (zurueck 132 SO)` |
| `!qth <locator\|lat lon>` | `!loc` | Maidenhead to coordinates and back |
| `!netz` | `!status` | `Netz KTN: 29/33 aktiv, Pakete 2578/1h, top WO-Poelling` |
| `!wo <name\|hash>` | `!node`, `!pfad` | Position, traffic and last contact of a node |
| `!relais <band> [place\|lat lon]` | `!rpt` | `2m b. Villach: OE8XNK Gerlitzen 145.7625 -0.6 (10km) \| …` |
| `!melde <what, where>` | `!luecke` | Record a field report, position optional |
| `!dx` | `!solar` | `DX: SFI 117, A6, K0, SN 83, Xray C1.3` |
| `!iss [lat lon]` | `!sat` | `ISS 05:44 max 24Grad, NNO>W, 6min` |
| `!zeit` | `!time`, `!utc` | `UTC 16.08.2026 17:11:53 (Epoch 1786900313)` |
| `!quota` | `!kontingent`, `!rest` | `Kontingent: 44/50 pro 1h00 frei. Bot 12/12 pro 10min` |
| `!ping` | | `MeshBot OK, up 3d4h, 42 cmds` |
| `!frag <question>` | `!frage`, `!ask`, `!ki` | `KI: Ein Repeater verstärkt und weiterleitet Signale im Funknetz.` |
| `!version` | `!ver`, `!stand` | `MeshBot 1.6.0: !frag beantwortet freie Fragen, Antwort mit KI: markiert` |
| `!help [cmd\|group]` | `!hilfe` | Overview; with a command, the details |

Without a place, `!wx` and `!relais` use the default location from the configuration.
Typos are tolerated (`!wx vilach` finds Villach) — and **flagged**, see below.

**A missing argument produces the usage line, not silence.** Someone who typed a
command correctly and merely forgot an argument has not produced garbage. The airtime
is better spent on `!sicht <lat,lon> <lat,lon> - z.B. !sicht 46.60,13.67 46.79,14.96`
than on a second round of guessing. An *unknown* command still gets silence.

## What gets found, and in which order

Five stages. The rule behind them is the same one twice: **measured beats computed,
certain beats guessed.**

| | Directory | Match | Answer |
|---|---|---|---|
| 1. | Carinthian places | exact | measurement from one of 34 stations |
| 2. | Summits | exact or whole word | model |
| 3. | Places worldwide | exact | model, with country code |
| 4. | Carinthian places | similar | measurement, marked `?` |
| 5. | Places worldwide | similar | model, marked `?` |

**Why stage 3 comes before stage 4:** until 2026-08-28, `!wx hamburg` answered
`WX Haimburg (Voelkermarkt-Goldbrunnhof)` — a Carinthian hamlet with a similar name.
An exact match must beat a guessed one, across the border too.

**Why stage 4 comes before stage 5:** the same in reverse. In the worldwide directory
the typo `vilach` finds **Vilachá in Spain, population five**. A guess must not beat
another guess.

> **No similarity threshold can separate these, and that was measured.**
> `hamburg → haimburg` scores **0.933**; the genuine typo `vilach → villach` scores
> **0.923**. The wrong match is *more* similar than the right one. Any threshold that
> locks Hamburg out also locks out `vilach`, `goldek` and `spittall`. That Hamburg is
> a real place outside Carinthia is not in the string — that is knowledge, and
> knowledge belongs in a table, not in a threshold.

**A guess says so.** One question mark on the name, one single character:

```
!wx vilach   →   WX Villach?: 20.7C, 86%, Wind 5km/h NO, 960hPa
```

It reads as *I assume you mean Villach*. Before this, a guessed answer was
indistinguishable from a known one.

## Places

**Around 3200 Carinthian places**, generated from OpenStreetMap into
`data/stations_ktn.json`, down to hamlets and quarters. `Sankt` and `St.` are the same
place; bilingual names work in both languages (`Feistritz ob Bleiburg` as well as
`Bistrica pri Pliberku`).

Measurements come from **34 weather stations**, and **place names get valley
stations** (up to 1100 m). Arnoldstein sits at 580 m, the Villacher Alpe at 2117 m and
is still the nearest station — without that rule `!wx arnoldstein` answers ten degrees
too cold. Where a station stands in the place itself, it counts even up high
(Mallnitz, Flattnitz, Kanzelhöhe). Given a **position** the rule does not apply:
whoever asks from the Dobratsch wants the Dobratsch values.

If the station sits somewhere other than the place asked for, it is named:

```
!wx Knappenberg   →   WX Knappenberg (Friesach): 25.3C, 51%, Wind 11km/h NO, 954hPa
```

**Lookup is umlaut-free, the answer is not.** `!wx noetsch` and `!wx nötsch` reach the
same entry, and the answer spells the place the way it is spelled:

```
!wx nötsch   →   WX Nötsch (Bad Bleiberg): 20.4C, 81%, Wind 7km/h O, 914hPa
```

Until 2026-08-28 the lookup key doubled as the display name, so the place was
misspelled in every answer. 405 names have their umlaut back. Station names cannot be
fixed this way: GeoSphere itself writes `DOELLACH` and `GMUEND/KAERNTEN` — that is the
name there, not our transliteration.

**A position works anywhere a place name does** — `!wx 46.6031 13.6712`,
`!relais 2m geo:46.79,13.50`, `!vorhersage 46,6247, 14,3053`. For `!wx` the nearest
weather station is used and **its name is included**, so it is clear where the values
came from.

## Places outside Carinthia

The 34 stations end at the state border. Since 2026-08-28 the answer does not:
everything beyond gets a **model value**, with a country code.

```
!wx lienz     →   WX Lienz (AT): 21.9C, 77%, Wind 1km/h NNO (Modell)
!wx hamburg   →   WX Hamburg (DE): 18.0C, 85%, Wind 13km/h SSO (Modell)
```

The country code is not decoration: **there is a Lienz in East Tyrol and one in the
canton of St. Gallen**, a Hamburg in Germany and four in the USA. Without the code the
receiver cannot tell which one arrived.

Among identically named candidates, **proximity beats population** — "Peca" is a
village in Indonesia and a mountain in the Karawanks, both with population zero. If
nothing is nearby, the larger place wins.

Position lookup and model values both come from Open-Meteo, the same source as the
summit weather, so an outage takes out one dependency and not two.

## Summit weather

Summits have their own command, `!gipfel` (alias `!berg`), covering **9442 summits**
in ten countries from the SOTA list: AT, IT, SI, DE, CH, HR, CZ, SK, HU, PL.

```
!gipfel triglav     →   WX Triglav 2864m: 9.0C, 79%, Wind 7km/h WNW (Modell)
!gipfel marmolada   →   WX Marmolada 3343m: 7.4C, 74%, Wind 9km/h SW (Modell)
```

`!wx goldeck` still works — `!wx` still answers an unambiguous summit name. What it no
longer does is *guess* among summits: that turned `lienz` into "Sandegg - Lienzer" and
`eckwand` into "Bl-eckwand".

> **Nobody measures on a summit — that is a model value, and the answer says so.**
> Hence the `(Modell)` suffix. The numbers come from Open-Meteo, computed **at summit
> elevation**: without that parameter a weather model answers for the mean elevation
> of its grid cell, which on a mountain is easily several hundred metres too low, with
> correspondingly too-warm temperatures.

**Where something is measured, nothing is computed** — that holds on mountains too:

```
!wx dobratsch   →   WX Dobratsch (Villacher Alpe): 11.7C, 99%, Wind 30km/h SW, 791hPa
```

No `(Modell)`, because the *Villacher Alpe* station stands 200 m from the summit
cross. A summit takes the measurement as soon as a station is **closer than 3 km and
within 300 m of elevation**. Both conditions together: proximity alone is not enough,
a valley station can be near in a straight line and still measure different weather
1500 m lower down.

> **How far apart the two can be, on the same mountain in the same minute:** the model
> said 8 km/h of wind, the summit station measured **30 km/h**. Planning a ridge walk
> on the model value produces a surprise. That is exactly why `(Modell)` is there.

**Names are generous:** `Dobratsch` also finds `Villacher Alpe (Dobratsch)`,
`Marmolada` also finds `Punta Penia – Marmolada`, `grossglockner` also finds
`Großglockner`. Where two summits share a name, the higher one wins.

**A match must be a whole word, not a fragment.** Measured against two dozen real
summit queries, the stricter rule costs exactly one hit — `glockner` — and an alias
table catches that:

| typed | found |
|---|---|
| `koralpe` | Großer Speikkogel, 2140 m |
| `saualpe` | Ladinger Spitz, 2079 m |
| `kellerwand` | Hohe Warte, 2780 m |
| `hochstuhl` | Stol, 2236 m *(Slovenian name, same mountain)* |
| `glockner` | Großglockner, 3798 m |
| `obir` | Hochobir, 2139 m |

The SOTA list names the highest point; local usage names the massif. A test asserts
every alias points at an entry that exists — an alias into the void would silently
fall back to guessing, which is worse than no alias.

**Some mountains are missing from the SOTA list entirely.** It only carries summits
with at least 150 m of prominence, which leaves Carinthia with 282 entries; Petzen,
Kornock, Falkert and the Koschuta are not among them. For those, the place lookup
steps in:

```
!gipfel petzen   →   WX Peca (AT): 16.9C, 66%, Wind 11km/h W (Modell)
```

`Peca` is the Slovenian name of the Petzen. If the place lookup finds nothing either —
`Kornock`, for instance — the bot says so instead of returning a similar-sounding
foreign mountain.

## Help on the air

Three stages: all commands, one group, one command.

```
!help            24 Befehle in 5 Gruppen: wetter berg standort netz sonst | !help <thema>
!help berg       Berg: !gipfel !sota !az !spot !sonne !mond
!help az         !az <lat lon> stehst du in der SOTA-Aktivierungszone? Polygon von SOTLAS
!help pfad       aliases resolve too — this is the help for !wo
```

The flat list used to be the normal case. It no longer fits: at 24 commands the
overview falls back to group names by itself rather than being truncated at the
character limit. A test checks, at every character limit, that **either** all commands
**or** all groups are named — never a stump. Two more tests check that every command
has its own help text and appears in a group, otherwise a new command drops silently
out of the documentation.

`netz` is both a command and a group; asking for it returns both in one message.

## Summits by position

On a summit you rarely know the reference, but the device knows the coordinates.
`!sota 46.60 13.67` returns the nearest summits with distance and bearing — nothing
beyond 25 km, which would be worthless as a location statement.

The position may be written in any style, so it can be pasted from the app rather than
typed out:

```
!sota 46.6031, 13.6712
!sota 46,6031, 13,6712
!sota geo:46.6031,13.6712
!sota https://maps.google.com/?q=46.6031,13.6712
```

The first two decimal numbers in the text are used — whole numbers such as a zoom
factor in a map link do not interfere.

## Checking radio paths

`!sicht` is the one command that answers a real operational question: **can these two
points see each other?** It samples the terrain between them at 85 points, lays the
line of sight over it and reports the tightest spot.

```
!sicht 46.603101,13.671223 46.67191,13.89025
Sicht 18.4km: FREI, Fresnel 100% (enger bei km17.5, 1347m)
```

The measure is **not** bare line of sight but how much of the first Fresnel zone stays
clear — the ellipsoid around the beam through which most of the energy travels. A beam
that grazes the ridge is geometrically clear and radio-technically dead. From 60 %
clear it reads `FREI`, below that `KNAPP`, and on contact `BLOCKIERT` together with the
missing height.

Earth curvature is included with the standard factor k = 4/3: the beam bends slightly
with the atmosphere, so it does not travel perfectly straight.

**The near field is excluded** — the first and last 500 m of a path do not enter the
assessment. There the Fresnel radius is nearly zero by arithmetic, every bump in the
ground would produce absurd percentages, and at that range the mounting decides the
link, not the terrain profile. An obstacle 50 m in front of the antenna is visible
without a computer.

**Limits worth knowing:** the computation runs on bare terrain. Forest, buildings and
masts are not in the model — `FREI` means "the terrain is not in the way", not "the
link works". Antenna height is assumed to be 3 m at both ends. And the elevation model
has a 25 m grid: a single sharp ridge can disappear between two grid points.

## `!frag` — the one answer that is not measured

Every other command returns something measured or computed: a station reading, an
ephemeris, a polygon from SOTLAS. `!frag` returns what a language model believes,
and a model can be confidently wrong. Three things follow, and none of them is
decoration.

**It does not do local facts.** Asked how high the Dobratsch is (2166 m), the
models installed on `rag-node-01` answered 412, 711, 1047, 1586, 1743 and 2764 —
six different wrong numbers, none of them hedged. So the system prompt steers the
command away from heights, distances and coordinates: those the bot already
answers from measurement via `!gipfel`, `!hoehe` and `!dist`, and a pointer to a
measured command beats a confident invention. What is left is what the models are
actually good at — explaining a term.

**The answer is marked.** It goes out as `KI: …`, four characters off a
hundred-character budget. On the channel a sentence from a model looks exactly like
a sentence from a measuring station, and everything the bot sends carries the
operator's callsign. The prefix is the only thing that tells the two apart.

**Failure is silence.** Inference runs locally on `rag-node-01` (Ollama, `gemma3:4b`, CPU,
about 4 s per answer). There is no cloud fallback and no API key — deliberately: the command costs
nothing to run and is allowed to be unavailable. If the box is down, `!frag` says
nothing at all and every other command keeps working.

**It is on a shorter leash than anything else.** Two questions per sender per 15
minutes and 100 per day, on top of the limits every command passes. A weather
lookup is one HTTP request; an AI answer is seconds of CPU on a machine that also
runs other services. Repeated questions are served from a cache for an hour and
cost neither.

The model is told to answer in one line within the character budget, and to say
`weiss ich nicht` rather than invent. Neither instruction is a guarantee. What
happens to an answer that is too long, in order:

| | | |
|---|---|---|
| 1 | system prompt names the budget as a number | a request, not a rule |
| 2 | `FRAG_NUM_PREDICT` caps the tokens generated | bounds **CPU time**, not characters — 80 tokens is roughly 280 characters |
| 3 | whole sentences are dropped from the end | only if at least half the budget survives |
| 4 | `clamp()` in the router cuts at a word boundary and appends `…` | the guarantee |

Only step 4 guarantees anything, and what it guarantees is that the *packet* fits —
not that the *answer* is complete. Step 3 exists so that a model answering in three
sentences loses its last sentences rather than its last words: a short answer reads
better than a broken one. A single long sentence has nothing to trim and falls
through to step 4.

The output is also stripped of markdown, control characters and backslashes before
it can reach the JSON template that carries it to the bridge. Hyphens survive —
`2-5 km` is legitimate German, so a markdown list arrives as `- Tal: 2-5 km` rather
than being mangled.

`FRAG_ENABLED` is `false` by default. Switching it on is a decision about what may
be transmitted under your callsign, and it should be made deliberately.

## Data sources

| Command | Source | Licence / note |
|---|---|---|
| `!wx` | GeoSphere Austria, dataset `tawes-v1-10min` | CC BY 4.0, no key needed |
| `!wx` place names | OpenStreetMap, built by `tools/build_orte.py` | ODbL, JSON in the repo, works offline |
| `!wx` abroad | Open-Meteo geocoding + forecast | model value, country code in the answer |
| `!gipfel` | Open-Meteo, computed at summit elevation | 9442 summits from AT, IT, SI, DE, CH, HR, CZ, SK, HU, PL |
| `!warn` | GeoSphere warning API, `getWarningsForCoords` | four query points cover Carinthia |
| `!vorhersage` | GeoSphere, model `nwp-v1-1h-2500m` | point-accurate via coordinates |
| `!lawine` | EAWS bulletin, region `AT-02` | in season only |
| `!sota` by reference | SOTA API v2 | |
| `!sota` by position | the local summit file | offline, also serves as fallback |
| `!az` | activation-zone polygons from SOTLAS | cached 30 days |
| `!spot` | SOTAwatch via the SOTA API | default: OE only |
| `!relais` | RelaisBlick (oeradio.at), JSON in the repo | 219 repeaters, works offline |
| `!netz`, `!wo` | map API of map.carinthiamesh.com | hash is the start of the public key |
| `!sicht`, `!hoehe` | OpenTopoData, model EU-DEM 25 m | one query per path, cached for a week |
| `!dx` | hamqsl.com (N0NBH) | solar and propagation data |
| `!iss` | orbital data from Celestrak, SGP4 | TLE cached 6 h, then marked `~` |
| `!sonne`, `!mond`, `!zeit`, `!dist`, `!qth` | computed, no source | works offline |
| `!frag` | language model on rag-node-01 (Ollama, LAN) | **not measured** — marked `KI:`, cached 1 h |

Caches: weather 10 min, warnings 5 min, SOTA and repeaters 24 h, terrain and place
lookups a week. If a source fails, the last known value is returned prefixed with `~`
— an old value that says it is old beats no value at all.

## Installation

```bash
git clone <repo> meshbot && cd meshbot
cp .env.example .env && chmod 600 .env
$EDITOR .env                 # broker, topics, channel
docker compose up -d --build
docker compose logs -f
```

Check that it is running:

```bash
docker compose exec meshbot python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read())"
```

## Emergency stop

Two ways, both effective immediately:

```bash
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m pause     # stops all sending
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m resume
```

or `BOT_ENABLED=false` in `.env` followed by `docker compose up -d`.

`!frag` has a switch of its own, because it is the one command whose output nobody
vetted before it went on the air. Silencing it should not take the weather down
with it:

```bash
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m "frag off"
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m "frag on"
```

or `FRAG_ENABLED=false` in `.env` for the permanent version.

## Configuration

All values come from environment variables, see `.env.example`. The important ones:

| Variable | Meaning |
|---|---|
| `TOPIC_RX` | topic carrying the decrypted channel messages, e.g. `meshinfra/message/channel/3` |
| `TOPIC_TX` | delivery towards the mesh, `meshinfra/tx/chan` |
| `TX_CHANNEL` | channel slot on the node used for answers |
| `CHANNEL_FILTER` | serve only this channel, empty = all |
| `MAX_MSG_LEN` | character limit, default 140 |
| `SENDER_RESERVE` | room for the node's own name prefix, default 24 |
| `TRANSLITERATE` | rewrite umlauts as `ae/oe/ue`; **off** since 2026-08-28 |
| `GLOBAL_LIMIT` / `SENDER_LIMIT` | airtime brakes |
| `BOT_NAME` | own name, used for loop protection |
| `FRAG_ENABLED` | `!frag` on or off, **default off** |
| `OLLAMA_URL` | inference endpoint, default `http://192.168.1.32:11434/api/chat` |
| `FRAG_MODEL` | model name as Ollama knows it |
| `FRAG_NUM_PREDICT` | token ceiling per answer — the real cost limit, default 80 |
| `FRAG_SENDER_LIMIT` / `FRAG_TAGESLIMIT` | `!frag`'s own brakes, default 2 per 15 min and 100 per day |

## Deployment

```bash
./deploy.sh              # rsync to the bot host, rebuild, wait for healthy
./deploy.sh --dry-run    # show what would change
```

The bot host holds a copy without git — no remote, no pull. The script refuses to
deploy when the tests are red, and waits for the container to report `healthy`; a
started container is not a running bot.

**`data/` is deployed along with the code.** It was excluded for a long time, on the
grounds that it held the running bot's state — it does not: runtime state lives in the
`meldungen:/data` volume, while the repo directory lands at `/srv/data`. On
2026-08-28 that cost a debugging session: the place directory had been rebuilt,
deployed, tests green — and the bot kept answering with the old names, because the file
never made it across.

`.env` is deliberately **not** deployed. The host keeps its own limits.

## Updating the data

**Repeater list** from RelaisBlick:

```bash
python3 - <<'PY'
import json
rb=json.load(open("/path/to/relaisblick/data/relais.json"))
rel=[{"call":r["rufzeichen"],"ort":r["standort"],"bl":r.get("bundesland"),"typ":r.get("typ"),
      "band":r.get("band"),"tx":r.get("txFrequenz"),"shift":r.get("shift"),
      "lat":r["koordinaten"]["lat"],"lon":r["koordinaten"]["lng"]}
     for r in rb["relais"] if r.get("status")=="aktiv" and r.get("koordinaten")]
json.dump({"quelle":"RelaisBlick","stand":rb.get("lastUpdate"),"relais":rel},
          open("data/relais_oe.json","w"), ensure_ascii=False, indent=1)
PY
```

**Place directory** from OpenStreetMap:

```bash
python3 tools/build_orte.py
```

Pulls around 3200 Carinthian place nodes via Overpass, discards everything outside the
state border and attaches each place to a valley station. The station list survives;
hand-maintained entries come from `data/orte_gepflegt.json` and override the result.
The generated directory itself is replaced wholesale every time — otherwise one
badly generated entry would survive every later run. Overpass answers `504` under
load; the script waits and retries, so a run can take up to three minutes.

**Summit directory** from the SOTA list:

```bash
python3 tools/build_gipfel.py                  # all ten associations
python3 tools/build_gipfel.py --assoc OE I S5  # a subset
```

One fetch of `summitslist.csv` rather than one API call per region. The script refuses
to write a file with fewer than 1000 summits — that pattern means a broken fetch, and
a truncated directory is worse than a stale one.

**Weather stations**: `data/stations_ktn.json` maps places to the nearest TAWES
station. Valley stations are preferred deliberately (up to 1100 m), otherwise a query
for Nötsch returns the values of the Villacher Alpe at 2140 m.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q
```

400 tests. The most important one is a property across every handler: **no answer ever
exceeds the character limit.**

## Versioning

`!version` reports which build is on the air and what changed with it. The number and
the one-line summary live in `meshbot/version.py`.

Nobody on the radio can see the repository. When somebody reports "the bot is
answering oddly", they need to be able to say *which* bot — otherwise every
investigation starts with guessing whether the deployment even arrived.

Counting: major for a change that makes existing commands answer differently, minor
for a new command, patch for a fix.

## What the bot does not do

- **Never sends unprompted.** Push sources such as RSS belong in a separate service
- **Never sends multiple messages.** If it does not fit on one line, it gets shortened
- **Never reacts to itself.** Messages carrying its own sender name are discarded
- **Never sends error messages over the air.** What cannot be answered stays unanswered
