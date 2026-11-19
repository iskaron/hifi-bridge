"""Source of the update/backup page dictionaries: edit here, then run `python3 _generate_update.py` (rewrites update.en.json/update.de.json).
i18n.strings() merges every `<module>.<lang>.json` into the main dictionary; the tests require identical keys and placeholders."""
import json
import os

E, D = {}, {}


def k(key, en, de):
    if key in E:
        raise SystemExit(f"duplicate key {key}")
    E[key] = en
    D[key] = de


k("settings.update", "Software update", "Software-Update")
k("settings.backup", "Backup and restore", "Sicherung und Wiederherstellung")
k("upd.title", "Software update", "Software-Update")
k("upd.software", "Installed software", "Installierte Software")
k("upd.version", "Running version", "Laufende Version")
k("upd.slots", "System slots", "Systembereiche")
k("upd.slot.name", "Slot {slot}", "Bereich {slot}")
for s, en, de in [("good", "bootable", "startfähig"), ("bad", "not bootable", "nicht startfähig"), ("unknown", "state unknown", "Zustand unbekannt"),
                  ("empty", "empty", "leer"), ("running", "running now", "läuft gerade"), ("next", "starts next", "startet als Nächstes")]:
    k(f"upd.slot.{s}", en, de)
k("upd.rauc_unavailable", "The update service (RAUC) is not reachable.", "Der Update-Dienst (RAUC) ist nicht erreichbar.")
k("upd.trust", "Accepted update signatures", "Akzeptierte Update-Signaturen")
for m, en, de in [("official", "official releases only", "nur offizielle Releases"), ("own", "own update CA only", "nur eigene Update-CA"),
                  ("official-and-own", "official releases and own update CA", "offizielle Releases und eigene Update-CA"),
                  ("none", "not recorded in this system", "in diesem System nicht hinterlegt")]:
    k(f"upd.trust.{m}", en, de)
k("upd.trust.note", "Installing an official bundle replaces the whole system including the trusted keys: afterwards this device accepts only official updates.",
  "Ein offizielles Paket ersetzt das ganze System einschließlich der vertrauten Schlüssel: Danach akzeptiert dieses Gerät nur noch offizielle Updates.")
k("upd.source", "Official update source", "Offizielle Update-Quelle")
k("upd.source.none", "none – updates by upload only", "keine – Updates nur per Upload")
k("upd.certs", "Trusted certificates", "Vertraute Zertifikate")
k("upd.cert.official", "Official", "Offiziell")
k("upd.cert.own", "Own", "Eigene")
k("upd.status", "Status", "Status")
for p, en, de in [("idle", "No update in progress.", "Kein Update in Arbeit."), ("downloading", "Downloading the update …", "Update wird geladen …"),
                  ("uploading", "Receiving the update …", "Update wird empfangen …"), ("verifying", "Checking the signature …", "Signatur wird geprüft …"),
                  ("staged", "Update ready to install.", "Update bereit zur Installation."), ("installing", "Installing – the device keeps playing meanwhile.", "Wird installiert – das Gerät spielt währenddessen weiter."),
                  ("installed", "Installed. Restart to use the new version.", "Installiert. Neustart nötig, um die neue Version zu nutzen."),
                  ("rebooting", "Restarting …", "Startet neu …"), ("switching", "Previous version selected for the next start.", "Vorherige Version für den nächsten Start gewählt."),
                  ("confirming", "Checking the new version after the start …", "Neue Version wird nach dem Start geprüft …")]:
    k(f"upd.phase.{p}", en, de)
for r, en, de in [("updated", "Update from {from} to {to} completed.", "Update von {from} auf {to} abgeschlossen."),
                  ("rolled_back", "The update to {from} did not start correctly. The device returned to {to}.", "Das Update auf {from} ist nicht richtig gestartet. Das Gerät läuft wieder mit {to}."),
                  ("switched", "Now running {to} (switched from {from}).", "Läuft jetzt mit {to} (gewechselt von {from})."),
                  ("cancelled", "Restart declined: the device stays on {from}. Version {to} is installed but not started.", "Neustart abgelehnt: Das Gerät bleibt bei {from}. Version {to} ist installiert, wird aber nicht gestartet.")]:
    k(f"upd.result.{r}", en, de)
