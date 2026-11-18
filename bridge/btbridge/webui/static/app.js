// Progressive enhancement only: every action works without JavaScript (plain forms).
// - live status on pages with <body data-live>, polling /api/status every 2 s
// - pairing countdown, confirmation dialogs (data-confirm)
(function () {
  "use strict";
  var S = {};
  try { S = JSON.parse(document.getElementById("i18n").textContent); } catch (e) { S = {}; }
  function t(key, vars) {
    var s = S[key] || key;
    Object.keys(vars || {}).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }
  function text(id, value) { var el = document.getElementById(id); if (el && el.textContent !== value) el.textContent = value; }

  // an error message after a redirect receives focus, so screen readers read it and keyboard users start from there
  var err = document.querySelector(".flash[data-focus]");
  if (err) { try { err.focus(); } catch (e) { /* ignore */ } }

  document.addEventListener("submit", function (ev) {
    var f = ev.target, msg = f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) ev.preventDefault();
  });

  var countdown = document.querySelector(".countdown[data-remaining]");
  // the Bluetooth name guests select (review M6: the header shows the product name, not this one)
  var deviceName = countdown ? (countdown.getAttribute("data-name") || "") : "";
  if (countdown) {
    var left = parseInt(countdown.getAttribute("data-remaining"), 10) || 0;
    var tick = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(tick); window.location.reload(); return; }
      countdown.textContent = t("live.pairing_open", { seconds: left, name: deviceName });
    }, 1000);
  }

  if (!document.body.hasAttribute("data-live")) return;
  var hero = document.querySelector(".hero"), banner = null, lastHeadline = hero && hero.getAttribute("data-headline");
  function setOffline(off) {
    if (off && !banner) { banner = document.createElement("div"); banner.className = "offline-banner"; banner.setAttribute("role", "status");
      banner.textContent = t("live.offline"); document.body.appendChild(banner); }
    if (!off && banner) { banner.remove(); banner = null; }
  }
  function poll() {
    fetch("/api/status", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; return null; }
      return r.ok ? r.json() : null;
    }).then(function (st) {
      if (!st) return;
      setOffline(false);
      if (hero) {
        var h = st.headline;
        if (h !== lastHeadline) {                   // state class + texts
          hero.className = hero.className.replace(/state-\S+/, "state-" + h); hero.setAttribute("data-headline", h); lastHeadline = h;
        }
        text("live-headline", t("headline." + h)); text("live-detail", t("headline." + h + ".detail"));
        var dev = st.bluetooth.connected_device; text("live-device", dev ? dev.name : t("state.no_device"));
        text("live-now", st.now_playing || "–");
      }
      var pairingOpenOnPage = !!document.getElementById("live-pairing");
      if (pairingOpenOnPage !== st.bluetooth.pairing_open) window.location.reload();   // window opened/closed elsewhere (e.g. MQTT)
    }).catch(function () { setOffline(true); });
  }
  setInterval(poll, 2000);
})();
