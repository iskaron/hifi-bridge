"use strict";
addStrings({
  en: {
    "mon.title": "Audio monitor", "mon.lead": "What a room's Squeezelite player outputs – behind Lyrion – made audible on this computer. The rooms are simulated speakers.",
    "mon.room": "Room", "mon.listen": "Listen", "mon.stoplisten": "Stop listening", "mon.gesture": "The browser only plays sound after a click.",
    "mon.left": "Left", "mon.right": "Right", "mon.latency": "Added monitor latency (measured)", "mon.buffer": "Buffer", "mon.output": "Audio output",
    "mon.underruns": "Underruns", "mon.latency_note": "From the player's virtual DAC to your speakers: relay + browser buffer + audio output. Not part of the bridge's own latency.",
    "mon.helpers": "Lyrion helpers (demo)", "mon.helpers_note": "These act on Lyrion like its own web UI would – never on the bridge.",
    "mon.radio": "Play demo radio in this room", "mon.stop": "Stop this room", "mon.group": "Synchronised group",
    "mon.sync": "Form group (first ticked room leads)", "mon.unsync": "Dissolve groups", "mon.done": "Done.", "mon.failed": "Lyrion did not accept the command.",
    "mon.lms": "Lyrion: {mode} {title}", "mon.waiting": "waiting for audio …",
    "mon.gain": "Listening level (this browser only; the room volume is set in Lyrion or on the phone)",
  },
  de: {
    "mon.title": "Audiomonitor", "mon.lead": "Was der Squeezelite-Player eines Raums ausgibt – hinter Lyrion – hörbar auf diesem Rechner. Die Räume sind simulierte Lautsprecher.",
    "mon.room": "Raum", "mon.listen": "Zuhören", "mon.stoplisten": "Zuhören beenden", "mon.gesture": "Der Browser gibt erst nach einem Klick Ton aus.",
    "mon.left": "Links", "mon.right": "Rechts", "mon.latency": "Zusätzliche Monitor-Latenz (gemessen)", "mon.buffer": "Puffer", "mon.output": "Audioausgabe",
    "mon.underruns": "Aussetzer", "mon.latency_note": "Vom virtuellen DAC des Players bis zu Ihren Lautsprechern: Weiterleitung + Browserpuffer + Audioausgabe. Nicht Teil der Latenz der Bridge.",
    "mon.helpers": "Lyrion-Helfer (Demo)", "mon.helpers_note": "Diese wirken auf Lyrion wie dessen eigene Weboberfläche – nie auf die Bridge.",
    "mon.radio": "Demo-Radio in diesem Raum abspielen", "mon.stop": "Diesen Raum stoppen", "mon.group": "Synchronisierte Gruppe",
    "mon.sync": "Gruppe bilden (erster markierter Raum führt)", "mon.unsync": "Gruppen auflösen", "mon.done": "Erledigt.", "mon.failed": "Lyrion hat den Befehl nicht angenommen.",
    "mon.lms": "Lyrion: {mode} {title}", "mon.waiting": "warte auf Audio …",
    "mon.gain": "Abhörpegel (nur dieser Browser; die Raumlautstärke stellt man in Lyrion oder am Telefon ein)",
  },
});
const M = { ctx: null, node: null, abort: null, room: null, state: null, lastT: null, levels: [0, 0] };
window.monitorStats = { limiter: "soft knee 0.6 -> 0.98", latencyMs: null, bufferMs: null, outputMs: null, underruns: 0, frames: 0, peak: 0, engine: null };

// Linear up to 0.6 of full scale, then a smooth tanh knee towards 0.98: no makeup gain (unlike DynamicsCompressorNode), no added latency.
function softLimiterCurve() {
  const n = 4097, c = new Float32Array(n), t = 0.6, k = 0.38;
  for (let i = 0; i < n; i++) {
    const x = (2 * i) / (n - 1) - 1, a = Math.abs(x);
    c[i] = a <= t ? x : Math.sign(x) * (t + k * Math.tanh((a - t) / k));
  }
  return c;
}
const DEFAULT_GAIN = "4";                              // +12 dB: the rooms start at Lyrion volume 75 (-12.5 dB); see docs/DEMO.md "Loudness"
function playerFor(room) { return ((M.state && M.state.players) || []).find((p) => p.name === room); }
function selectedRoom() { const r = document.querySelector('input[name="room"]:checked'); return r ? r.value : null; }

