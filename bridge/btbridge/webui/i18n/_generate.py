"""Source of the UI dictionaries: edit here, then run `python3 _generate.py` to rewrite en.json/de.json (keys stay in sync)."""
import json
import os
E, D = {}, {}
def k(key, en, de): E[key] = en; D[key] = de
k("brand.tagline", "Bluetooth to Lyrion", "Bluetooth zu Lyrion")
k("a11y.skip", "Skip to content", "Zum Inhalt springen")
for key, en, de in [("nav.label","Main navigation","Hauptnavigation"),("nav.status","Status","Status"),("nav.pairing","Pairing","Kopplung"),
    ("nav.settings","Settings","Einstellungen"),("nav.diagnostics","Diagnostics","Diagnose"),("nav.system","System","System"),("nav.logout","Log out","Abmelden")]: k(key,en,de)
# headlines
H = {"not_configured": ("Setup needed","Einrichtung nötig","Choose a Lyrion server and a player to finish setup.","Wählen Sie einen Lyrion-Server und einen Player, um die Einrichtung abzuschließen."),
     "audio_disabled": ("Bridge audio off","Bridge-Wiedergabe aus","Phones can stay connected, but nothing is played on Lyrion.","Telefone bleiben verbunden, aber auf Lyrion wird nichts abgespielt."),
     "lyrion_unreachable": ("Lyrion unreachable","Lyrion nicht erreichbar","Bluetooth stays connected; playback resumes when the server or player is back.","Bluetooth bleibt verbunden; die Wiedergabe setzt fort, sobald Server bzw. Player wieder da sind."),
     "ready": ("Ready","Bereit","Waiting for a phone to connect.","Wartet auf die Verbindung eines Telefons."),
     "connected": ("Phone connected","Telefon verbunden","Press Play in any audio app on the phone.","Auf dem Telefon in einer beliebigen Audio-App „Play“ drücken."),
     "playing": ("Playing","Spielt","Bluetooth audio is playing on the selected Lyrion player.","Bluetooth-Audio läuft auf dem gewählten Lyrion-Player."),
     "paused": ("Paused","Pausiert","Playback resumes when Play is pressed on the phone.","Die Wiedergabe setzt fort, sobald auf dem Telefon „Play“ gedrückt wird."),
     "waiting_for_target": ("Waiting for the player","Wartet auf den Player","Other music is playing on the player; the bridge does not interrupt it right now.","Auf dem Player läuft andere Musik; die Bridge unterbricht sie gerade nicht.")}
for h,(en,de,ed,dd) in H.items(): k(f"headline.{h}",en,de); k(f"headline.{h}.detail",ed,dd)
S = {"connected":("Connected","Verbunden"),"disconnected":("Not connected","Nicht verbunden"),"playing":("Playing","Spielt"),"paused":("Paused","Pausiert"),
     "idle":("Idle","Ruhend"),"allowed":("Allowed","Erlaubt"),"blocked":("Blocked","Gesperrt"),"reachable":("Reachable","Erreichbar"),
     "unreachable":("Unreachable","Nicht erreichbar"),"unknown":("Unknown","Unbekannt"),"none":("None","Keine"),"owned":("Bridge plays","Bridge spielt"),
     "lost":("Taken over by another source","Von anderer Quelle übernommen"),"closed":("Closed","Geschlossen"),"parked":("Paused (grace period)","Pausiert (Karenzzeit)"),
     "streaming":("Streaming","Streamt"),"audio_disabled":("Bridge audio off","Bridge-Wiedergabe aus"),"override_disabled":("Override disabled","Übernahme deaktiviert"),
     "notification_filter":("Checking: notification or music?","Prüft: Hinweiston oder Musik?"),"lyrion_unreachable":("Lyrion unreachable","Lyrion nicht erreichbar"),
     "stream_address_unavailable":("Waiting for the network","Wartet auf das Netzwerk"),
     "no_device":("No phone connected","Kein Telefon verbunden"),"not_configured":("Not configured","Nicht eingerichtet"),"not_connected":("Not connected","Nicht verbunden")}
for s,(en,de) in S.items(): k(f"state.{s}",en,de)
DIM = {"bluetooth_connection":("Bluetooth","Bluetooth"),"bt_media_activity":("Phone media","Medien am Telefon"),"audio_permission":("Bridge audio","Bridge-Wiedergabe"),
       "lyrion_reachability":("Lyrion","Lyrion"),"target_ownership":("Player","Player"),"stream_transport":("Stream","Stream"),"blocked_reason":("Waiting because","Wartet, weil")}
