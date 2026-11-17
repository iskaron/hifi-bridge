// Hi-Fi Bridge demo console: shared language, theme and API helpers. Every device in this console is SIMULATED.
"use strict";
const I18N = {
  en: {
    "nav.overview": "Overview", "nav.phones": "Phones", "nav.monitor": "Audio monitor",
    "badge.simulated": "SIMULATED", "theme": "Theme", "theme.auto": "Auto", "theme.light": "Light", "theme.dark": "Dark",
    "lang": "Language",
    "ov.eyebrow": "Interactive demo", "ov.title": "Hi-Fi Bridge demo",
    "ov.lead": "The bridge software, Lyrion and the Squeezelite players are real. Phones, Bluetooth radio, rooms and speakers are simulated.",
    "ov.open": "Open", "ov.links": "Interfaces", "ov.players": "Rooms (Lyrion players)", "ov.steps": "First steps",
    "ov.step1": "Open the bridge (HTTP), enter the setup code shown by “make demo-status” and finish the setup wizard (Lyrion: lms).",
    "ov.step2": "Open the pairing window in the bridge, then pair and connect a phone on the “Phones” page.",
    "ov.step3": "Press play on the phone and listen on the “Audio monitor” page.",
    "ov.guide": "Full guide: docs/DEMO.md in the repository.",
    "link.bridge_http": "Bridge (HTTP)", "link.bridge_https": "Bridge (HTTPS, after trusting the device CA)", "link.lms": "Lyrion Media Server",
    "link.console": "This console", "link.homeassistant": "Home Assistant (optional profile)",
    "player.mode.play": "playing", "player.mode.pause": "paused", "player.mode.stop": "stopped", "player.offline": "offline",
    "player.group": "Group", "none": "–",
  },
  de: {
    "nav.overview": "Übersicht", "nav.phones": "Smartphones", "nav.monitor": "Audiomonitor",
    "badge.simulated": "SIMULIERT", "theme": "Design", "theme.auto": "Auto", "theme.light": "Hell", "theme.dark": "Dunkel",
    "lang": "Sprache",
    "ov.eyebrow": "Interaktive Demo", "ov.title": "Hi-Fi Bridge Demo",
    "ov.lead": "Bridge-Software, Lyrion und die Squeezelite-Player sind echt. Smartphones, Bluetooth-Funk, Räume und Lautsprecher sind simuliert.",
    "ov.open": "Öffnen", "ov.links": "Oberflächen", "ov.players": "Räume (Lyrion-Player)", "ov.steps": "Erste Schritte",
    "ov.step1": "Bridge öffnen (HTTP), den Einrichtungscode aus „make demo-status“ eingeben und den Assistenten abschließen (Lyrion: lms).",
    "ov.step2": "In der Bridge das Kopplungsfenster öffnen, dann auf der Seite „Smartphones“ ein Telefon koppeln und verbinden.",
    "ov.step3": "Am Telefon Play drücken und auf der Seite „Audiomonitor“ zuhören.",
    "ov.guide": "Ausführliche Anleitung: docs/DEMO.md im Repository.",
    "link.bridge_http": "Bridge (HTTP)", "link.bridge_https": "Bridge (HTTPS, nach Vertrauen der Geräte-CA)", "link.lms": "Lyrion Media Server",
    "link.console": "Diese Konsole", "link.homeassistant": "Home Assistant (optionales Profil)",
    "player.mode.play": "spielt", "player.mode.pause": "pausiert", "player.mode.stop": "gestoppt", "player.offline": "offline",
    "player.group": "Gruppe", "none": "–",
  },
};
const store = {
  get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
let LANG = store.get("hifi-demo-lang", (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en");
const EXTRA = { en: {}, de: {} };
function t(key, args) {
  let s = (EXTRA[LANG] && EXTRA[LANG][key]) || I18N[LANG][key] || (EXTRA.en && EXTRA.en[key]) || I18N.en[key] || key;
  if (args) for (const [k, v] of Object.entries(args)) s = s.replace("{" + k + "}", v);
  return s;
}
function addStrings(dict) { for (const l of ["en", "de"]) Object.assign(EXTRA[l], dict[l] || {}); }
function applyI18n(root) {
  document.documentElement.lang = LANG;
  (root || document).querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  (root || document).querySelectorAll("[data-i18n-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nLabel)); });
}
function applyTheme(v) {
  if (v === "light" || v === "dark") document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme;
}
async function api(path, body, method) {
  const opt = body === undefined && !method ? {} : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* non-JSON error page */ }
  if (!r.ok && data.ok === undefined) data.ok = false;
  return data;
}
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
function initChrome() {
  const lang = document.getElementById("lang"), theme = document.getElementById("theme");
  if (lang) { lang.value = LANG; lang.addEventListener("change", () => { LANG = lang.value; store.set("hifi-demo-lang", LANG); applyI18n(); document.dispatchEvent(new Event("langchange")); }); }
  const th = store.get("hifi-demo-theme", "auto");
  applyTheme(th);
  if (theme) { theme.value = th; theme.addEventListener("change", () => { store.set("hifi-demo-theme", theme.value); applyTheme(theme.value); }); }
  const here = location.pathname;
  document.querySelectorAll(".nav a").forEach((a) => { if (a.getAttribute("href") === here) a.setAttribute("aria-current", "page"); });
  applyI18n();
}
document.addEventListener("DOMContentLoaded", initChrome);