async function startListening() {
  stopListening();
  const room = selectedRoom();
  if (!room) return;
  if (!M.ctx) {
    M.ctx = new AudioContext({ sampleRate: 44100, latencyHint: "interactive" });
    if (M.ctx.audioWorklet) await M.ctx.audioWorklet.addModule("/static/worklet.js");
  }
  await M.ctx.resume();
  if (M.ctx.audioWorklet) {                         // secure context (http://127.0.0.1, https): low-jitter audio thread
    M.node = new AudioWorkletNode(M.ctx, "pcm-player", { outputChannelCount: [2], processorOptions: { targetMs: 250, maxMs: 800 } });
    M.node.port.onmessage = (e) => stats(e.data);
    window.monitorStats.engine = "AudioWorklet";
  } else {                                          // e.g. opened via a LAN address over plain http: same buffer logic, main thread
    M.node = fallbackPlayer(M.ctx, 250, 800);
    window.monitorStats.engine = "ScriptProcessor";
  }
  if (!M.gain) {                                     // listening level -> soft limiter -> speakers: a high level never clips hard
    M.gain = M.ctx.createGain();
    M.limiter = M.ctx.createWaveShaper(); M.limiter.curve = softLimiterCurve(); M.limiter.oversample = "none";
    M.gain.connect(M.limiter); M.limiter.connect(M.ctx.destination);
  }
  M.gain.gain.value = Number(document.getElementById("gain").value);
  M.node.connect(M.gain);
  M.abort = new AbortController();
  M.room = room;
  document.getElementById("listen").textContent = t("mon.stoplisten");
  document.getElementById("lat").textContent = t("mon.waiting");
  try {
    const r = await fetch("/stream/" + encodeURIComponent(room), { signal: M.abort.signal });
    const reader = r.body.getReader();
    let buf = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const nb = new Uint8Array(buf.length + value.length); nb.set(buf); nb.set(value, buf.length); buf = nb;
      let off = 0;
      while (buf.length - off >= 14) {
        const dv = new DataView(buf.buffer, buf.byteOffset + off);
        if (dv.getUint8(0) !== 72 || dv.getUint8(1) !== 66) throw new Error("bad monitor stream");
        const tdac = dv.getFloat64(2, true), n = dv.getUint32(10, true);
        if (buf.length - off < 14 + n) break;
        const pcm = new DataView(buf.buffer, buf.byteOffset + off + 14, n), frames = n / 4;
        const l = new Float32Array(frames), rr = new Float32Array(frames);
        let pl = 0, pr = 0;
        for (let i = 0; i < frames; i++) {
          l[i] = pcm.getInt16(i * 4, true) / 32768; rr[i] = pcm.getInt16(i * 4 + 2, true) / 32768;
          pl = Math.max(pl, Math.abs(l[i])); pr = Math.max(pr, Math.abs(rr[i]));
        }
        M.levels = [Math.max(pl, M.levels[0] * 0.85), Math.max(pr, M.levels[1] * 0.85)];
        window.monitorStats.frames += frames; window.monitorStats.peak = Math.max(window.monitorStats.peak, pl, pr);
        M.node.port.postMessage({ l, r: rr, t: tdac }, [l.buffer, rr.buffer]);
        off += 14 + n;
      }
      buf = buf.slice(off);
    }
  } catch (e) { if (e.name !== "AbortError") document.getElementById("lat").textContent = "–"; }
}
// Fallback when AudioWorklet is unavailable (insecure context): the worklet's ring buffer, run in a ScriptProcessorNode.
function fallbackPlayer(ctx, targetMs, maxMs) {
  const rate = ctx.sampleRate, cap = rate * 4, target = Math.round(targetMs / 1000 * rate), max = Math.round(maxMs / 1000 * rate);
  const L = new Float32Array(cap), R = new Float32Array(cap), marks = [];
  const s = { read: 0, write: 0, count: 0, written: 0, played: 0, running: false, underruns: 0, skipped: 0, n: 0 };
  const node = ctx.createScriptProcessor(2048, 0, 2);
  const drop = (n) => { s.read = (s.read + n) % cap; s.count -= n; s.played += n; };
  const tDacAt = (f) => { for (let i = marks.length - 1; i >= 0; i--) if (marks[i][0] <= f) return marks[i][1] + (f - marks[i][0]) / rate; return null; };
  node.port = { postMessage(d) {
    const n = d.l.length;
    if (s.count + n > cap) drop(s.count + n - cap);
    for (let i = 0; i < n; i++) { L[s.write] = d.l[i]; R[s.write] = d.r[i]; s.write = (s.write + 1) % cap; }
    marks.push([s.written, d.t]); s.written += n; s.count += n;
    if (marks.length > 400) marks.splice(0, marks.length - 400);
    if (s.count > max) { s.skipped += s.count - target; drop(s.count - target); }
  } };
  node.onaudioprocess = (e) => {
    const outL = e.outputBuffer.getChannelData(0), outR = e.outputBuffer.getChannelData(1), n = outL.length;
    if (!s.running && s.count >= target) s.running = true;
    if (s.running && s.count < n) { s.running = false; s.underruns++; }
    for (let i = 0; i < n; i++) {
      if (s.running) { outL[i] = L[s.read]; outR[i] = R[s.read]; s.read = (s.read + 1) % cap; s.count--; s.played++; } else { outL[i] = 0; outR[i] = 0; }
    }
    if (++s.n % 2 === 0) stats({ buffered: s.count, running: s.running, tPlaying: s.running ? tDacAt(s.played) : null, underruns: s.underruns, skipped: s.skipped });
  };
  return node;
}
function stopListening() {
  if (M.abort) M.abort.abort();
  if (M.node) { M.node.disconnect(); M.node = null; }
  M.abort = null; M.room = null;
  document.getElementById("listen").textContent = t("mon.listen");
}
function stats(d) {
  const outMs = M.ctx ? 1000 * ((M.ctx.outputLatency || 0) + (M.ctx.baseLatency || 0)) : 0;
  const s = window.monitorStats;
  s.bufferMs = Math.round(1000 * d.buffered / 44100); s.outputMs = Math.round(outMs); s.underruns = d.underruns;
  s.latencyMs = d.tPlaying ? Math.round(1000 * (Date.now() / 1000 - d.tPlaying) + outMs) : null;
  document.getElementById("lat").textContent = s.latencyMs === null ? t("mon.waiting") : "≈ " + s.latencyMs + " ms";
  document.getElementById("buf").textContent = s.bufferMs + " ms";
  document.getElementById("out").textContent = s.outputMs + " ms";
  document.getElementById("und").textContent = String(d.underruns);
  const db = (v) => Math.max(0, Math.min(100, (20 * Math.log10(Math.max(v, 1e-6)) + 60) / 60 * 100));   // -60..0 dBFS
  document.getElementById("ml").style.width = Math.round(db(M.levels[0])) + "%";
  document.getElementById("mr").style.width = Math.round(db(M.levels[1])) + "%";
}
function toast(ok) { document.getElementById("mtoast").textContent = t(ok ? "mon.done" : "mon.failed"); }
async function lms(path, body) { const r = await api(path, body); toast(r.ok); refresh(); }