for d,(en,de) in DIM.items(): k(f"dim.{d}",en,de)
for key,en,de in [("live.pairing_open","Pairing open for {seconds} s – select “{name}” on the phone","Kopplung {seconds} s offen – am Telefon „{name}“ auswählen"),
    ("live.offline","Connection to the bridge lost – retrying …","Verbindung zur Bridge unterbrochen – neuer Versuch …")]: k(key,en,de)
# dashboard
for key,en,de in [("dash.now","Now","Jetzt"),("dash.device","Phone","Telefon"),("dash.playing","Now playing","Aktueller Titel"),("dash.target","Lyrion player","Lyrion-Player"),
    ("dash.server","Lyrion server","Lyrion-Server"),("dash.attention","Needs attention","Bitte beachten"),("dash.pairing","Pair a phone","Telefon koppeln"),
    ("dash.audio","Bridge audio","Bridge-Wiedergabe"),("dash.test","Test sound","Testton"),("dash.system","System","System"),("dash.more","System details","Systemdetails"),
    ("pairing.open","Allow pairing for 5 minutes","Kopplung 5 Minuten erlauben"),("pairing.close","Stop pairing","Kopplung beenden"),
    ("pairing.open_for","Pairing open for {seconds} s – select “{name}” on the phone","Kopplung {seconds} s offen – am Telefon „{name}“ auswählen"),
    ("pairing.help","Guests then select “{name}” in their phone’s Bluetooth settings – no app needed.","Gäste wählen dann in den Bluetooth-Einstellungen ihres Telefons „{name}“ – ohne App."),
    ("pairing.window","Pairing window","Kopplungsfenster"),("pairing.guest_hint","Paired phones reconnect later without a pairing window.","Gekoppelte Telefone verbinden sich später ohne Kopplungsfenster."),
    ("pairing.devices","Paired phones","Gekoppelte Telefone"),("pairing.no_devices","No phone paired yet.","Noch kein Telefon gekoppelt."),
    ("pairing.one_source","Only one phone can play at a time; a second phone is refused and never interrupts the first.","Es kann immer nur ein Telefon spielen; ein zweites wird abgewiesen und unterbricht das erste nie."),
    ("pairing.label","Name","Name"),("pairing.remove","Remove","Entfernen"),("pairing.remove.confirm","Remove this phone? It must pair again later.","Dieses Telefon entfernen? Es muss später neu gekoppelt werden."),
    ("audio.allowed","On – phones may play","An – Telefone dürfen spielen"),("audio.blocked","Off – nothing is played","Aus – es wird nichts abgespielt"),
    ("audio.help","Switching off stops only the bridge’s own playback, never other Lyrion music.","Ausschalten beendet nur die eigene Wiedergabe der Bridge, nie andere Lyrion-Musik."),
    ("action.test_sound","Play test sound","Testton abspielen"),("action.save","Save","Speichern")]: k(key,en,de)
# wizard
for key,en,de in [("wizard.progress","Setup progress","Fortschritt der Einrichtung"),("wizard.step.account","Owner","Besitzer"),("wizard.step.bluetooth","Bluetooth","Bluetooth"),
    ("wizard.step.lyrion","Lyrion","Lyrion"),("wizard.step.target","Player","Player"),("wizard.step.finish","Done","Fertig"),
    ("wizard.welcome","Welcome","Willkommen"),("wizard.welcome.text","Set up the bridge in a few steps. Guests will later simply connect via Bluetooth and press Play.","Richten Sie die Bridge in wenigen Schritten ein. Gäste verbinden sich später einfach per Bluetooth und drücken „Play“."),
    ("wizard.code.help","The one-time setup code belongs to this device (development: file setup-code.txt in the data directory).","Der einmalige Einrichtungscode gehört zu diesem Gerät (Entwicklung: Datei setup-code.txt im Datenverzeichnis)."),
    ("wizard.create_account","Create owner password","Besitzer-Passwort festlegen"),("wizard.next","Continue","Weiter"),
    ("wizard.bluetooth.title","Bluetooth name","Bluetooth-Name"),("wizard.bluetooth.text","This is the name guests see on their phones.","Diesen Namen sehen Gäste auf ihren Telefonen."),
    ("wizard.bluetooth.help","Up to 32 characters, e.g. “Living room”.","Bis zu 32 Zeichen, z. B. „Wohnzimmer“."),
    ("wizard.lyrion.title","Lyrion server","Lyrion-Server"),("wizard.lyrion.text","Choose the server the bridge should play on.","Wählen Sie den Server, auf dem die Bridge spielen soll."),
    ("wizard.lyrion.found","Found in the network","Im Netzwerk gefunden"),("wizard.lyrion.none_found","No server found automatically – enter it below.","Kein Server automatisch gefunden – bitte unten eingeben."),
    ("wizard.lyrion.manual","Enter manually","Manuell eingeben"),("wizard.lyrion.connect","Connect","Verbinden"),
    ("wizard.lyrion.unreachable","No Lyrion server answered at {server}.","Unter {server} antwortet kein Lyrion-Server."),("wizard.lyrion.connected","Connected to Lyrion {version}.","Mit Lyrion {version} verbunden."),
    ("wizard.target.title","Player or group","Player oder Gruppe"),("wizard.target.text","Choose one player or an existing synchronised group.","Wählen Sie einen Player oder eine bestehende synchronisierte Gruppe."),
    ("wizard.target.legend","Available targets","Verfügbare Ziele"),("wizard.target.help","The bridge never creates or changes groups and never switches to another player on its own.","Die Bridge legt nie Gruppen an oder ändert sie und wechselt nie selbstständig zu einem anderen Player."),
    ("wizard.target.none","The server reports no players.","Der Server meldet keine Player."),("wizard.target.unreachable","The Lyrion server is not reachable right now.","Der Lyrion-Server ist gerade nicht erreichbar."),
    ("wizard.finish.title","All set","Fertig eingerichtet"),("wizard.finish.text","Play a test sound to check the player, then finish.","Spielen Sie zur Kontrolle einen Testton ab und schließen Sie dann ab."),
    ("wizard.finish.done","Finish setup","Einrichtung abschließen"),("target.group","Synchronised group","Synchronisierte Gruppe"),("target.player","Player","Player"),
    ("target.online","online","online"),("target.offline","offline","offline")]: k(key,en,de)