k("upd.reason.boot_failed", "It did not finish starting.", "Es hat den Start nicht abgeschlossen.")
k("upd.reason.health_check_failed", "The check after the start failed.", "Die Prüfung nach dem Start ist fehlgeschlagen.")
k("upd.dismiss", "Dismiss", "Ausblenden")
k("upd.restored", "The settings snapshot of version {version} was restored.", "Der Einstellungs-Schnappschuss der Version {version} wurde wiederhergestellt.")
k("upd.progress", "Progress", "Fortschritt")
k("upd.staged", "Version {version} ({name}, {size}).", "Version {version} ({name}, {size}).")
k("upd.staged.verified", "The signature was verified against the trusted keys of this device.", "Die Signatur wurde mit den vertrauten Schlüsseln dieses Geräts geprüft.")
k("upd.install", "Install version {version}", "Version {version} installieren")
k("upd.install.confirm", "Install version {version}? The current settings are saved first. The new version starts only after you restart.",
  "Version {version} installieren? Die aktuellen Einstellungen werden vorher gesichert. Die neue Version startet erst nach Ihrem Neustart.")
k("upd.discard", "Discard", "Verwerfen")
k("upd.reboot_needed.installed", "Version {version} is installed. Restart now? Playback stops for about a minute.",
  "Version {version} ist installiert. Jetzt neu starten? Die Wiedergabe stoppt für etwa eine Minute.")
k("upd.reboot_needed.switching", "Version {version} starts after the restart.", "Version {version} startet nach dem Neustart.")
k("upd.reboot", "Restart now", "Jetzt neu starten")
k("upd.reboot.confirm", "Restart the device now? Bluetooth audio stops until it is back.", "Gerät jetzt neu starten? Die Bluetooth-Wiedergabe stoppt, bis es wieder da ist.")
k("upd.cancel", "Keep the current version", "Aktuelle Version behalten")
k("upd.cancel_download", "Cancel download", "Download abbrechen")
k("upd.refresh", "Refresh", "Aktualisieren")
k("upd.check", "Check for updates", "Nach Updates suchen")
k("upd.check.help", "Asks the official release page once. Nothing is downloaded or installed without you.",
  "Fragt einmal die offizielle Release-Seite. Ohne Sie wird nichts geladen oder installiert.")
k("upd.check.own_only", "This system accepts only own update bundles; install them by upload.", "Dieses System akzeptiert nur eigene Update-Pakete; installieren Sie sie per Upload.")
k("upd.check.unavailable", "No official update source is recorded in this system.", "In diesem System ist keine offizielle Update-Quelle hinterlegt.")
for s, en, de in [("update_available", "Version {version} is available (running: {current}).", "Version {version} ist verfügbar (läuft: {current})."),
                  ("up_to_date", "Up to date: {current} is the newest release.", "Aktuell: {current} ist das neueste Release."),
                  ("no_bundle", "No release contains an update for this board yet.", "Noch kein Release enthält ein Update für diese Platine."),
                  ("no_source", "No official update source.", "Keine offizielle Update-Quelle."),
                  ("not_public", "The release page is not publicly reachable (the repository is still private).", "Die Release-Seite ist nicht öffentlich erreichbar (das Repository ist noch privat)."),
                  ("rate_limited", "The release page refused too many requests. Try again later.", "Die Release-Seite hat zu viele Anfragen abgelehnt. Später erneut versuchen."),
                  ("unreachable", "The release page could not be reached (internet connection?).", "Die Release-Seite war nicht erreichbar (Internetverbindung?)."),
                  ("invalid_response", "The release page sent an unexpected answer.", "Die Release-Seite hat unerwartet geantwortet."),
                  ("insecure_source", "The recorded update source does not use HTTPS and is not used.", "Die hinterlegte Update-Quelle nutzt kein HTTPS und wird nicht verwendet.")]:
    k(f"upd.check.{s}", en, de)
