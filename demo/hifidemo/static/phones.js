"use strict";
addStrings({
  en: {
    "ph.title": "Guest phones", "ph.lead": "Simulated phones. They reach the bridge only over the (simulated) Bluetooth radio, like real phones.",
    "ph.bluetooth": "Bluetooth", "ph.on": "On", "ph.off": "Off", "ph.scan": "Search for devices", "ph.found": "Available devices",
    "ph.none_found": "Nothing found. Open the pairing window in the bridge first.", "ph.my": "My devices", "ph.pair": "Pair",
    "ph.connect": "Connect", "ph.disconnect": "Disconnect", "ph.forget": "Forget", "ph.connected": "Connected", "ph.not_connected": "Not connected",
    "ph.playing": "Now playing", "ph.play": "Play", "ph.pause": "Pause", "ph.prev": "Previous track", "ph.next": "Next track",
    "ph.volume": "Volume", "ph.extras": "Simulation", "ph.notify": "Message arrives (short sound)", "ph.drop": "Simulate connection loss",
    "ph.library": "Music on this phone", "ph.upload": "Add own file (WAV, FLAC, MP3, Ogg)", "ph.uploading": "Decoding …",
    "msg.paired": "Paired.", "msg.connected": "Connected.", "msg.not_visible": "Search first – the bridge is only visible while its pairing window is open.",
    "msg.not_discoverable": "The bridge is not in pairing mode.", "msg.rejected": "The bridge rejected the pairing.",
    "msg.refused": "The bridge refused the connection (another phone is playing).", "msg.pair_again": "The bridge no longer knows this phone. Pair again.",
    "msg.pair_first": "Pair the phone first.", "msg.not_connected": "Connect to the bridge first.", "msg.link_lost": "Connection lost.",
    "msg.disconnected_by_bridge": "The bridge disconnected this phone.", "msg.removed": "The bridge owner removed this phone.",
    "msg.radio_error": "Radio side not reachable.", "msg.uploaded": "Added: {title}", "msg.upload_failed": "Could not add the file: {error}",
  },
  de: {
    "ph.title": "Gast-Smartphones", "ph.lead": "Simulierte Smartphones. Sie erreichen die Bridge nur über den (simulierten) Bluetooth-Funk – wie echte Telefone.",
    "ph.bluetooth": "Bluetooth", "ph.on": "Ein", "ph.off": "Aus", "ph.scan": "Geräte suchen", "ph.found": "Verfügbare Geräte",
    "ph.none_found": "Nichts gefunden. Zuerst in der Bridge das Kopplungsfenster öffnen.", "ph.my": "Meine Geräte", "ph.pair": "Koppeln",
    "ph.connect": "Verbinden", "ph.disconnect": "Trennen", "ph.forget": "Entfernen", "ph.connected": "Verbunden", "ph.not_connected": "Nicht verbunden",
    "ph.playing": "Wiedergabe", "ph.play": "Play", "ph.pause": "Pause", "ph.prev": "Vorheriger Titel", "ph.next": "Nächster Titel",
    "ph.volume": "Lautstärke", "ph.extras": "Simulation", "ph.notify": "Nachricht kommt an (kurzer Ton)", "ph.drop": "Verbindungsabbruch simulieren",
    "ph.library": "Musik auf diesem Telefon", "ph.upload": "Eigene Datei hinzufügen (WAV, FLAC, MP3, Ogg)", "ph.uploading": "Wird dekodiert …",
    "msg.paired": "Gekoppelt.", "msg.connected": "Verbunden.", "msg.not_visible": "Zuerst suchen – die Bridge ist nur sichtbar, solange ihr Kopplungsfenster offen ist.",
    "msg.not_discoverable": "Die Bridge ist nicht im Kopplungsmodus.", "msg.rejected": "Die Bridge hat die Kopplung abgelehnt.",
    "msg.refused": "Die Bridge hat die Verbindung abgelehnt (ein anderes Telefon spielt).", "msg.pair_again": "Die Bridge kennt dieses Telefon nicht mehr. Bitte neu koppeln.",
    "msg.pair_first": "Telefon zuerst koppeln.", "msg.not_connected": "Zuerst mit der Bridge verbinden.", "msg.link_lost": "Verbindung verloren.",
    "msg.disconnected_by_bridge": "Die Bridge hat dieses Telefon getrennt.", "msg.removed": "Der Besitzer der Bridge hat dieses Telefon entfernt.",
    "msg.radio_error": "Funkseite nicht erreichbar.", "msg.uploaded": "Hinzugefügt: {title}", "msg.upload_failed": "Datei konnte nicht hinzugefügt werden: {error}",
  },
});
const views = {};
let lastState = null;
const ICONS = { prev: "M6 5h2v14H6zM9.5 12 18 5v14z", next: "M16 5h2v14h-2zM6 5l8.5 7L6 19z", play: "M7 4.5v15L19.5 12z", pause: "M6.5 5h4v14h-4zM13.5 5h4v14h-4z" };
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  path.setAttribute("d", ICONS[name]); svg.append(path); return svg;
}
function fmt(s) { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
async function act(id, action, body) {
  const v = views[id];
  const r = await api(`/api/phones/${id}/${action}`, body || {});
  if (r && r.message) showMsg(v, r.message, !r.ok);
  refresh();
  return r;
}
function showMsg(v, key, bad, args) { v.toast.textContent = key ? t("msg." + key, args) : ""; v.toast.classList.toggle("bad", !!bad); v.msgKey = key; }
function build(p) {
  const id = p.id, v = {};
  v.name = el("h2", {}, p.name);
  v.bt = el("button", { class: "btn", onclick: () => act(id, "bluetooth", { on: !lastPhone(id).bt_on }) });
  v.scan = el("button", { class: "btn", onclick: () => act(id, "scan") }, t("ph.scan"));
  v.found = el("div", {});
  v.my = el("div", {});
  v.cover = el("div", { class: "cover", "aria-hidden": "true" });
  v.title = el("strong", {}); v.artist = el("span", { class: "muted small" });
  v.bar = el("span", {}); v.time = el("span", { class: "muted small" });
  v.prev = el("button", { class: "btn", onclick: () => act(id, "prev") }, icon("prev"));
  v.play = el("button", { class: "btn primary", onclick: () => act(id, lastPhone(id).status === "playing" ? "pause" : "play") });
  v.next = el("button", { class: "btn", onclick: () => act(id, "next") }, icon("next"));
  v.vol = el("input", { type: "range", min: "0", max: "127", step: "1" });
  v.vol.addEventListener("change", () => act(id, "volume", { value: Number(v.vol.value) }));
  v.volLabel = el("span", { class: "muted small" });
  v.notify = el("button", { class: "btn", onclick: () => act(id, "notify") });
  v.drop = el("button", { class: "btn danger", onclick: () => act(id, "drop") });
  v.lib = el("select", { "aria-label": t("ph.library"), onchange: () => act(id, "select", { id: v.lib.value }) });
  v.file = el("input", { type: "file", accept: ".wav,.flac,.mp3,.ogg,.oga,audio/*" });
  v.file.addEventListener("change", () => upload(id, v.file.files[0]));
  v.toast = el("p", { class: "toast", role: "status", "aria-live": "polite" });
  v.status = el("span", {});
  v.labels = {};
  const lab = (k) => (v.labels[k] = el("h3", {}, t(k)));
  const screen = el("div", { class: "screen" },
    el("div", { class: "statusbar" }, el("span", {}, "09:41"), el("span", { class: "sim" }, t("badge.simulated")), v.status),
    el("div", { class: "row between" }, v.name, v.bt),
    el("div", { class: "section" }, lab("ph.found"), v.scan, v.found),
    el("div", { class: "section" }, lab("ph.my"), v.my),
    el("div", { class: "section" }, lab("ph.playing"), v.cover, el("div", { class: "track" }, v.title, v.artist),
       el("div", { class: "progress" }, v.bar), el("div", { class: "row between" }, v.time),
       el("div", { class: "transport" }, v.prev, v.play, v.next),
       el("label", { class: "volume" }, v.vol, v.volLabel)),
    el("div", { class: "section library" }, lab("ph.library"), v.lib, el("label", { class: "small muted" }, el("span", { "data-k": "up" }), v.file)),
    el("div", { class: "section" }, lab("ph.extras"), el("div", { class: "row" }, v.notify, v.drop)),
    v.toast);
  v.root = el("section", { class: "phone", "aria-label": p.name, "data-phone": id }, screen);
  views[id] = v;
  document.getElementById("phones").append(v.root);
  return v;
}
function lastPhone(id) { return (lastState.phones || []).find((p) => p.id === id) || {}; }
async function upload(id, file) {
  const v = views[id];
  if (!file) return;
  showMsg(v, null); v.toast.textContent = t("ph.uploading");
  const r = await fetch("/api/library?name=" + encodeURIComponent(file.name), { method: "PUT", headers: { "X-Hifi-Demo": "1" }, body: file });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { showMsg(v, "uploaded", false, { title: d.item.title }); await act(id, "select", { id: d.item.id }); }
  else showMsg(v, "upload_failed", true, { error: d.error || r.status });
  v.file.value = "";
}
function update(p, lib) {
  const v = views[p.id] || build(p);
  v.bt.textContent = t("ph.bluetooth") + ": " + (p.bt_on ? t("ph.on") : t("ph.off"));
  v.scan.textContent = t("ph.scan"); v.scan.disabled = !p.bt_on;
  for (const [k, h] of Object.entries(v.labels)) h.textContent = t(k);
  v.found.replaceChildren(...(p.visible.length ? p.visible.map((d) => el("div", { class: "device" },
    el("span", {}, el("strong", {}, d.name), el("span", { class: "muted small" }, d.address)),
    el("button", { class: "btn", onclick: () => act(p.id, "pair") }, t("ph.pair")))) : [el("p", { class: "muted small" }, t("ph.none_found"))]));
  v.my.replaceChildren(...(p.paired ? [el("div", { class: "device" },
    el("span", {}, el("span", { class: "name" }, el("span", { class: "dot" + (p.connected ? " on" : "") }), p.bridge_name || "Hi-Fi Bridge"),
       el("span", { class: "muted small" }, p.connected ? t("ph.connected") : t("ph.not_connected"))),
    el("span", { class: "row" },
       p.connected ? el("button", { class: "btn", onclick: () => act(p.id, "disconnect") }, t("ph.disconnect"))
                   : el("button", { class: "btn primary", onclick: () => act(p.id, "connect") }, t("ph.connect")),
       el("button", { class: "btn danger", onclick: () => act(p.id, "forget") }, t("ph.forget"))))] : [el("p", { class: "muted small" }, "–")]));
  v.cover.textContent = (p.track.title || "?").slice(0, 2);
  v.title.textContent = p.track.title; v.artist.textContent = p.track.artist + " · " + p.track.album;
  v.bar.style.width = Math.min(100, 100 * p.position_s / Math.max(1, p.duration_s)) + "%";
  v.time.textContent = fmt(p.position_s) + " / " + fmt(p.duration_s);
  if (v.play.dataset.state !== p.status) { v.play.replaceChildren(icon(p.status === "playing" ? "pause" : "play")); v.play.dataset.state = p.status; }
  v.play.setAttribute("aria-label", p.status === "playing" ? t("ph.pause") : t("ph.play"));
  v.prev.setAttribute("aria-label", t("ph.prev")); v.next.setAttribute("aria-label", t("ph.next"));
  if (document.activeElement !== v.vol) v.vol.value = p.volume;
  v.vol.setAttribute("aria-label", t("ph.volume"));
  v.volLabel.textContent = Math.round(100 * p.volume / 127) + " %";
  v.notify.textContent = t("ph.notify"); v.drop.textContent = t("ph.drop");
  v.lib.setAttribute("aria-label", t("ph.library"));
  v.notify.disabled = v.drop.disabled = !p.connected;
  v.status.textContent = p.connected ? "BT" : "";
  v.root.querySelector('[data-k="up"]').textContent = t("ph.upload") + " ";
  if (v.lib.options.length !== lib.length || document.activeElement !== v.lib) {
    v.lib.replaceChildren(...lib.map((it) => el("option", { value: it.id }, it.title + (it.source === "upload" ? " ⬆" : ""))));
  }
  v.lib.value = p.track.id;
  if (p.message && p.message !== v.msgKey && !["paired", "connected"].includes(p.message)) showMsg(v, p.message, true);
  else if ((!p.message || ["paired", "connected"].includes(p.message)) && v.toast.classList.contains("bad")) showMsg(v, p.message, false);
}
async function refresh() {
  try {
    lastState = await api("/api/state");
    for (const p of lastState.phones) update(p, lastState.library || []);
  } catch (e) { /* console restarting */ }
}
document.addEventListener("DOMContentLoaded", () => { refresh(); setInterval(refresh, 1000); });
document.addEventListener("langchange", refresh);
