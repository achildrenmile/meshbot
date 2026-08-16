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
- **Höchstens 6 Antworten je 10 Minuten** im ganzen Netz, **2 Befehle je 5 Minuten** pro Absender
- Bei überschrittenem Limit, unbekanntem Befehl oder Duplikat: **Stille**. Eine Absage kostet genauso viel Sendezeit wie eine Antwort
- Einlieferung erfolgt über das **bestehende Rate-Limit-Gate** von meshinfra (`tx/chan`), nicht daran vorbei — dessen Stundenlimit gilt zusätzlich

## Befehle

| Befehl | Alias | Antwort |
|---|---|---|
| `!wx <ort>` | `!wetter` | `WX Villach: 31.8C, 30%, Wind 14km/h NW, 956hPa` |
| `!uwz` | `!warn` | `UWZ KTN: GELB Gewitter (Zentralraum bis 22:00) +1 weitere` |
| `!sota <ref>` | `!summit` | `OE/KT-048 Rinsennock 2334m, 10Pkt` |
| `!sota <lat> <lon>` | | `OE/KT-072 Villacher Alpe 2166m 8Pkt (88m NW) \| …` |
| `!relais <band> [ort]` | `!rpt` | `2m b. Villach: OE8XNK Gerlitzen 145.7625 -0.6 (10km) \| …` |
| `!ping` | | `MeshBot OK, up 3d4h, 42 cmds` |
| `!help` | `!hilfe` | Einzeiler mit allen Befehlen |

Ohne Ort nimmt `!wx` und `!relais` den Standardort aus der Konfiguration.
Tippfehler werden toleriert (`!wx vilach` findet Villach).

**Gipfel per Position:** Am Berg kennt man die Referenz selten, das Gerät aber die
Koordinaten. `!sota 46.60 13.67` liefert die nächstgelegenen Gipfel mit Entfernung
und Himmelsrichtung — über 25 km Entfernung kommt nichts, das wäre als
Standortangabe wertlos.

## Datenquellen

| Befehl | Quelle | Lizenz / Hinweis |
|---|---|---|
| `!wx` | GeoSphere Austria, Datensatz `tawes-v1-10min` | CC BY 4.0, kein Schlüssel nötig |
| `!uwz` | GeoSphere Warn-API, `getWarningsForCoords` | vier Abfragepunkte decken Kärnten ab |
| `!sota` per Referenz | SOTA API v2 | |
| `!sota` per Position | 1780 Gipfel aus OE/KT, ST, TI, SB, OO im Repo | lokal, ohne Netz, dient auch als Rückfall |
| `!relais` | RelaisBlick (oeradio.at), als JSON im Repo | funktioniert ohne Internet |

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

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q
```

48 Tests. Der wichtigste prüft als Eigenschaft über alle Handler: **keine Antwort
überschreitet je die Zeichengrenze.**

## Was der Bot nicht tut

- **Nicht von selbst senden.** Push-Quellen wie RSS gehören in einen eigenen Dienst
- **Keine Mehrfachnachrichten.** Passt es nicht in eine Zeile, wird gekürzt
- **Nicht auf sich selbst reagieren.** Nachrichten mit dem eigenen Absendernamen werden verworfen
- **Keine Fehlermeldungen ins Funknetz.** Was nicht beantwortet werden kann, bleibt unbeantwortet
