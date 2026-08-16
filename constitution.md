# Constitution — MeshBot

Verbindliche Grundsätze. Wer eine Änderung plant, die einem dieser Sätze
widerspricht, ändert zuerst diese Datei — mit Begründung.

1. **Sendezeit schlägt Funktionsumfang.** Jede neue Antwort muss sich gegen die
   Frage verteidigen, ob sie den Platz auf dem Band wert ist.
2. **Im Zweifel schweigen.** Unbekannter Befehl, gerissenes Limit, Duplikat,
   Fehler in der Quelle: keine Antwort. Eine Absage kostet dasselbe wie eine Antwort.
3. **Eine Nachricht, harte Grenze.** 140 Zeichen, erzwungen vor dem Senden, geprüft
   als Eigenschaft über alle Handler.
4. **Nur auf Abruf.** Der Bot beginnt nie ein Gespräch.
5. **Alles konfigurierbar, nichts einkompiliert.** Topics, Nutzlastformat, Kanal,
   Grenzen und Quellen kommen aus der Umgebung.
6. **Keine Geheimnisse im Repo.** Zugangsdaten ausschließlich in `.env`.
7. **Fremde Bremsen respektieren.** Eingeliefert wird über das bestehende
   Rate-Limit-Gate, nicht daran vorbei.
8. **Ausfall der Außenwelt ist kein Ausfall des Bots.** Timeouts, ein Wiederholversuch,
   danach der letzte bekannte Wert mit `~` oder Stille.
