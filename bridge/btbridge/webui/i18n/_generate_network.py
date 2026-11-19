"""Source of the network page dictionaries: edit here, then run `python3 _generate_network.py` (rewrites network.en.json/network.de.json).
i18n.strings() merges every `<module>.<lang>.json` into the main dictionary; the tests require identical keys and placeholders."""
import json
import os

E, D = {}, {}


def k(key, en, de):
    E[key] = en
    D[key] = de


k("settings.network", "Network", "Netzwerk")
k("net.title", "Network", "Netzwerk")
k("net.status", "Connection", "Verbindung")
k("net.mode", "Mode", "Modus")
for s, en, de in [("connected", "Wi-Fi connected", "WLAN verbunden"), ("connecting", "Connecting to Wi-Fi …", "WLAN wird verbunden …"),
                  ("probing", "Trying the Wi-Fi again …", "WLAN wird erneut versucht …"), ("hotspot", "Setup hotspot active", "Einrichtungs-Hotspot aktiv"),
                  ("off", "Wi-Fi off (Ethernet only)", "WLAN aus (nur Ethernet)"), ("unavailable", "No Wi-Fi hardware found", "Keine WLAN-Hardware gefunden"),
                  ("unmanaged", "Managed by the host system", "Vom Host-System verwaltet"),
                  ("agent_unreachable", "Network service not reachable", "Netzwerkdienst nicht erreichbar")]:
    k("net.state." + s, en, de)
k("net.setup.back", "Back to the setup", "Zurück zur Einrichtung")
k("net.setup.link", "Network settings", "Netzwerkeinstellungen")
k("net.setup.hint", "Not connected to your home network yet – for example only to the device's setup hotspot, or no Ethernet cable? "
  "Then no Lyrion server can be found. Set up the network first; the setup continues here afterwards.",
  "Noch nicht mit Ihrem Heimnetz verbunden – zum Beispiel nur mit dem Einrichtungs-Hotspot des Geräts, oder ohne Ethernet-Kabel? "
  "Dann kann kein Lyrion-Server gefunden werden. Richten Sie zuerst das Netzwerk ein; danach geht die Einrichtung hier weiter.")
k("net.setup.note", "The setup is not finished yet. After joining your home Wi-Fi, connect your phone to the same Wi-Fi, open the device's "
  "page again (its name or the address your router shows), confirm the new network settings and continue with the setup.",
  "Die Einrichtung ist noch nicht abgeschlossen. Verbinden Sie nach dem Wechsel ins Heim-WLAN Ihr Telefon mit demselben WLAN, öffnen "
  "Sie die Seite des Geräts erneut (sein Name oder die Adresse, die Ihr Router anzeigt), bestätigen Sie die neuen Netzwerkeinstellungen "
  "und setzen Sie die Einrichtung fort.")
k("net.wifi", "Wi-Fi network", "WLAN")
k("net.last_problem", "Last problem", "Letztes Problem")
k("net.fallback", "Setup hotspot", "Einrichtungs-Hotspot")
k("net.fallback_in", "opens in {seconds} s if the Wi-Fi does not connect", "öffnet in {seconds} s, falls das WLAN nicht verbindet")
k("net.hotspot", "Hotspot", "Hotspot")
k("net.hotspot.active", "“{ssid}” – setup page http://{address}/ – {n} device(s) connected",
  "„{ssid}“ – Einrichtungsseite http://{address}/ – {n} Gerät(e) verbunden")
k("net.retry", "Wi-Fi retry", "Neuer WLAN-Versuch")
k("net.retry_in", "in about {minutes} min (only while no device uses the hotspot)", "in etwa {minutes} min (nur solange kein Gerät den Hotspot nutzt)")
k("net.addresses_of", "Addresses ({name})", "Adressen ({name})")
k("net.hostname", "Host name", "Gerätename im Netz")
k("net.change", "change", "ändern")
k("net.time", "Clock", "Uhrzeit")
k("net.time.synced", "synchronised ({now})", "synchronisiert ({now})")
k("net.time.not_synced", "not synchronised yet ({now}) – certificates are re-checked after the first synchronisation",
  "noch nicht synchronisiert ({now}) – Zertifikate werden nach der ersten Synchronisation erneut geprüft")