# login
for key,en,de in [("login.title","Log in","Anmelden"),("login.submit","Log in","Anmelden"),("login.help","Your browser can remember the owner password.","Ihr Browser kann das Besitzer-Passwort speichern.")]: k(key,en,de)
# fields
F = {"setup_code":("Setup code","Einrichtungscode"),"new_password":("New password","Neues Passwort"),"confirm_password":("Repeat password","Passwort wiederholen"),
     "password":("Password","Passwort"),"device_name":("Bluetooth name","Bluetooth-Name"),"pairing_seconds":("Pairing window (seconds)","Kopplungsfenster (Sekunden)"),
     "audio_allowed":("Bridge audio","Bridge-Wiedergabe"),"override_lms":("Phones may take over a playing Lyrion player","Telefone dürfen einen spielenden Lyrion-Player übernehmen"),
     "pause_strategy":("Pause behaviour","Verhalten bei Pause"),"stream_format":("Stream format","Stream-Format"),"icy_metadata":("Send track titles in the stream (ICY)","Titel im Stream senden (ICY)"),
     "lms_host":("Server address","Server-Adresse"),"lms_port":("Port","Port"),"target_player":("Player","Player"),"target_label":("Player name","Player-Name"),
     "language":("Language","Sprache"),"theme":("Appearance","Darstellung"),"mqtt_enabled":("Enable MQTT","MQTT aktivieren"),"mqtt_host":("Broker address","Broker-Adresse"),
     "mqtt_port":("Broker port","Broker-Port"),"mqtt_username":("User name","Benutzername"),"mqtt_password":("Password","Passwort"),"mqtt_tls":("Use TLS","TLS verwenden"),
     "mqtt_base_topic":("Base topic","Basis-Topic"),"mqtt_discovery_prefix":("Home Assistant discovery prefix","Home-Assistant-Discovery-Präfix"),
     "device_labels":("Phone names","Telefonnamen"),
     "mqtt_allow_pairing":("Allow opening the pairing window via MQTT","Kopplungsfenster per MQTT öffnen erlauben"),
     "mqtt_allow_audio":("Allow switching bridge playback via MQTT","Bridge-Wiedergabe per MQTT schalten erlauben"),
     "mqtt_allow_test":("Allow the test tone via MQTT","Testton per MQTT erlauben"),
     "mqtt_telemetry_s":("Telemetry interval (seconds)","Telemetrie-Intervall (Sekunden)"),
     "mqtt_ca_pem":("Broker CA certificate (PEM, optional)","CA-Zertifikat des Brokers (PEM, optional)"),"setup_complete":("Setup complete","Einrichtung abgeschlossen"),"instance_id":("Instance id","Instanz-ID")}
for f,(en,de) in F.items(): k(f"field.{f}",en,de)
C = {"pause_strategy.drop_restart":("Silence at once (recommended)","Sofort Stille (empfohlen)"),"pause_strategy.keepalive_silence":("Keep the stream open","Stream offen halten"),
     "stream_format.wav":("WAV / PCM (recommended)","WAV / PCM (empfohlen)"),"stream_format.flac":("FLAC","FLAC"),"language.auto":("Browser language","Browsersprache"),
     "language.de":("Deutsch","Deutsch"),"language.en":("English","English"),"theme.auto":("Like the device","Wie das Gerät"),"theme.light":("Light","Hell"),"theme.dark":("Dark","Dunkel")}
