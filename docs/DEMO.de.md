# Interaktive Demo (`make demo`) – Kurzfassung

Hi-Fi Bridge auf einem normalen Linux-Notebook ausprobieren – ohne Bluetooth-Adapter, ohne Raspberry Pi, ohne Lautsprecher.
Ausführlich (Englisch): [DEMO.md](DEMO.md).

**Echt:** die unveränderte Bridge-Software mit ihrem BlueZ-Adapter, PipeWire, Lyrion mit drei Squeezelite-Playern (Wohnzimmer,
Schlafzimmer, Arbeitszimmer), Mosquitto, optional Home Assistant, der Kern des Flash-Tools.
**SIMULIERT** (überall gekennzeichnet): Smartphones, Bluetooth-Funk, die Lautsprecher der Räume, das „Internetradio“.
Der Audiomonitor spielt die **Ausgabe der Squeezelite-Player hinter Lyrion** ab, nicht das Signal des Telefons.
Echte Bluetooth-Verbindungen, echte Telefone und ein Raspberry Pi sind damit **nicht** nachgewiesen (Hardware-Gate).

## Start
Voraussetzungen: Docker mit Compose v2 und `make`. Keine Installation auf dem Rechner, keine privilegierten Container.
```bash
make demo          # bauen + starten, zeigt alle Adressen und den Einrichtungscode
make demo-status   # Status, Adressen, Einrichtungscode, CA-Fingerabdruck, MQTT-Zugang, CPU/RAM
make demo-logs     # Logs verfolgen
make demo-stop     # anhalten (alle Daten bleiben)
make demo-reset    # nach Eingabe von RESET: NUR die Demo-Daten löschen
make demo-trust    # Geräte-CA prüfen und als .demo/hifi-bridge-ca.pem speichern
make demo-recover  # Besitzerpasswort vergessen: Wiederherstellung wie per SD-Karte, neuer Einmal-Code
make demo-ha       # zusätzlich Home Assistant mit MQTT-Integration
```
Bridge: http://127.0.0.1:18080 (HTTPS: https://127.0.0.1:18443) · Konsole: http://127.0.0.1:18100 (Smartphones `/phones`,
Audiomonitor `/monitor`) · Lyrion: http://127.0.0.1:19000 · Home Assistant: http://127.0.0.1:18123.
Andere Ports: `DEMO_HTTP_PORT=… DEMO_CONSOLE_PORT=… make demo` (siehe DEMO.md §1). Alle Ports hängen nur an 127.0.0.1; die Dienste der Demo
erreichen weder Internet noch Heimnetz noch Dienste auf diesem Rechner.

## Erstinstallation
Einrichtungscode aus `make demo` → http://127.0.0.1:18080 → Code und Besitzerpasswort → Bluetooth-Name (z. B. „Hi-Fi Bridge Wohnzimmer“)
→ Lyrion-Server (wird automatisch gefunden) → Player **Wohnzimmer** → Fertig.

## Vertrauenswürdiges HTTPS
`make demo-trust` **vor** der Einrichtung prüft die CA mit dem Einmal-Schlüssel des Flash-Tools (Ausgabe `VERIFIED`). Danach ist dieser
Schlüssel absichtlich gelöscht; `make demo-trust` vergleicht dann den Fingerabdruck mit dem der Boot-Partition. Die Datei
`.demo/hifi-bridge-ca.pem` im Browser als Zertifizierungsstelle importieren, dann https://127.0.0.1:18443 öffnen. Nach der Demo wieder entfernen.

## Ausprobieren
* **Gast koppeln und Musik abspielen:** Bridge → Kopplung → „Kopplung 5 Minuten erlauben“. Smartphones → Anna: „Geräte suchen“ →
  „Koppeln“ → „Verbinden“ → Play. Audiomonitor → Wohnzimmer → „Zuhören“.
* **Multiroom:** Audiomonitor → Wohnzimmer und Schlafzimmer ankreuzen → „Gruppe bilden“ → im Schlafzimmer zuhören.
* **Radio durch Bluetooth übernehmen:** Telefon stoppen, „Demo-Radio in diesem Raum abspielen“, dann am Telefon Play: nach etwa 2 s
  übernimmt die Bridge. (Einstellungen → Wiedergabe → „Telefone dürfen einen spielenden Lyrion-Player übernehmen“ ausschalten: die Bridge wartet.)
* **Kurze Benachrichtigungstöne:** Während das Radio läuft, „Nachricht kommt an“: das Radio läuft weiter. Spielt das Telefon selbst Musik,
  wird der Ton hineingemischt.
* **Manueller Quellenwechsel:** Während die Bridge spielt, in Lyrion etwas anderes starten: die Bridge tritt zurück.
* **Lautstärke:** Regler am Telefon → die Lautstärke des Raums in Lyrion ändert sich. Der Abhörpegel im Monitor wirkt nur im Browser.
* **Titelwechsel:** vor/zurück am Telefon; Titel in Lyrion nach Einstellungen → Wiedergabe → „Titel im Stream senden (ICY)“.
* **Verbindungsabbruch:** „Verbindungsabbruch simulieren“: der Raum stoppt sofort.
* **Zweites Smartphone:** Ben verbinden, während Anna spielt: wird abgelehnt, Anna spielt weiter. Unter Bridge → Kopplung „Entfernen“.
* **Eigene Musik:** am Telefon „Eigene Datei hinzufügen“ (WAV, FLAC, MP3, Ogg; bleibt in der Demo).
* **MQTT/Home Assistant:** Bridge → Einstellungen → MQTT mit den Daten aus `make demo-status`; dann `make demo-ha`.

## Messwerte (SIMULIERTE Telefone und Räume; Software-Zeiten in Docker, nicht akustisch)
Play → Lyrion spielt: 2,1 s · Telefon → Raum im Betrieb: 2,2 s · Übernahme vom Radio: 2,3 s · Abbruch → Stopp: 0,02 s ·
**zusätzliche** Latenz des Audiomonitors: ca. 260–295 ms (Puffer ~225 ms + Browserausgabe ~50 ms, gemessen in Chromium; Ihr Browser und
Ihre Soundkarte kommen hinzu). Der Monitor zeigt den Wert live an.