k("net.time.unknown", "status unknown ({now})", "Status unbekannt ({now})")
k("net.firewall", "Firewall", "Firewall")
k("net.firewall.applied", "active (only the web interface, the stream and name service are reachable)",
  "aktiv (nur Weboberfläche, Stream und Namensdienst erreichbar)")
k("net.firewall.failed", "could not be applied – see diagnostics", "konnte nicht angewendet werden – siehe Diagnose")
k("net.config_error", "Settings file", "Einstellungsdatei")
k("net.config_error.text", "unreadable – Ethernet with automatic address only", "nicht lesbar – nur Ethernet mit automatischer Adresse")
k("net.unmanaged.help", "This installation has no network service of its own (for example the Docker lab). The addresses are shown; changes are made on the host.",
  "Diese Installation hat keinen eigenen Netzwerkdienst (z. B. das Docker-Labor). Die Adressen werden angezeigt; Änderungen erfolgen auf dem Host.")
for s, en, de in [("excellent", "excellent signal", "sehr gutes Signal"), ("good", "good signal", "gutes Signal"), ("fair", "fair signal", "mittleres Signal"),
                  ("weak", "weak signal", "schwaches Signal"), ("unknown", "signal unknown", "Signal unbekannt")]:
    k("net.signal." + s, en, de)
for s, en, de in [("wrong_password", "The Wi-Fi password was not accepted.", "Das WLAN-Passwort wurde nicht akzeptiert."),
                  ("not_found", "The Wi-Fi network was not found (out of range, switched off, or the name is different).",
                   "Das WLAN wurde nicht gefunden (außer Reichweite, ausgeschaltet oder anderer Name)."),
                  ("rejected", "The access point refused the connection.", "Der Access Point hat die Verbindung abgelehnt."),
                  ("no_address", "Connected, but no address was assigned (DHCP).", "Verbunden, aber keine Adresse erhalten (DHCP)."),
                  ("timeout", "The connection did not come up in time.", "Die Verbindung kam nicht rechtzeitig zustande."),
                  ("not_confirmed", "The change was not confirmed in time, so the previous settings are active again.",
                   "Die Änderung wurde nicht rechtzeitig bestätigt, daher gelten wieder die vorherigen Einstellungen."),
                  ("reverted", "The change was undone.", "Die Änderung wurde rückgängig gemacht.")]:
    k("net.reason." + s, en, de)
k("net.trial.title", "Keep the new network settings?", "Neue Netzwerkeinstellungen behalten?")
k("net.trial.text", "If you can read this page over the new connection, keep the settings. Otherwise the device returns to the previous settings by itself.",
  "Wenn Sie diese Seite über die neue Verbindung sehen, behalten Sie die Einstellungen. Sonst kehrt das Gerät von selbst zu den vorherigen zurück.")
k("net.trial.remaining", "Automatic undo in {time}", "Automatisches Zurücksetzen in {time}")
k("live.net_remaining", "Automatic undo in {time}", "Automatisches Zurücksetzen in {time}")
k("net.trial.keep", "Keep settings", "Einstellungen behalten")
k("net.trial.undo", "Undo now", "Jetzt zurücksetzen")
k("net.trial.rolled_back", "Previous network settings restored", "Vorherige Netzwerkeinstellungen wiederhergestellt")
k("net.trial.had_address", "The device had reached the new network as {addresses} – try that address next time.",
  "Das Gerät war im neuen Netz als {addresses} erreichbar – versuchen Sie beim nächsten Mal diese Adresse.")
k("net.trial.kept", "Network settings kept.", "Netzwerkeinstellungen behalten.")
k("net.busy", "The network service did not answer in time – the device may still be applying the change. Reload this page in a moment: "
  "if “Keep the new network settings?” appears, confirm it.",
  "Der Netzwerkdienst hat nicht rechtzeitig geantwortet – das Gerät übernimmt die Änderung eventuell noch. Laden Sie diese Seite gleich "
  "neu: Erscheint „Neue Netzwerkeinstellungen behalten?“, bestätigen Sie sie.")
k("net.agent_unreachable.help", "The network service of the device is not reachable, so nothing was changed. Wait a minute and reload this "
  "page; if it stays like this, restart the device (unplug it briefly). The current network settings stay in effect.",
  "Der Netzwerkdienst des Geräts ist nicht erreichbar, daher wurde nichts geändert. Warten Sie eine Minute und laden Sie die Seite neu; "
  "bleibt es so, starten Sie das Gerät neu (kurz vom Strom trennen). Die aktuellen Netzwerkeinstellungen bleiben wirksam.")
