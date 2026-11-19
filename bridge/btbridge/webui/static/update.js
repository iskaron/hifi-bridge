// Software update page (views_update.py). Progressive enhancement only: every action is a plain form.
// - live phase and progress while something runs (polls /api/update), reload when the phase changes
// - upload progress for the bundle upload (XMLHttpRequest; the server answers with the same flash message as the plain form)
// - after "Restart now": waits until the device is back, then reloads
(function () {
  "use strict";
  var box = document.getElementById("ota-status");
  if (!box) return;
  var ACTIVE = ["downloading", "uploading", "verifying", "installing", "rebooting", "confirming"];
  var phase = box.getAttribute("data-phase");
  var phaseText = document.getElementById("ota-phase");
  function setText(s) { if (phaseText && phaseText.textContent !== s) phaseText.textContent = s; }

  function waitForRestart() {
    var wentDown = false, started = Date.now();
    setText(box.getAttribute("data-restarting"));
    var timer = setInterval(function () {
      fetch("/healthz", { cache: "no-store" }).then(function (r) {
        if (r.ok && (wentDown || Date.now() - started > 180000)) {
          clearInterval(timer); setText(box.getAttribute("data-back")); window.location.reload();
        }
      }).catch(function () { wentDown = true; });
    }, 3000);
  }

  function poll() {
    fetch("/api/update", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; return null; }
      return r.ok ? r.json() : null;
    }).then(function (st) {
      if (!st || !st.managed) return;
      if (st.phase !== phase) { window.location.reload(); return; }
      setText(st.phase_text);
      var bar = document.getElementById("ota-progress"), label = document.getElementById("ota-progress-text");
      if (bar && st.progress.total) {
        var pct = st.phase === "installing" ? st.progress.done : Math.floor(100 * st.progress.done / st.progress.total);
        bar.value = pct;
        if (label) label.textContent = pct + " %" + (st.progress.message ? " · " + st.progress.message : "");
      }
    }).catch(function () { /* the device may be restarting */ });
  }

  if (phase === "rebooting") waitForRestart();
  else if (ACTIVE.indexOf(phase) >= 0) setInterval(poll, 1500);

  var form = document.getElementById("ota-upload");
  if (form && window.FormData && window.XMLHttpRequest) {
    form.addEventListener("submit", function (ev) {
      if (ev.defaultPrevented) return;
      ev.preventDefault();
      var bar = document.getElementById("ota-upload-progress"), btn = form.querySelector("button");
      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.getAttribute("action"));
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      if (bar) { bar.hidden = false; bar.value = 0; }
      if (btn) btn.disabled = true;
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var pct = Math.floor(100 * e.loaded / e.total);
        if (bar) bar.value = pct;
        setText(box.getAttribute("data-uploading").split("{pct}").join(pct));
      };
      xhr.onloadend = function () { window.location.href = "/settings/update"; };
      xhr.send(new FormData(form));
    });
  }
})();