k("upd.check.at", "Last check: {time}", "Zuletzt geprüft: {time}")
k("upd.check.now", "Check now", "Jetzt prüfen")
k("upd.download", "Download {version} ({size})", "{version} laden ({size})")
k("upd.resume", "Resume download of {version} ({size})", "Download von {version} fortsetzen ({size})")
k("upd.release_notes", "Release notes", "Versionshinweise")
k("upd.download.started", "Download started.", "Download gestartet.")
k("upd.download.cancelled", "Download cancelled. It can be resumed.", "Download abgebrochen. Er kann fortgesetzt werden.")
k("upd.upload", "Upload an update bundle", "Update-Paket hochladen")
k("upd.upload.file", "Update bundle (.raucb)", "Update-Paket (.raucb)")
k("upd.upload.help", "Only bundles signed with a key this device trusts are accepted (at most {size}). Installation starts only after you confirm.",
  "Angenommen werden nur Pakete, die mit einem von diesem Gerät vertrauten Schlüssel signiert sind (höchstens {size}). Installiert wird erst nach Ihrer Bestätigung.")
k("upd.upload.button", "Upload and check", "Hochladen und prüfen")
k("upd.uploaded", "Bundle {version} received and verified.", "Paket {version} empfangen und geprüft.")
k("upd.rollback", "Previous version", "Vorherige Version")
k("upd.rollback.text", "Version {version} is still kept in slot {slot} and can be started instead.", "Version {version} liegt noch in Bereich {slot} und kann stattdessen gestartet werden.")
k("upd.rollback.button", "Start version {version} next time", "Beim nächsten Start Version {version} nutzen")
k("upd.rollback.confirm", "Use version {version} from the next start on? Settings stay as they are; you can restore the snapshot taken before the update afterwards.",
  "Ab dem nächsten Start Version {version} nutzen? Die Einstellungen bleiben, wie sie sind; den vor dem Update gesicherten Schnappschuss können Sie danach wiederherstellen.")
k("upd.rollback.none", "No other bootable version is available.", "Keine andere startfähige Version vorhanden.")
k("upd.rollback.selected", "Previous version selected. Restart to use it.", "Vorherige Version gewählt. Zum Nutzen neu starten.")
k("upd.unmanaged", "Updates are installed by RAUC on the appliance image; this system has no update service.",
  "Updates installiert RAUC im Geräte-Image; dieses System hat keinen Update-Dienst.")