k("net.trial.undone", "Change undone.", "Änderung rückgängig gemacht.")
k("net.trial.too_late", "There is no change waiting for confirmation (it may have been undone already).",
  "Keine Änderung wartet auf Bestätigung (sie wurde eventuell schon zurückgesetzt).")
k("net.wifi.title", "Wi-Fi", "WLAN")
k("net.wifi.saved", "Saved network: “{ssid}”", "Gespeichertes Netz: „{ssid}“")
k("net.wifi.hidden_flag", "hidden", "versteckt")
k("net.wifi.forget", "Forget this network", "Dieses Netz vergessen")
k("net.wifi.forget_confirm", "Forget the Wi-Fi network? If you are connected through it, the device may become unreachable until it opens the setup hotspot.",
  "WLAN vergessen? Wenn Sie darüber verbunden sind, ist das Gerät eventuell nicht erreichbar, bis es den Einrichtungs-Hotspot öffnet.")
for s, en, de in [("wpa2", "WPA2", "WPA2"), ("wpa2/wpa3", "WPA2/WPA3", "WPA2/WPA3"), ("wpa3", "WPA3", "WPA3"), ("wpa", "WPA (old)", "WPA (alt)"),
                  ("enterprise", "WPA Enterprise", "WPA Enterprise"), ("wep", "WEP (insecure)", "WEP (unsicher)"), ("open", "open (no password)", "offen (ohne Passwort)"),
                  ("None", "unknown security", "Sicherheit unbekannt")]:
    k("net.security." + s, en, de)
k("net.security.unsupported", "not supported", "nicht unterstützt")
k("net.wifi.choose", "Choose a network", "Netz wählen")
k("net.wifi.other", "Other or hidden network", "Anderes oder verstecktes Netz")
k("net.wifi.other_help", "Enter the name below", "Namen unten eingeben")
k("net.wifi.ssid", "Network name (SSID)", "Netzwerkname (SSID)")
k("net.wifi.hidden", "Hidden network", "Verstecktes Netz")
k("net.wifi.hidden_help", "Only for networks that do not announce their name.", "Nur für Netze, die ihren Namen nicht aussenden.")
k("net.wifi.password", "Wi-Fi password", "WLAN-Passwort")
k("net.wifi.password_help", "8 to 63 characters (WPA2/WPA3 Personal). It is stored on the device only and never shown again.",
  "8 bis 63 Zeichen (WPA2/WPA3 Personal). Es wird nur auf dem Gerät gespeichert und nie wieder angezeigt.")
k("net.wifi.password_keep", "Leave empty to keep the stored password of the same network.",
  "Leer lassen, um das gespeicherte Passwort desselben Netzes zu behalten.")
k("net.wifi.join", "Connect", "Verbinden")
k("net.wifi.join_confirm", "Connect to this network? While the setup hotspot is active it closes now: join the same Wi-Fi with your phone and open this page again. If that fails, the hotspot returns.",
  "Mit diesem Netz verbinden? Ein aktiver Einrichtungs-Hotspot schließt jetzt: Verbinden Sie Ihr Telefon mit demselben WLAN und öffnen Sie diese Seite erneut. Klappt das nicht, kommt der Hotspot zurück.")
k("net.scan.again", "Search again", "Erneut suchen")
k("net.scan.running", "Searching for networks …", "Netze werden gesucht …")
k("net.scan.age", "Networks found {minutes} min ago.", "Netze vor {minutes} min gefunden.")
k("net.scan.none", "No search yet.", "Noch keine Suche.")
k("net.scan.unsupported", "Searching is not possible right now (the radio is busy as a hotspot). Enter the network name below.",
  "Suchen ist gerade nicht möglich (das Funkmodul arbeitet als Hotspot). Geben Sie den Netznamen unten ein.")
