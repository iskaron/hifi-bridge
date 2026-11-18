// Network pages - progressive enhancement only (every action is a plain form):
// - confirmation countdown of a pending change (reloads at zero: the page then shows the rollback)
// - country names in the page language (Intl.DisplayNames; the server lists ISO codes)
// - refresh while a Wi-Fi search runs, focus the name field for "other network", hide unused fixed-address fields
(function () {
  "use strict";
  var S = {};
  try { S = JSON.parse(document.getElementById("i18n").textContent); } catch (e) { S = {}; }
  var lang = document.documentElement.lang || "en";

  var cd = document.querySelector(".net-countdown[data-remaining]");
  if (cd) {
    var left = parseInt(cd.getAttribute("data-remaining"), 10) || 0;
    var tick = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(tick); window.location.reload(); return; }
      var m = Math.floor(left / 60), s = left % 60;
      cd.textContent = (S["live.net_remaining"] || "{time}").split("{time}").join(m + ":" + (s < 10 ? "0" : "") + s);
    }, 1000);
  }

  var names = null;
  try { names = new Intl.DisplayNames([lang], { type: "region" }); } catch (e) { names = null; }
  if (names) {
    document.querySelectorAll("select[data-countries] option").forEach(function (o) {
      if (!o.value) return;
      try { var n = names.of(o.value); if (n && n !== o.value) o.textContent = n + " (" + o.value + ")"; } catch (e) { /* keep the code */ }
    });
    document.querySelectorAll("select[data-countries]").forEach(function (sel) {   // alphabetical by the shown name
      var first = sel.options[0], rest = Array.prototype.slice.call(sel.options, 1), cur = sel.value;
      rest.sort(function (a, b) { return a.textContent.localeCompare(b.textContent, lang); });
      sel.innerHTML = ""; sel.appendChild(first); rest.forEach(function (o) { sel.appendChild(o); }); sel.value = cur;
    });
  }

  if (document.querySelector("[data-scanning]")) {
    var tries = 0;
    var poll = setInterval(function () {
      tries += 1;
      fetch("/api/network", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (st) { if (st && st.scan && !st.scan.pending && st.scan.age_s !== null && st.scan.age_s < 30) window.location.reload(); })
        .catch(function () { /* ignore */ });
      if (tries > 15) clearInterval(poll);
    }, 2000);
  }

  var other = document.querySelector('input[name="ssid_other"]');
  document.querySelectorAll('input[name="ssid"]').forEach(function (r) {
    r.addEventListener("change", function () { if (r.value === "__other__" && r.checked && other) other.focus(); });
  });

  document.querySelectorAll("select[data-method]").forEach(function (sel) {
    var box = document.querySelector('.static-fields[data-for="' + sel.name + '"]');
    function sync() { if (box) box.hidden = sel.value !== "static"; }
    sel.addEventListener("change", sync); sync();
  });
})();