for c,(en,de) in C.items(): k(f"choice.{c}",en,de)
for key,en,de in [("settings.lyrion","Lyrion server","Lyrion-Server"),("settings.lyrion.help","Changing the server stops only the bridge’s own playback.","Ein Serverwechsel beendet nur die eigene Wiedergabe der Bridge."),
    ("settings.target","Player or group","Player oder Gruppe"),("settings.playback","Playback","Wiedergabe"),
    ("settings.playback.help","ICY titles stay off until older Squeezebox models are verified.","ICY-Titel bleiben aus, bis ältere Squeezebox-Modelle geprüft sind."),
    ("settings.bluetooth","Bluetooth","Bluetooth"),("settings.mqtt","MQTT / Home Assistant","MQTT / Home Assistant"),
    ("settings.mqtt.help","Optional. Audio keeps working when the broker is down.","Optional. Audio funktioniert auch, wenn der Broker ausfällt."),
    ("settings.mqtt.password_keep","unchanged","unverändert"),("settings.appearance","Appearance","Darstellung"),("settings.security","Security","Sicherheit"),
    ("settings.change_password","Change password","Passwort ändern"),("settings.token.rotate","Renew stream address","Stream-Adresse erneuern"),
    ("settings.token.help","Creates a new secret stream address. A running playback continues; new ones use the new address.","Erzeugt eine neue geheime Stream-Adresse. Laufende Wiedergabe läuft weiter, neue verwenden die neue Adresse."),
    ("settings.token.confirm","Renew the stream address now?","Stream-Adresse jetzt erneuern?"),
    ("settings.token.title","Stream address","Stream-Adresse")]: k(key,en,de)
# diagnostics
for key,en,de in [("diag.test","Test sound","Testton"),("diag.test.help","Plays a short tone on the selected player. Refused – without interrupting anything – if the player is busy.","Spielt einen kurzen Ton auf dem gewählten Player. Wird abgelehnt – ohne etwas zu unterbrechen –, wenn der Player belegt ist."),
    ("diag.test.started","Test sound started.","Testton gestartet."),("diag.test.refused","Test sound refused: {reason}","Testton abgelehnt: {reason}"),
    ("diag.lyrion","Lyrion connection","Lyrion-Verbindung"),("diag.lyrion.help","Checks that the server answers. Nothing is played.","Prüft, ob der Server antwortet. Es wird nichts abgespielt."),
    ("diag.check","Check now","Jetzt prüfen"),("diag.check.ok","Lyrion {version} answers.","Lyrion {version} antwortet."),("diag.check.fail","No answer from {server}.","Keine Antwort von {server}."),
    ("diag.states","Current state","Aktueller Zustand"),("diag.bundle","Diagnostics file","Diagnosedatei"),
    ("diag.bundle.help","Contains settings, states and logs – never passwords or the stream address.","Enthält Einstellungen, Zustände und Protokolle – nie Passwörter oder die Stream-Adresse."),
    ("diag.download","Download","Herunterladen"),("diag.faults","Critical events","Kritische Ereignisse"),("diag.no_faults","None recorded.","Keine aufgezeichnet."),("diag.logs","Recent log","Aktuelles Protokoll")]: k(key,en,de)
R = {"bridge_session_active":("the bridge is playing","die Bridge spielt gerade"),"lyrion_unreachable":("Lyrion or the player is unreachable","Lyrion oder der Player ist nicht erreichbar"),
     "target_busy":("the player is playing something","auf dem Player läuft etwas"),"start_failed":("start failed","Start fehlgeschlagen"),"not_configured":("setup is not finished","die Einrichtung ist nicht abgeschlossen")}
for r,(en,de) in R.items(): k(f"refusal.{r}",en,de)
# system
for key,en,de in [("sys.health","Health","Zustand"),("sys.cpu","CPU","CPU"),("sys.temp","Temperature","Temperatur"),("sys.load","Load","Last"),("sys.memory","Memory","Speicher"),
    ("sys.storage","Storage","Datenspeicher"),("sys.trend","Last 2 hours","Letzte 2 Stunden"),("sys.uptime","Uptime","Laufzeit"),("sys.free","free","frei"),("sys.network","Network","Netzwerk"),
    ("sys.net_value","received {rx} MB · sent {tx} MB","empfangen {rx} MB · gesendet {tx} MB"),("sys.wifi","Wi-Fi {dbm} dBm","WLAN {dbm} dBm"),("sys.mqtt","MQTT","MQTT"),
    ("mqtt.disabled","Not enabled.","Nicht aktiviert."),("mqtt.connected","Connected to {broker}.","Mit {broker} verbunden."),("mqtt.disconnected","Not connected ({error}).","Nicht verbunden ({error}).")]: k(key,en,de)