k("net.scan.started", "Searching for networks – the list updates in a few seconds.", "Netze werden gesucht – die Liste aktualisiert sich in wenigen Sekunden.")
k("net.scan.failed", "The search could not be started.", "Die Suche konnte nicht gestartet werden.")
k("net.more", "More network settings", "Weitere Netzwerkeinstellungen")
k("net.ip.title", "Addresses (IPv4/IPv6)", "Adressen (IPv4/IPv6)")
k("net.ip.title.summary", "Automatic (DHCP) or fixed addresses", "Automatisch (DHCP) oder feste Adressen")
k("net.ip.ethernet", "Ethernet", "Ethernet")
k("net.ip.wifi", "Wi-Fi “{ssid}”", "WLAN „{ssid}“")
k("net.ip.v4_method", "IPv4", "IPv4")
k("net.ip.v6_method", "IPv6", "IPv6")
k("net.ip.dhcp", "Automatic (DHCP)", "Automatisch (DHCP)")
k("net.ip.auto", "Automatic (router advertisements)", "Automatisch (Router-Ankündigungen)")
k("net.ip.static", "Fixed address", "Feste Adresse")
k("net.ip.off", "Off", "Aus")
k("net.ip.address4", "Address with prefix", "Adresse mit Präfix")
k("net.ip.address4_help", "For example 192.168.1.20/24", "Zum Beispiel 192.168.1.20/24")
k("net.ip.address6", "Address with prefix", "Adresse mit Präfix")
k("net.ip.address6_help", "For example 2001:db8::20/64", "Zum Beispiel 2001:db8::20/64")
k("net.ip.gateway", "Gateway (optional)", "Gateway (optional)")
k("net.ip.dns", "DNS servers (optional)", "DNS-Server (optional)")
k("net.ip.dns_help", "Up to three, separated by spaces.", "Bis zu drei, durch Leerzeichen getrennt.")
k("net.ip.confirm", "Apply the new addresses? You may have to open the page at the new address and confirm within 3 minutes, otherwise they are undone.",
  "Neue Adressen anwenden? Eventuell müssen Sie die Seite unter der neuen Adresse öffnen und innerhalb von 3 Minuten bestätigen, sonst werden sie zurückgesetzt.")
k("net.ip.help", "A wrong fixed address cannot lock you out: unconfirmed changes are undone after 3 minutes, and a 'hifi-recovery' file with 'network-reset' on the SD card restores automatic addresses.",
  "Eine falsche feste Adresse sperrt Sie nicht aus: Unbestätigte Änderungen werden nach 3 Minuten zurückgesetzt, und eine Datei 'hifi-recovery' mit 'network-reset' auf der SD-Karte stellt automatische Adressen wieder her.")
k("net.hotspot.title", "Setup hotspot and country", "Einrichtungs-Hotspot und Land")
k("net.hotspot.title.summary", "Wi-Fi region and the fallback hotspot", "WLAN-Region und Ausweich-Hotspot")
k("net.country", "Country (Wi-Fi regulations)", "Land (WLAN-Vorschriften)")
k("net.country.choose", "Choose …", "Bitte wählen …")
k("net.country.help", "Required for Wi-Fi. Choose where the device is used; it is never detected automatically.",
  "Für WLAN erforderlich. Wählen Sie, wo das Gerät betrieben wird; es wird nie automatisch ermittelt.")
k("net.hotspot.configured", "Hotspot “{ssid}” is configured.", "Hotspot „{ssid}“ ist eingerichtet.")
k("net.hotspot.none", "No setup hotspot is configured: without a working network the device is reachable over Ethernet only.",
  "Kein Einrichtungs-Hotspot eingerichtet: Ohne funktionierendes Netz ist das Gerät nur per Ethernet erreichbar.")
k("net.hotspot.help", "The hotspot opens only when no Wi-Fi or Ethernet connection works (after about a minute), never together with the Wi-Fi connection. Its clients reach only this setup page.",
  "Der Hotspot öffnet nur, wenn weder WLAN noch Ethernet funktionieren (nach etwa einer Minute), nie gleichzeitig mit der WLAN-Verbindung. Seine Teilnehmer erreichen nur diese Einrichtungsseite.")
k("net.hotspot.ssid", "Hotspot name", "Hotspot-Name")
k("net.hotspot.password", "Hotspot password", "Hotspot-Passwort")
k("net.hotspot.password_help", "12 to 63 characters. Note it down: there is no hotspot without a password you know.",
  "12 bis 63 Zeichen. Notieren Sie es: Ohne ein Ihnen bekanntes Passwort gibt es keinen Hotspot.")
k("net.hotspot.password_keep", "Leave empty to keep the stored password (12 to 63 characters for a new one).",
  "Leer lassen, um das gespeicherte Passwort zu behalten (12 bis 63 Zeichen für ein neues).")
