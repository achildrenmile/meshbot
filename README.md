# MeshBot — Befehlsbot fürs CarinthiaMesh

Hört auf einem MeshCore-Kanal mit, erkennt Befehle mit `!`-Präfix, holt Daten von
außen und antwortet **in einer einzigen kurzen Nachricht**.

Kanal: **`#at-ktn-bot`**, am Node auf **Slot 3**.

Der Bot hängt nicht am Funkgerät, sondern am MQTT-Broker des bestehenden
[meshinfra](https://github.com/achildrenmile/meshinfra)-Stacks. Er ist damit kein
weiterer TCP-Client am Node — davon verträgt ein Companion nur zwei.

---

## Die eine Regel

**Sendezeit ist das knappste Gut im Netz.** Alles andere folgt daraus:

- Antworten sind auf **140 Zeichen** begrenzt, hart, vor dem Senden
- **Nur auf Abruf**, nie von selbst
- **Höchstens 12 Antworten je 10 Minuten** im ganzen Netz, **4 Befehle je 5 Minuten** pro Absender
- Bei überschrittenem Limit, unbekanntem Befehl oder Duplikat: **Stille**. Eine Absage kostet genauso viel Sendezeit wie eine Antwort
- Einlieferung erfolgt über das **bestehende Rate-Limit-Gate** von meshinfra (`tx/chan`), nicht daran vorbei — dessen eigene Bremsen gelten zusätzlich
- Beides sind **Token-Buckets**: `limit` Antworten am Stück, danach eine je `window/limit` Sekunden. Global also 12 auf einmal, dann eine alle 50 s; je Absender 4 auf einmal, dann eine alle 75 s

## Befehle

| Befehl | Alias | Antwort |
|---|---|---|
| `!wx <ort\|lat lon>` | `!wetter` | `WX Villach: 31.8C, 30%, Wind 14km/h NW, 956hPa` |
| `!uwz` | `!warn` | `UWZ KTN: GELB Gewitter (Zentralraum bis 22:00) +1 weitere` |
| `!sota <ref>` | `!summit` | `OE/KT-048 Rinsennock 2334m, 10Pkt` |
| `!sota <lat> <lon>` | | `OE/KT-072 Villacher Alpe 2166m 8Pkt (88m NW) \| …` |
| `!relais <band> [ort\|lat lon]` | `!rpt` | `2m b. Villach: OE8XNK Gerlitzen 145.7625 -0.6 (10km) \| …` |
| `!vorhersage <ort>` | `!morgen`, `!fc` | `24h Villach: 18 bis 26C, 11mm Regen, Boeen 38km/h` |
| `!lawine` | `!avalanche` | `Lawine KTN: Stufe 3 erheblich (ab Waldgrenze)` |
| `!spot [assoc]` | `!spots` | `OE8XXX OE/KT-048 14.062 CW 12min` |
| `!sonne [ort\|lat lon]` | `!sun` | `Sonne: auf 06:04, unter 20:15, dunkel 20:48 (noch 1h03)` |
| `!netz` | `!status` | `Netz KTN: 32/33 Repeater aktiv, 31729 Weiterleitungen/24h` |
| `!wo <name>` | `!node` | Position, Verkehr und letzter Empfang eines Knotens |
| `!melde <was, wo>` | `!luecke` | Feldmeldung erfassen, Position optional |
| `!qth <locator\|lat lon>` | `!loc` | Maidenhead in Koordinaten und zurück |
| `!sicht <lat,lon> <lat,lon>` | `!los` | `Sicht 18.4km: FREI, Fresnel 100% (enger bei km17.5, 1347m)` |
| `!hoehe <lat,lon>` | `!seehoehe` | `Hoehe 46.6719,13.8902: 1478m (EU-DEM 25m)` |
| `!dist <lat,lon> <lat,lon>` | `!entfernung` | `37.9km, Peilung 312 NW (zurueck 132 SO)` |
| `!mond [ort\|lat lon]` | `!moon` | `Mond: auf 10:28, unter 21:33, zunehmend 17%` |
| `!dx` | `!solar` | `DX: SFI 117, A6, K0, SN 83, Xray C1.3` |
| `!iss [lat lon]` | `!sat` | `ISS 05:44 max 24Grad, NNO>W, 6min` |
| `!zeit` | `!time`, `!utc` | `UTC 16.08.2026 17:11:53 (Epoch 1786900313)` |
| `!ping` | | `MeshBot OK, up 3d4h, 42 cmds` |
| `!help [cmd]` | `!hilfe` | Übersicht, mit Befehl die Einzelheiten |

Ohne Ort nimmt `!wx` und `!relais` den Standardort aus der Konfiguration.
Tippfehler werden toleriert (`!wx vilach` findet Villach).

**Ortsnamen: rund 3200 Kärntner Orte**, aus OpenStreetMap erzeugt und in
`data/stations_ktn.json` abgelegt — bis hinunter zu Weilern und Ortsteilen.
`Sankt` und `St.` sind derselbe Ort, zweisprachige Namen gelten in beiden
Sprachen (`Feistritz ob Bleiburg` wie `Bistrica pri Pliberku`). Das Verzeichnis
endet an der Landesgrenze: `!wx Innsbruck` bleibt unbekannt, denn ein Treffer
wäre schlimmer als keiner — er lieferte Kärntner Werte für Tirol.

Gemessen wird an **34 Wetterstationen**, und **Ortsnamen bekommen Talstationen**
(bis 1100 m). Arnoldstein liegt auf 580 m, die Villacher Alpe auf 2117 m und ist
trotzdem die nächste Station — ohne diese Regel antwortet `!wx arnoldstein` mit
zehn Grad zu wenig. Wo die eigene Station am Berg steht (Mallnitz, Flattnitz,
Kanzelhöhe), gilt sie. Bei einer **Position** gilt die Grenze nicht: wer vom
Dobratsch fragt, will die Werte vom Dobratsch.

Steht die Station woanders als der gefragte Ort, wird sie mitgenannt:

```
!wx Knappenberg   →   WX Knappenberg (Friesach): 25.3C, 51%, Wind 11km/h NO, 954hPa
```

**Statt eines Ortsnamens geht überall auch eine Position** — `!wx 46.6031 13.6712`,
`!relais 2m geo:46.79,13.50`, `!vorhersage 46,6247, 14,3053`. Bei `!wx` wird die
nächstgelegene Wetterstation genommen und **ihr Name mit ausgegeben**, damit klar
ist, woher die Werte stammen.

**Dreistufige Hilfe:** `!help` listet alle Befehle, `!help standort` eine Gruppe
davon, `!help sicht` einen einzelnen. Die flache Liste ist die bessere Antwort —
wer `!help` tippt, will sehen was es gibt, nicht ein Menü durchklicken. Sie wächst
aber mit jedem Befehl; passt sie nicht mehr in eine Nachricht, fällt die Antwort
selbsttätig auf die Gruppennamen zurück, statt am Zeichenlimit abgeschnitten zu
werden.

**Gipfel per Position:** Am Berg kennt man die Referenz selten, das Gerät aber die
Koordinaten. `!sota 46.60 13.67` liefert die nächstgelegenen Gipfel mit Entfernung
und Himmelsrichtung — über 25 km Entfernung kommt nichts, das wäre als
Standortangabe wertlos.

Die Position darf in beliebiger Schreibweise dahinterstehen, damit man sie aus der
App einfach hineinkopieren kann statt sie abzutippen:

```
!sota 46.6031, 13.6712
!sota 46,6031, 13,6712
!sota geo:46.6031,13.6712
!sota https://maps.google.com/?q=46.6031,13.6712
```

Gesucht werden die ersten zwei Dezimalzahlen im Text — ganze Zahlen wie ein
Zoomfaktor in einem Kartenlink stören nicht.

## Funkstrecken prüfen

`!sicht` ist der einzige Befehl, der eine echte Frage des Netzbetriebs beantwortet:
**Sehen sich diese zwei Punkte?** Er tastet das Gelände zwischen ihnen an 85 Stellen
ab, legt die Sichtlinie darüber und meldet die engste Stelle.

```
!sicht 46.603101,13.671223 46.67191,13.89025
Sicht 18.4km: FREI, Fresnel 100% (enger bei km17.5, 1347m)
```

Maßstab ist **nicht** die blanke Sichtlinie, sondern wie viel der ersten Fresnelzone
frei bleibt — jenes Ellipsoids um den Strahl, das der Großteil der Energie
durchläuft. Ein Strahl, der den Grat streift, ist geometrisch frei und funktechnisch
tot. Ab 60 % freier Zone heißt es `FREI`, darunter `KNAPP`, bei Berührung
`BLOCKIERT` samt fehlender Höhe.

Mitgerechnet wird die Erdkrümmung mit dem Standardfaktor k = 4/3: Der Funkstrahl
biegt sich in der Atmosphäre leicht mit, er läuft also nicht ganz geradeaus.

**Der Nahbereich bleibt außen vor** — die ersten und letzten 500 m einer Strecke
gehen nicht in die Bewertung ein. Dort ist der Fresnelradius rechnerisch fast null,
jeder Bodenbuckel ergäbe absurde Prozentwerte, und in dieser Nähe entscheidet die
Aufstellung über die Verbindung, nicht das Streckenprofil. Wer 50 m vor der Antenne
ein Hindernis hat, sieht das ohne Rechner.

**Grenzen, die man kennen sollte:** Gerechnet wird auf dem nackten Gelände. Wald,
Häuser und Masten stehen nicht im Modell — `FREI` heißt „das Gelände steht nicht im
Weg", nicht „die Verbindung steht". Angenommen werden 3 m Antennenhöhe an beiden
Enden. Und das Höhenmodell hat 25 m Rasterweite: ein einzelner scharfer Grat kann
zwischen zwei Rasterpunkten verschwinden.

## Datenquellen

| Befehl | Quelle | Lizenz / Hinweis |
|---|---|---|
| `!wx` | GeoSphere Austria, Datensatz `tawes-v1-10min` | CC BY 4.0, kein Schlüssel nötig |
| `!wx` Ortsnamen | OpenStreetMap, erzeugt mit `tools/build_orte.py` | ODbL, als JSON im Repo, ohne Netz |
| `!uwz` | GeoSphere Warn-API, `getWarningsForCoords` | vier Abfragepunkte decken Kärnten ab |
| `!sota` per Referenz | SOTA API v2 | |
| `!sota` per Position | 1780 Gipfel aus OE/KT, ST, TI, SB, OO im Repo | lokal, ohne Netz, dient auch als Rückfall |
| `!relais` | RelaisBlick (oeradio.at), als JSON im Repo | funktioniert ohne Internet |
| `!vorhersage` | GeoSphere, Modell `nwp-v1-1h-2500m` | punktgenau über Koordinaten |
| `!lawine` | EAWS-Bulletin, Region `AT-02` | nur in der Saison |
| `!spot` | SOTAwatch über die SOTA-API | Vorgabe: nur OE |
| `!netz` | Karten-API von map.carinthiamesh.com | |
| `!sonne`, `!mond`, `!zeit` | gerechnet, keine Quelle | funktioniert ohne Internet |
| `!dist`, `!qth` | gerechnet, keine Quelle | funktioniert ohne Internet |
| `!sicht`, `!hoehe` | OpenTopoData, Modell EU-DEM 25 m | eine Abfrage je Strecke, Ergebnis eine Woche im Cache |
| `!dx` | hamqsl.com (N0NBH) | Solar- und Ausbreitungsdaten |
| `!iss` | Bahndaten von Celestrak, Rechnung mit SGP4 | TLE 6 h im Cache, danach mit `~` markiert |
| `!wo` | Karten-API von map.carinthiamesh.com | |

Zwischenspeicher: Wetter 10 min, Warnungen 5 min, SOTA und Relais 24 h. Fällt eine
Quelle aus, kommt der letzte bekannte Wert mit `~` davor — lieber ein alter Wert
mit Kennzeichnung als gar keiner.

## Installation

```bash
git clone <repo> meshbot && cd meshbot
cp .env.example .env && chmod 600 .env
$EDITOR .env                 # Broker, Topics, Kanal
docker compose up -d --build
docker compose logs -f
```

Prüfen, ob er läuft:

```bash
docker compose exec meshbot python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read())"
```

## Not-Aus

Zwei Wege, beide sofort wirksam:

```bash
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m pause     # stoppt jedes Senden
mosquitto_pub -h <broker> -t meshinfra/bot/admin -m resume
```

oder `BOT_ENABLED=false` in der `.env` und `docker compose up -d`.

## Konfiguration

Alle Werte über Umgebungsvariablen, siehe `.env.example`. Die wichtigsten:

| Variable | Bedeutung |
|---|---|
| `TOPIC_RX` | Topic mit den entschlüsselten Kanalnachrichten, z. B. `meshinfra/message/channel/3` |
| `TOPIC_TX` | Einlieferung Richtung Mesh, `meshinfra/tx/chan` |
| `TX_CHANNEL` | Kanalslot am Node, auf dem geantwortet wird |
| `CHANNEL_FILTER` | Nur diesen Kanal bedienen, leer = alle |
| `MAX_MSG_LEN` | Zeichengrenze, Vorgabe 140 |
| `GLOBAL_LIMIT` / `SENDER_LIMIT` | Airtime-Bremsen |
| `BOT_NAME` | Eigener Name, dient dem Schleifenschutz |

## Daten aktualisieren

**Relaisliste** aus RelaisBlick neu erzeugen:

```bash
python3 - <<'PY'
import json
rb=json.load(open("/pfad/zu/relaisblick/data/relais.json"))
rel=[{"call":r["rufzeichen"],"ort":r["standort"],"bl":r.get("bundesland"),"typ":r.get("typ"),
      "band":r.get("band"),"tx":r.get("txFrequenz"),"shift":r.get("shift"),
      "lat":r["koordinaten"]["lat"],"lon":r["koordinaten"]["lng"]}
     for r in rb["relais"] if r.get("status")=="aktiv" and r.get("koordinaten")]
json.dump({"quelle":"RelaisBlick","stand":rb.get("lastUpdate"),"relais":rel},
          open("data/relais_oe.json","w"), ensure_ascii=False, indent=1)
PY
```

**Wetterstationen**: `data/stations_ktn.json` bildet Ort auf die nächstgelegene
TAWES-Station ab. Bewusst werden **Talstationen bevorzugt** (bis 1100 m) — sonst
liefert eine Abfrage für Nötsch die Werte der Villacher Alpe auf 2140 m.

**Ortsverzeichnis** neu aus OpenStreetMap erzeugen:

```bash
python3 tools/build_orte.py
```

Zieht rund 3200 Kärntner Ortsknoten über Overpass, verwirft alles außerhalb der
Landesgrenze und hängt jeden Ort an eine Talstation. Die Stationsliste bleibt
stehen, die Handpflege kommt aus `data/orte_gepflegt.json` und überschreibt das
Ergebnis — das erzeugte Verzeichnis selbst wird jedes Mal komplett ersetzt,
sonst überlebte ein einmal falsch erzeugter Eintrag jeden weiteren Lauf.
Overpass antwortet unter Last mit `504`; das Skript wartet und wiederholt, ein
Lauf dauert deshalb bis zu drei Minuten.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q
```

83 Tests. Der wichtigste prüft als Eigenschaft über alle Handler: **keine Antwort
überschreitet je die Zeichengrenze.**

## Was der Bot nicht tut

- **Nicht von selbst senden.** Push-Quellen wie RSS gehören in einen eigenen Dienst
- **Keine Mehrfachnachrichten.** Passt es nicht in eine Zeile, wird gekürzt
- **Nicht auf sich selbst reagieren.** Nachrichten mit dem eigenen Absendernamen werden verworfen
- **Keine Fehlermeldungen ins Funknetz.** Was nicht beantwortet werden kann, bleibt unbeantwortet