for s,(en,de) in {"ok":("normal","normal"),"warning":("warm","warm"),"critical":("too hot","zu heiß"),"unknown":("not available","nicht verfügbar")}.items(): k(f"thermal.{s}",en,de)
for d,(en,de) in {"not_configured":("Setup is not finished.","Die Einrichtung ist nicht abgeschlossen."),"lyrion_unreachable":("The Lyrion server or player is not reachable.","Lyrion-Server oder Player sind nicht erreichbar."),
    "thermal_warning":("The device is getting warm.","Das Gerät wird warm."),"thermal_critical":("The device is too hot.","Das Gerät ist zu heiß."),"mqtt_disconnected":("MQTT broker not connected.","MQTT-Broker nicht verbunden."),
    "stream_address_unavailable":("No network address for the stream yet: the bridge waits for the network before it starts playback.",
                                  "Noch keine Netzwerkadresse für den Stream: Die Bridge wartet auf das Netzwerk, bevor sie die Wiedergabe startet.")}.items(): k(f"degraded.{d}",en,de)
# flashes + errors
for key,en,de in [("flash.session_expired","Please log in again.","Bitte erneut anmelden."),("flash.account_created","Owner password set.","Besitzer-Passwort festgelegt."),
    ("flash.setup_done","Setup complete. Guests can now connect via Bluetooth.","Einrichtung abgeschlossen. Gäste können sich jetzt per Bluetooth verbinden."),
    ("flash.logged_out","Logged out.","Abgemeldet."),("flash.pairing_opened","Pairing allowed.","Kopplung erlaubt."),("flash.pairing_closed","Pairing stopped.","Kopplung beendet."),
    ("flash.audio_on","Bridge audio on.","Bridge-Wiedergabe an."),("flash.audio_off","Bridge audio off.","Bridge-Wiedergabe aus."),("flash.saved","Saved.","Gespeichert."),
    ("flash.device_removed","Phone removed.","Telefon entfernt."),("flash.device_not_found","Phone not found.","Telefon nicht gefunden."),
    ("flash.password_changed","Password changed. Other browsers must log in again.","Passwort geändert. Andere Browser müssen sich neu anmelden."),
    ("flash.token_rotated","New stream address created.","Neue Stream-Adresse erzeugt."),
    ("error.generic","Something went wrong.","Etwas ist schiefgelaufen."),("error.not_found","Page not found.","Seite nicht gefunden."),
    ("error.password_mismatch","The passwords do not match.","Die Passwörter stimmen nicht überein."),("error.setting","{field}: {reason}","{field}: {reason}"),
    ("error.bad_password","Wrong password.","Falsches Passwort."),("error.bad_setup_code","Wrong setup code.","Falscher Einrichtungscode."),
    ("error.already_set_up","The owner password is already set.","Das Besitzer-Passwort ist bereits festgelegt."),
    ("error.rate_limited","Too many attempts. Try again in {seconds} s.","Zu viele Versuche. Erneut in {seconds} s."),
    ("error.weak_password","Password not accepted: {reasons}.","Passwort nicht akzeptiert: {reasons}."),
    ("error.pw.too_short","at least 10 characters","mindestens 10 Zeichen"),("error.pw.too_long","at most 256 characters","höchstens 256 Zeichen"),
    ("error.pw.whitespace","no spaces at the start or end","keine Leerzeichen am Anfang oder Ende"),("error.pw.too_simple","too simple","zu einfach")]: k(key,en,de)
EC = {"unknown_setting":("unknown setting","unbekannte Einstellung"),"invalid_choice":("invalid choice","ungültige Auswahl"),"invalid_host":("not a valid host name or IP address","kein gültiger Hostname bzw. keine gültige IP-Adresse"),
      "invalid_name":("1 to 32 characters","1 bis 32 Zeichen"),"invalid_int":("a whole number is needed","eine ganze Zahl ist nötig"),"invalid_bool":("invalid value","ungültiger Wert"),
      "invalid_player":("invalid player","ungültiger Player"),"invalid_topic":("letters, digits, _ - / only","nur Buchstaben, Ziffern, _ - /"),"invalid_address":("invalid Bluetooth address","ungültige Bluetooth-Adresse"),
      "invalid_labels":("invalid names","ungültige Namen"),"invalid_pem":("not a PEM certificate","kein PEM-Zertifikat"),"invalid_instance_id":("invalid id","ungültige ID"),"invalid_str":("invalid text","ungültiger Text"),"too_long":("too long","zu lang"),
      "too_small":("too small","zu klein"),"too_large":("too large","zu groß"),"invalid":("invalid value","ungültiger Wert")}