k("net.hotspot.remove_title", "Remove the setup hotspot", "Einrichtungs-Hotspot entfernen")
k("net.hotspot.remove_help", "Without the hotspot, a device without a working network can only be reached over Ethernet or restored with the SD card.",
  "Ohne Hotspot ist ein Gerät ohne funktionierendes Netz nur per Ethernet erreichbar oder per SD-Karte wiederherzustellen.")
k("net.hotspot.remove", "Remove hotspot", "Hotspot entfernen")
k("net.hotspot.remove_confirm", "Remove the setup hotspot and its password?", "Einrichtungs-Hotspot und sein Passwort entfernen?")
k("net.problem", "{field}: {reason}", "{field}: {reason}")
for f, en, de in [("wifi.ssid", "Network name", "Netzwerkname"), ("wifi.psk", "Wi-Fi password", "WLAN-Passwort"), ("hotspot.ssid", "Hotspot name", "Hotspot-Name"),
                  ("hotspot.psk", "Hotspot password", "Hotspot-Passwort"), ("country", "Country", "Land"),
                  ("ethernet.ipv4.address", "Ethernet IPv4 address", "Ethernet-IPv4-Adresse"), ("ethernet.ipv4.gateway", "Ethernet IPv4 gateway", "Ethernet-IPv4-Gateway"),
                  ("ethernet.ipv4.dns", "Ethernet IPv4 DNS", "Ethernet-IPv4-DNS"), ("ethernet.ipv6.address", "Ethernet IPv6 address", "Ethernet-IPv6-Adresse"),
                  ("ethernet.ipv6.gateway", "Ethernet IPv6 gateway", "Ethernet-IPv6-Gateway"), ("ethernet.ipv6.dns", "Ethernet IPv6 DNS", "Ethernet-IPv6-DNS"),
                  ("wifi.ipv4.address", "Wi-Fi IPv4 address", "WLAN-IPv4-Adresse"), ("wifi.ipv4.gateway", "Wi-Fi IPv4 gateway", "WLAN-IPv4-Gateway"),
                  ("wifi.ipv4.dns", "Wi-Fi IPv4 DNS", "WLAN-IPv4-DNS"), ("wifi.ipv6.address", "Wi-Fi IPv6 address", "WLAN-IPv6-Adresse"),
                  ("wifi.ipv6.gateway", "Wi-Fi IPv6 gateway", "WLAN-IPv6-Gateway"), ("wifi.ipv6.dns", "Wi-Fi IPv6 DNS", "WLAN-IPv6-DNS"),
                  ("document", "Settings", "Einstellungen"), ("file", "Settings file", "Einstellungsdatei")]:
    k("net.field." + f, en, de)
for r, en, de in [("invalid", "invalid", "ungültig"), ("required_iso_3166_alpha2", "please choose a country", "bitte ein Land wählen"),
                  ("required_12_to_63_printable_ascii", "12 to 63 characters (letters, digits, punctuation)", "12 bis 63 Zeichen (Buchstaben, Ziffern, Satzzeichen)"),
                  ("conflicts_with_hotspot_network", "overlaps the hotspot network 10.42.0.0/24", "überschneidet sich mit dem Hotspot-Netz 10.42.0.0/24"),
                  ("same_as_wifi", "must differ from the Wi-Fi network name", "muss sich vom WLAN-Namen unterscheiden"),
                  ("not_json", "unreadable", "nicht lesbar"), ("mismatch", "changed meanwhile – please try again", "zwischenzeitlich geändert – bitte erneut versuchen"),
                  ("unknown_field", "unknown entry", "unbekannter Eintrag"), ("not_an_object", "invalid", "ungültig"),
                  ("permissions_too_open", "wrong file permissions", "falsche Dateirechte")]:
    k("net.err." + r, en, de)
k("net.saved", "Network settings saved.", "Netzwerkeinstellungen gespeichert.")
k("net.saved_pending", "New network settings are active – please confirm them.", "Neue Netzwerkeinstellungen sind aktiv – bitte bestätigen.")
k("net.unchanged", "Nothing changed.", "Nichts geändert.")

here = os.path.dirname(os.path.abspath(__file__))
for lang, d in (("en", E), ("de", D)):
    with open(os.path.join(here, f"network.{lang}.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