function render() {
  const st = M.state || { rooms: [] };
  const box = document.getElementById("rooms"), sel = selectedRoom() || store.get("hifi-demo-room", st.rooms[0]);
  if (box.querySelectorAll("input").length !== st.rooms.length) {
    box.replaceChildren(el("legend", { class: "sr" }, t("mon.room")), ...st.rooms.map((room) => el("label", {},
      el("input", { type: "radio", name: "room", value: room, onchange: () => { store.set("hifi-demo-room", room); if (M.node) startListening(); render(); } }), room)));
    const gs = document.getElementById("groupset");
    gs.replaceChildren(...st.rooms.map((room) => el("label", {}, el("input", { type: "checkbox", value: room }), " " + room)));
  }
  box.querySelectorAll("input").forEach((i) => { i.checked = i.value === sel; });
  const p = playerFor(selectedRoom());
  document.getElementById("lmsnow").textContent = p ? t("mon.lms", { mode: t("player.mode." + p.mode), title: p.title ? "– " + p.title : "" }) : "";
}
async function refresh() { try { M.state = await api("/api/state"); render(); } catch (e) { /* restarting */ } }

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("listen").addEventListener("click", () => (M.node ? stopListening() : startListening()));
  const g = document.getElementById("gain"), gv = document.getElementById("gainv");
  g.value = store.get("hifi-demo-gain-v2", DEFAULT_GAIN);
  const showGain = () => { gv.textContent = Math.round(100 * Number(g.value)) + " %"; if (M.gain) M.gain.gain.value = Number(g.value); };
  g.addEventListener("input", () => { store.set("hifi-demo-gain-v2", g.value); showGain(); });
  showGain();
  document.getElementById("radio").addEventListener("click", () => { const p = playerFor(selectedRoom()); if (p) lms("/api/lms/radio", { player: p.id }); });
  document.getElementById("stop").addEventListener("click", () => { const p = playerFor(selectedRoom()); if (p) lms("/api/lms/stop", { player: p.id }); });
  document.getElementById("sync").addEventListener("click", () => {
    const ids = [...document.querySelectorAll("#groupset input:checked")].map((i) => playerFor(i.value)).filter(Boolean).map((p) => p.id);
    if (ids.length >= 2) lms("/api/lms/sync", { players: ids });
  });
  document.getElementById("unsync").addEventListener("click", () => lms("/api/lms/unsync", { players: ((M.state && M.state.players) || []).map((p) => p.id) }));
  refresh(); setInterval(refresh, 2000);
});
document.addEventListener("langchange", () => { render(); if (!M.node) document.getElementById("listen").textContent = t("mon.listen"); });
