// AudioWorklet: plays the monitor stream from a ring buffer and reports which player-DAC time is audible right now.
class PcmPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.rate = sampleRate;
    this.target = Math.round((o.targetMs || 250) / 1000 * this.rate);   // pre-buffer before starting / after an underrun
    this.max = Math.round((o.maxMs || 800) / 1000 * this.rate);          // above this, skip ahead to the target (bounded latency)
    this.cap = this.rate * 4;
    this.l = new Float32Array(this.cap); this.r = new Float32Array(this.cap);
    this.read = 0; this.write = 0; this.count = 0;
    this.marks = [];                                                      // [frameIndexAtWrite, tDac] per received chunk
    this.written = 0; this.played = 0;
    this.running = false; this.underruns = 0; this.skipped = 0; this.n = 0;
    this.port.onmessage = (e) => this.push(e.data);
  }
  push(d) {
    const n = d.l.length;
    if (this.count + n > this.cap) { this.drop(this.count + n - this.cap); }
    for (let i = 0; i < n; i++) { this.l[this.write] = d.l[i]; this.r[this.write] = d.r[i]; this.write = (this.write + 1) % this.cap; }
    this.marks.push([this.written, d.t]); this.written += n; this.count += n;
    if (this.marks.length > 400) this.marks.splice(0, this.marks.length - 400);
    if (this.count > this.max) { this.skipped += this.count - this.target; this.drop(this.count - this.target); }
  }
  drop(n) { this.read = (this.read + n) % this.cap; this.count -= n; this.played += n; }
  tDacAt(frame) {                                                         // player-DAC wall time of an absolute frame index
    for (let i = this.marks.length - 1; i >= 0; i--) if (this.marks[i][0] <= frame) return this.marks[i][1] + (frame - this.marks[i][0]) / this.rate;
    return null;
  }
  process(inputs, outputs) {
    const out = outputs[0], L = out[0], R = out[1] || out[0], n = L.length;
    if (!this.running && this.count >= this.target) this.running = true;
    if (this.running && this.count < n) { this.running = false; this.underruns++; }
    for (let i = 0; i < n; i++) {
      if (this.running) { L[i] = this.l[this.read]; R[i] = this.r[this.read]; this.read = (this.read + 1) % this.cap; this.count--; this.played++; }
      else { L[i] = 0; R[i] = 0; }
    }
    if (++this.n % 38 === 0) {                                            // ~10 Hz at 128-frame quanta
      this.port.postMessage({ buffered: this.count, running: this.running, tPlaying: this.running ? this.tDacAt(this.played) : null,
                              underruns: this.underruns, skipped: this.skipped });
    }
    return true;
  }
}
registerProcessor("pcm-player", PcmPlayer);