k("upd.summary", "{version} · {phase}", "{version} · {phase}")
k("upd.summary.unmanaged", "{version} · no update service", "{version} · kein Update-Dienst")
k("upd.summary.reboot", "{version} · restart needed", "{version} · Neustart nötig")
k("upd.discarded", "Bundle discarded.", "Paket verworfen.")
k("upd.install.started", "Installation started.", "Installation gestartet.")
k("upd.reboot.started", "Restarting. This page reconnects when the device is back.", "Neustart läuft. Diese Seite verbindet sich wieder, sobald das Gerät zurück ist.")
k("upd.cancelled", "The device stays on the current version.", "Das Gerät bleibt bei der aktuellen Version.")
k("upd.live.restarting", "Restarting … waiting for the device.", "Neustart … warte auf das Gerät.")
k("upd.live.back", "The device is back.", "Das Gerät ist wieder da.")
k("upd.live.uploading", "Uploading … {pct} %", "Wird hochgeladen … {pct} %")
k("upd.error.generic", "The update step failed ({code}).", "Der Update-Schritt ist fehlgeschlagen ({code}).")
for c, en, de in [
        ("busy", "Another update step is in progress.", "Ein anderer Update-Schritt läuft gerade."),
        ("reboot_unavailable", "The device cannot restart itself here (no restart helper).", "Das Gerät kann sich hier nicht selbst neu starten (kein Neustart-Helfer)."),
        ("no_space", "Not enough free space on the data partition.", "Nicht genug freier Platz auf der Datenpartition."),
        ("too_large", "The bundle is too large.", "Das Paket ist zu groß."),
        ("signature_invalid", "Refused: the bundle is not signed by a key this device trusts, or it is damaged.", "Abgelehnt: Das Paket ist nicht mit einem vertrauten Schlüssel signiert oder beschädigt."),
        ("wrong_compatible", "Refused: the bundle is for a different board.", "Abgelehnt: Das Paket ist für eine andere Platine."),
        ("rauc_unavailable", "The update service (RAUC) is not reachable.", "Der Update-Dienst (RAUC) ist nicht erreichbar."),
        ("no_file", "No bundle was received.", "Es wurde kein Paket empfangen."),
        ("no_update", "Check for updates first.", "Bitte zuerst nach Updates suchen."),
        ("no_source", "No official update source.", "Keine offizielle Update-Quelle."),
        ("install_failed", "The installation failed; the device keeps the current version.", "Die Installation ist fehlgeschlagen; das Gerät behält die aktuelle Version."),
        ("install_interrupted", "The installation was interrupted; the device keeps the current version. You can try again.", "Die Installation wurde unterbrochen; das Gerät behält die aktuelle Version. Sie können es erneut versuchen."),
        ("rollback_unavailable", "No other bootable version is available.", "Keine andere startfähige Version vorhanden."),
        ("no_snapshot", "There is no settings snapshot of this version.", "Es gibt keinen Einstellungs-Schnappschuss dieser Version."),
        ("unreachable", "The download source could not be reached.", "Die Download-Quelle war nicht erreichbar."),
        ("http_error", "The download source answered with an error.", "Die Download-Quelle hat mit einem Fehler geantwortet."),
        ("size_mismatch", "The download has the wrong size.", "Der Download hat die falsche Größe."),
        ("digest_mismatch", "The download is damaged (checksum).", "Der Download ist beschädigt (Prüfsumme)."),
        ("interrupted", "The download was interrupted. It can be resumed.", "Der Download wurde unterbrochen. Er kann fortgesetzt werden."),
        ("download_failed", "The download failed.", "Der Download ist fehlgeschlagen."),
        ("upload_failed", "The upload failed.", "Der Upload ist fehlgeschlagen."),
        ("bad_request", "The upload was not understood.", "Der Upload wurde nicht verstanden."),
        ("truncated", "The upload was incomplete.", "Der Upload war unvollständig."),
        ("timeout", "The upload took too long.", "Der Upload hat zu lange gedauert."),
        ("too_many_parts", "The upload was not understood.", "Der Upload wurde nicht verstanden."),
        ("field_too_large", "The upload was not understood.", "Der Upload wurde nicht verstanden.")]:
    k(f"upd.error.{c}", en, de)
k("bak.title", "Backup and restore", "Sicherung und Wiederherstellung")
k("bak.summary", "Export or import the settings", "Einstellungen exportieren oder importieren")
k("bak.export", "Export the settings", "Einstellungen exportieren")
k("bak.export.help", "Downloads a file with all settings of this device. Keep it somewhere safe.",
  "Lädt eine Datei mit allen Einstellungen dieses Geräts herunter. Bewahren Sie sie sicher auf.")
k("bak.include_secrets", "Include secrets, encrypted with a passphrase", "Geheimnisse einschließen, mit Passphrase verschlüsselt")
k("bak.include_secrets.help", "Without this option the file contains no secret, and these must be set again after a restore: {secrets}.",
  "Ohne diese Option enthält die Datei kein Geheimnis; nach einer Wiederherstellung müssen diese neu gesetzt werden: {secrets}.")
k("bak.secret.admin_password_hash", "owner password", "Besitzer-Passwort")
k("bak.secret.stream_token", "stream address token", "Token der Stream-Adresse")
k("bak.secret.mqtt_password", "MQTT password", "MQTT-Passwort")
k("bak.passphrase", "Passphrase (at least 10 characters)", "Passphrase (mindestens 10 Zeichen)")
k("bak.passphrase_confirm", "Repeat the passphrase", "Passphrase wiederholen")
k("bak.never", "Never exported: the key of this device's HTTPS certificate authority, sessions, setup codes and an own TLS key.",
  "Nie exportiert: der Schlüssel der HTTPS-Zertifizierungsstelle dieses Geräts, Sitzungen, Einrichtungscodes und ein eigener TLS-Schlüssel.")
k("bak.export.button", "Download backup", "Sicherung herunterladen")
k("bak.import", "Restore from a backup", "Aus einer Sicherung wiederherstellen")
k("bak.import.file", "Backup file (.json)", "Sicherungsdatei (.json)")
k("bak.import.passphrase", "Passphrase (only for a backup with secrets)", "Passphrase (nur für eine Sicherung mit Geheimnissen)")
k("bak.import.help", "The file is checked completely first; nothing changes if anything is wrong. A restored owner password ends all sessions.",
  "Die Datei wird zuerst vollständig geprüft; ist etwas falsch, ändert sich nichts. Ein wiederhergestelltes Besitzer-Passwort beendet alle Sitzungen.")