for c,(en,de) in EC.items(): k(f"error.{c}",en,de)

# ---- phase-2a follow-up: product/device naming, settings sections, secure access, trust page, help texts ----
for key,en,de in [("brand.device","Device","Gerät"),("brand.tagline","Bluetooth audio for Lyrion","Bluetooth-Audio für Lyrion"),
    ("settings.lyrion","Lyrion server & player","Lyrion-Server & Player"),("settings.lyrion_server","Server","Server"),
    ("settings.access","Secure access","Sicherer Zugriff"),("settings.mqtt.connection","Broker connection","Broker-Verbindung"),
    ("settings.mqtt.controls","Control via MQTT","Steuerung per MQTT"),
    ("settings.mqtt.controls_help","Off by default: Home Assistant then only shows status. Each control is enabled separately; restrict access with broker ACLs.","Standardmäßig aus: Home Assistant zeigt dann nur den Status. Jede Steuerung wird einzeln freigegeben; Zugriff über Broker-ACLs beschränken."),
    ("summary.pairing_minutes","pairing window {minutes} min","Kopplungsfenster {minutes} Min."),("summary.read_only","read-only","nur lesend"),
    ("summary.controls","{n} control(s) enabled","{n} Steuerung(en) freigegeben"),("summary.security","Password, stream address","Passwort, Stream-Adresse"),
    ("help.override_lms","If off, a phone never interrupts other music playing on the player.","Wenn aus, unterbricht ein Telefon nie andere Musik auf dem Player."),
    ("help.pause_strategy","“Silence at once” stops the player within a fraction of a second when the phone pauses.","„Sofort Stille“ stoppt den Player Sekundenbruchteile nach der Pause am Telefon."),
    ("help.device_name","The name guests see in their Bluetooth list and that this device shows in Home Assistant.","Der Name, den Gäste in ihrer Bluetooth-Liste sehen und den das Gerät in Home Assistant zeigt."),
    ("help.mqtt_ca_pem","Only needed when the broker uses a certificate from your own CA. Verification is never switched off.","Nur nötig, wenn der Broker ein Zertifikat Ihrer eigenen CA verwendet. Die Prüfung wird nie abgeschaltet."),
    ("help.local_hostname","Network name without “.local”, e.g. hifi-bridge → hifi-bridge.local.","Netzwerkname ohne „.local“, z. B. hifi-bridge → hifi-bridge.local."),
    ("help.extra_hostnames","Further names that may be used to reach this device, e.g. a reverse-proxy name. Comma-separated.","Weitere Namen, unter denen das Gerät erreichbar ist, z. B. ein Reverse-Proxy-Name. Durch Komma getrennt."),
    ("help.tls_mode","Local CA: the device issues its own certificate; install its CA once on your devices.","Lokale CA: Das Gerät stellt sein Zertifikat selbst aus; installieren Sie die CA einmal auf Ihren Geräten."),
    ("help.trusted_proxies","Only these addresses may tell the device the original client and protocol (X-Forwarded-*).","Nur diese Adressen dürfen dem Gerät Client und Protokoll mitteilen (X-Forwarded-*)."),
    ("help.https_only","Can only be switched on from an HTTPS connection, so you cannot lock yourself out.","Lässt sich nur über eine HTTPS-Verbindung einschalten, damit Sie sich nicht aussperren."),
    ("help.custom_key_pem","The private key is stored only on this device (readable by the service only).","Der private Schlüssel wird nur auf diesem Gerät gespeichert (nur für den Dienst lesbar)."),
    ("access.status","Status","Status"),("settings.logging","Log","Protokoll"),
    ("settings.logging.help","Optional. Always encrypted (TLS, RFC 5425) with certificate verification; the log never contains passwords or the stream address.","Optional. Immer verschlüsselt (TLS, RFC 5425) mit Zertifikatsprüfung; das Protokoll enthält nie Passwörter oder die Stream-Adresse."),
    ("help.syslog_ca_pem","Only needed when the server uses a certificate from your own CA.","Nur nötig, wenn der Server ein Zertifikat Ihrer eigenen CA verwendet."),
    ("summary.syslog_off","Remote syslog off","Remote-Syslog aus"),("syslog.state","Sent {sent}, waiting {queued}, dropped {dropped}.","Gesendet {sent}, wartend {queued}, verworfen {dropped}."),("access.mdns","Name service","Namensdienst"),
    ("help.mdns_backend","Avahi is preferred; the built-in responder is only used where no Avahi service runs.","Avahi wird bevorzugt; der eingebaute Responder nur, wo kein Avahi-Dienst läuft."),("access.name","Network name","Netzwerkname"),("access.https","HTTPS","HTTPS"),
    ("access.on_port","on port {port}","auf Port {port}"),("access.off","not active","nicht aktiv"),("access.cert_until","Certificate valid until","Zertifikat gültig bis"),
    ("access.ca_fp","CA fingerprint (SHA-256)","CA-Fingerabdruck (SHA-256)"),("access.error","Certificate problem: {error}","Zertifikatsproblem: {error}"),
    ("access.trust_link","Set up trust on your devices","Vertrauen auf Ihren Geräten einrichten"),("access.renew","Renew certificate now","Zertifikat jetzt erneuern"),
    ("access.names","Names","Namen"),("access.tls","Encryption","Verschlüsselung"),("access.custom","Own certificate","Eigenes Zertifikat"),
    ("access.custom_help","Paste certificate (with chain) and key in PEM format. Used when “Own certificate” is selected.","Zertifikat (mit Kette) und Schlüssel im PEM-Format einfügen. Wird verwendet, wenn „Eigenes Zertifikat“ gewählt ist."),
    ("access.custom_install","Check and install","Prüfen und installieren"),
    ("flash.cert_installed","Certificate installed.","Zertifikat installiert."),("flash.cert_renewed","Certificate renewed.","Zertifikat erneuert."),
    ("error.https_only_needs_https","Open this page via HTTPS first – that proves your device trusts the certificate.","Öffnen Sie diese Seite zuerst über HTTPS – das zeigt, dass Ihr Gerät dem Zertifikat vertraut."),
    ("error.cert.pem_format","Certificate or key is not in PEM format.","Zertifikat oder Schlüssel ist nicht im PEM-Format."),
    ("error.cert.key_mismatch","The key does not belong to the certificate.","Der Schlüssel gehört nicht zum Zertifikat."),
    ("error.cert.expired","The certificate has expired.","Das Zertifikat ist abgelaufen."),
    ("error.cert.name_not_covered","The certificate does not cover this device’s name.","Das Zertifikat deckt den Namen dieses Geräts nicht ab."),
    ("error.cert.openssl not available","openssl is not available on this device.","openssl ist auf diesem Gerät nicht verfügbar."),
    ("trust.title","Trust this device","Diesem Gerät vertrauen"),
    ("trust.intro","Your browser only shows the lock without warnings after you install this device’s own certificate authority (CA) once. It is valid only for local names (.local, .home.arpa, .lan, .internal) and private addresses – it cannot vouch for any website on the internet.","Ihr Browser zeigt das Schloss erst ohne Warnung, nachdem Sie die eigene Zertifizierungsstelle (CA) dieses Geräts einmal installiert haben. Sie gilt nur für lokale Namen (.local, .home.arpa, .lan, .internal) und private Adressen – für Websites im Internet kann sie nichts bestätigen."),
    ("trust.fingerprint","Fingerprint","Fingerabdruck"),
    ("trust.verify","Before trusting it, compare this fingerprint with the one shown during setup on the device (or later by the flash tool). Never accept a browser warning without this check.","Vergleichen Sie diesen Fingerabdruck vor dem Vertrauen mit dem bei der Einrichtung am Gerät angezeigten (bzw. später vom Flash-Tool). Akzeptieren Sie nie eine Browserwarnung ohne diese Prüfung."),
    ("trust.download_pem","Download CA (.crt)","CA herunterladen (.crt)"),("trust.download_der","Download CA for Android (.cer)","CA für Android herunterladen (.cer)"),
    ("trust.scope","The private key of this CA never leaves the device.","Der private Schlüssel dieser CA verlässt das Gerät nie."),
    ("trust.open_https","Then open {url}","Danach {url} öffnen"),
    ("trust.ios","iPhone / iPad","iPhone / iPad"),("trust.ios.1","Open this page in Safari and download the .crt file; allow the profile download.","Diese Seite in Safari öffnen und die .crt-Datei laden; den Profil-Download erlauben."),
    ("trust.ios.2","Settings → General → VPN & Device Management → install the profile.","Einstellungen → Allgemein → VPN und Geräteverwaltung → Profil installieren."),
    ("trust.ios.3","Settings → General → About → Certificate Trust Settings → enable full trust for “Hi-Fi Bridge local CA”.","Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen → volles Vertrauen für „Hi-Fi Bridge local CA“ aktivieren."),
    ("trust.android","Android","Android"),("trust.android.1","Download the .cer file.","Die .cer-Datei herunterladen."),
    ("trust.android.2","Settings → Security → Encryption & credentials → Install a certificate → CA certificate.","Einstellungen → Sicherheit → Verschlüsselung & Anmeldedaten → Zertifikat installieren → CA-Zertifikat."),
    ("trust.android.3","Confirm the warning only after checking the fingerprint; then reopen the HTTPS address.","Die Warnung erst nach Prüfung des Fingerabdrucks bestätigen; dann die HTTPS-Adresse neu öffnen."),
    ("trust.windows","Windows","Windows"),("trust.windows.1","Download the .crt file and open it.","Die .crt-Datei herunterladen und öffnen."),
    ("trust.windows.2","Install certificate → Current user → place it in “Trusted Root Certification Authorities”.","Zertifikat installieren → Aktueller Benutzer → in „Vertrauenswürdige Stammzertifizierungsstellen“ ablegen."),
    ("trust.windows.3","Restart the browser.","Den Browser neu starten."),
    ("trust.macos","macOS","macOS"),("trust.macos.1","Download the .crt file and open it in Keychain Access (login keychain).","Die .crt-Datei herunterladen und in der Schlüsselbundverwaltung (Anmeldung) öffnen."),
    ("trust.macos.2","Double-click “Hi-Fi Bridge local CA” → Trust → “Always Trust”.","„Hi-Fi Bridge local CA“ doppelklicken → Vertrauen → „Immer vertrauen“."),
    ("trust.macos.3","Close the window and confirm with your password.","Fenster schließen und mit dem Passwort bestätigen."),
    ("trust.linux","Linux / Firefox","Linux / Firefox"),("trust.linux.1","Firefox: Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import.","Firefox: Einstellungen → Datenschutz & Sicherheit → Zertifikate → Zertifikate anzeigen → Zertifizierungsstellen → Importieren."),
    ("trust.linux.2","Tick “Trust this CA to identify websites”.","„Dieser CA vertrauen, um Websites zu identifizieren“ anhaken."),
    ("trust.linux.3","System-wide (Debian/Ubuntu): copy to /usr/local/share/ca-certificates/ and run update-ca-certificates.","Systemweit (Debian/Ubuntu): nach /usr/local/share/ca-certificates/ kopieren und update-ca-certificates ausführen.")]: k(key,en,de)
