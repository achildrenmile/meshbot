# Spec — MeshBot

## Zweck
Befehle aus einem MeshCore-Kanal beantworten: Wetter, Warnungen, SOTA-Gipfel,
Amateurfunk-Relais, Lebenszeichen.

## Schnittstellen
- **Eingang:** MQTT-Topic mit entschlüsselten Kanalnachrichten
  (`meshinfra/message/channel/<n>`), JSON mit Feldern für Text, Absender, Kanal.
- **Ausgang:** MQTT-Topic `meshinfra/tx/chan`, JSON `{"channel": N, "message": "..."}`.
  Von dort übernimmt das bestehende Gate die Weitergabe an den Node.
- **Steuerung:** Topic `meshinfra/bot/admin`, Nutzlast `pause` oder `resume`.
- **Health:** HTTP `:8080/healthz`, prüft Prozess und MQTT-Verbindung.

## Befehle
`!wx <ort>`, `!uwz`, `!sota <ref>`, `!relais <band> [ort]`, `!ping`, `!help`
samt deutscher und englischer Aliase. Präfix `!`, Groß- und Kleinschreibung egal.

## Grenzen
- Antwort ≤ 140 Zeichen (hart), niemals mehrere Nachrichten
- global 12 Antworten je 10 min, je Absender 4 Befehle je 5 min
- Duplikate innerhalb 60 s werden verworfen
- eigene Nachrichten werden nie als Befehl gewertet

## Nicht im Umfang
Push-Benachrichtigungen, Dialoge über mehrere Nachrichten, Direktnachrichten,
Konfiguration über das Funknetz.
