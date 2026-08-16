# Plan — MeshBot

1. **Fundament**: Konfiguration über Pydantic-Settings, Formatierung mit harter
   Zeichengrenze, Token-Bucket und Duplikaterkennung. Diese drei entscheiden über
   die Airtime und werden zuerst getestet.
2. **Router**: Nutzlast zerlegen, Befehl erkennen, Bremsen anwenden, Handler rufen.
   Jeder Zweig, der nicht sicher antworten kann, gibt `None` zurück.
3. **Handler**: je Quelle ein Modul mit `fetch` und `render`, strikt getrennt —
   `render` ist rein und damit ohne Netz testbar.
4. **Anbindung**: MQTT mit Wiederverbindung, Health-Endpunkt, sauberes Beenden.
5. **Daten**: Stationszuordnung aus der GeoSphere-Stationsliste erzeugen,
   Relaisliste aus RelaisBlick ableiten. Beide im Repo, damit der Bot ohne
   Internet startfähig bleibt.
6. **Betrieb**: Dockerfile ohne root, Compose am bestehenden Broker-Netz, README
   mit Not-Aus und Aktualisierungswegen.