F2 = {"local_hostname":("Network name","Netzwerkname"),"mdns_enabled":("Announce the name in the network (mDNS)","Namen im Netzwerk bekannt geben (mDNS)"),
      "extra_hostnames":("Further host names","Weitere Hostnamen"),"tls_mode":("Certificate","Zertifikat"),"https_only":("HTTPS only","Nur HTTPS"),
      "trusted_proxies":("Trusted reverse proxies","Vertrauenswürdige Reverse Proxies"),"custom_cert_pem":("Certificate (PEM)","Zertifikat (PEM)"),
      "custom_key_pem":("Private key (PEM)","Privater Schlüssel (PEM)"),
      "mdns_backend":("Name service","Namensdienst"),
      "syslog_enabled":("Send the log to a syslog server","Protokoll an einen Syslog-Server senden"),"syslog_host":("Syslog server","Syslog-Server"),
      "syslog_port":("Port (TLS)","Port (TLS)"),"syslog_ca_pem":("Server CA certificate (PEM, optional)","CA-Zertifikat des Servers (PEM, optional)")}
for f,(en,de) in F2.items(): k(f"field.{f}",en,de)
for c,(en,de) in {"tls_mode.local_ca":("Local CA of this device (recommended)","Lokale CA dieses Geräts (empfohlen)"),
                  "tls_mode.custom":("Own certificate","Eigenes Zertifikat"),
                  "mdns_backend.auto":("Automatic (Avahi if available)","Automatisch (Avahi, falls vorhanden)"),
                  "mdns_backend.avahi":("Avahi (system service)","Avahi (Systemdienst)"),
                  "mdns_backend.builtin":("Built-in responder","Eingebauter Responder"),"tls_mode.proxy":("Reverse proxy terminates TLS","Reverse Proxy übernimmt TLS")}.items(): k(f"choice.{c}",en,de)
for c,(en,de) in {"invalid_hostname":("letters, digits and hyphens only","nur Buchstaben, Ziffern und Bindestriche"),
                  "invalid_network":("not a valid address or network","keine gültige Adresse bzw. kein gültiges Netz")}.items(): k(f"error.{c}",en,de)

k("foot.source", "Source code (AGPL-3.0-only)", "Quellcode (AGPL-3.0-only)")

for lang, dct in (("en", E), ("de", D)):
    json.dump(dict(sorted(dct.items())), open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{lang}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(E), len(D))