k("bak.import.confirm", "Replace the current settings with the backup?", "Die aktuellen Einstellungen durch die Sicherung ersetzen?")
k("bak.import.button", "Restore", "Wiederherstellen")
k("bak.restored", "Backup restored: {n} setting(s) changed.", "Sicherung wiederhergestellt: {n} Einstellung(en) geändert.")
k("bak.skipped", "Not known to this version and skipped: {keys}.", "Dieser Version unbekannt und übersprungen: {keys}.")
k("bak.secrets_restored", "Secrets restored.", "Geheimnisse wiederhergestellt.")
k("bak.secrets_not_included", "The backup contained no secrets.", "Die Sicherung enthielt keine Geheimnisse.")
k("bak.snapshot", "Settings before the last update", "Einstellungen vor dem letzten Update")
k("bak.snapshot.help", "Before every installation the device saves its settings. After returning to an older version you can restore them; this restarts the device.",
  "Vor jeder Installation sichert das Gerät seine Einstellungen. Nach der Rückkehr zu einer älteren Version können Sie sie wiederherstellen; dabei startet das Gerät neu.")
k("bak.snapshot.text", "Snapshot of version {version} from {time}.", "Schnappschuss der Version {version} vom {time}.")
k("bak.snapshot.button", "Restore snapshot and restart", "Schnappschuss wiederherstellen und neu starten")
k("bak.snapshot.confirm", "Replace the current settings with the snapshot and restart?", "Die aktuellen Einstellungen durch den Schnappschuss ersetzen und neu starten?")
k("bak.snapshot.requested", "The snapshot is restored at the restart.", "Der Schnappschuss wird beim Neustart wiederhergestellt.")
k("bak.snapshot.none", "No snapshot of the running version exists.", "Es gibt keinen Schnappschuss der laufenden Version.")
k("bak.snapshot.started", "Restoring the snapshot: the device restarts.", "Schnappschuss wird wiederhergestellt: Das Gerät startet neu.")
k("bak.error.generic", "The backup could not be processed ({code}).", "Die Sicherung konnte nicht verarbeitet werden ({code}).")
for c, en, de in [
        ("invalid_file", "This is not a valid backup file.", "Das ist keine gültige Sicherungsdatei."),
        ("wrong_format", "This file is not a Hi-Fi Bridge backup.", "Diese Datei ist keine Hi-Fi-Bridge-Sicherung."),
        ("too_new", "This backup comes from a newer version. Update the device first.", "Diese Sicherung stammt von einer neueren Version. Bitte zuerst das Gerät aktualisieren."),
        ("invalid_settings", "The backup contains invalid settings; nothing was changed", "Die Sicherung enthält ungültige Einstellungen; nichts wurde geändert"),
        ("passphrase_required", "This backup contains secrets: enter its passphrase.", "Diese Sicherung enthält Geheimnisse: bitte ihre Passphrase eingeben."),
        ("wrong_passphrase", "Wrong passphrase, or the file was changed.", "Falsche Passphrase, oder die Datei wurde verändert."),
        ("weak_passphrase", "The passphrase needs at least 10 characters.", "Die Passphrase braucht mindestens 10 Zeichen."),
        ("passphrase_mismatch", "The passphrases do not match.", "Die Passphrasen stimmen nicht überein."),
        ("bad_request", "The upload was not understood.", "Der Upload wurde nicht verstanden."),
        ("truncated", "The upload was incomplete.", "Der Upload war unvollständig."),
        ("timeout", "The upload took too long.", "Der Upload hat zu lange gedauert."),
        ("too_large", "The file is too large for a backup.", "Die Datei ist zu groß für eine Sicherung.")]:
    k(f"bak.error.{c}", en, de)

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for lang, dct in (("en", E), ("de", D)):
        with open(os.path.join(here, f"update.{lang}.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(dct.items())), fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print(len(E), len(D))
