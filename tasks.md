# Tasks — MeshBot

- [x] Konfiguration über Umgebungsvariablen
- [x] Zeichengrenze und Umlautersetzung
- [x] Token-Bucket global und je Absender, Duplikaterkennung
- [x] Router mit Befehlserkennung, Aliasen, Schleifenschutz, Kanalfilter
- [x] Handler `!wx` gegen GeoSphere TAWES
- [x] Handler `!uwz` gegen die GeoSphere Warn-API
- [x] Handler `!sota` gegen SOTA API v2, Referenznormalisierung
- [x] Handler `!relais` gegen mitgelieferte RelaisBlick-Daten
- [x] `!ping` und `!help`
- [x] Zwischenspeicher mit Ablaufzeit, letzter bekannter Wert bei Ausfall
- [x] MQTT mit Wiederverbindung, Admin-Topic als Not-Aus
- [x] Health-Endpunkt
- [x] Datendateien aus echten Quellen erzeugt
- [x] 48 Tests, darunter die Eigenschaft „nie länger als das Limit"
- [x] Dockerfile, Compose, README
- [ ] Auf host-node-01 ausrollen, sobald der Bot-Kanal am Node angelegt ist
- [x] Kanal festgelegt: `#at-ktn-bot`, Slot 3 — `TOPIC_RX=meshinfra/message/channel/3`, `TX_CHANNEL=3`
