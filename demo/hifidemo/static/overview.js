"use strict";
function renderPlayers(players) {
  const tb = document.getElementById("players");
  tb.replaceChildren(...players.map((p) => el("tr", {},
    el("th", {}, p.name, " ", el("span", { class: "sim" }, t("badge.simulated"))),
    el("td", {}, p.connected ? t("player.mode." + p.mode) : t("player.offline")),
    el("td", { class: "muted" }, p.title || t("none")),
    el("td", { class: "muted" }, p.sync_slaves && p.sync_slaves.length ? t("player.group") : ""))));
}
function renderLinks(links) {
  const ul = document.getElementById("links");
  ul.replaceChildren(...Object.entries(links).map(([k, url]) => el("li", {},
    el("span", {}, t("link." + k), el("br"), el("code", {}, url)), el("a", { class: "btn", href: url, target: "_blank", rel: "noopener" }, t("ov.open")))));
}
async function refresh() {
  try {
    const st = await api("/api/state");
    renderLinks(st.links || {});
    renderPlayers(st.players || []);
  } catch (e) { /* console restarting */ }
}
document.addEventListener("DOMContentLoaded", () => { refresh(); setInterval(refresh, 3000); });
document.addEventListener("langchange", refresh);
