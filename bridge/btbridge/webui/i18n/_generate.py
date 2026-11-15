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
    ("sys.storage","Storage","Datenspeicher"),("sys.uptime","Uptime","Laufzeit"),("sys.free","free","frei"),("sys.network","Network","Netzwerk"),
    ("sys.net_value","received {rx} MB · sent {tx} MB","empfangen {rx} MB · gesendet {tx} MB"),("sys.wifi","Wi-Fi {dbm} dBm","WLAN {dbm} dBm"),("sys.mqtt","MQTT","MQTT"),
    ("mqtt.disabled","Not enabled.","Nicht aktiviert."),("mqtt.connected","Connected to {broker}.","Mit {broker} verbunden."),("mqtt.disconnected","Not connected ({error}).","Nicht verbunden ({error}).")]: k(key,en,de)
for s,(en,de) in {"ok":("normal","normal"),"warning":("warm","warm"),"critical":("too hot","zu heiß"),"unknown":("not available","nicht verfügbar")}.items(): k(f"thermal.{s}",en,de)
for d,(en,de) in {"not_configured":("Setup is not finished.","Die Einrichtung ist nicht abgeschlossen."),"lyrion_unreachable":("The Lyrion server or player is not reachable.","Lyrion-Server oder Player sind nicht erreichbar."),
    "thermal_warning":("The device is getting warm.","Das Gerät wird warm."),"thermal_critical":("The device is too hot.","Das Gerät ist zu heiß."),"mqtt_disconnected":("MQTT broker not connected.","MQTT-Broker nicht verbunden.")}.items(): k(f"degraded.{d}",en,de)
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
for lang, dct in (("en", E), ("de", D)):
    json.dump(dict(sorted(dct.items())), open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{lang}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(E), len(D))
