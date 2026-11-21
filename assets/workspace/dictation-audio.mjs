// Average native microphone samples into mono 16 kHz PCM, including fractional ratios.
class DictationAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = this.sum = this.count = this.used = this.sent = 0;
    this.block = new Int16Array(2048);
    this.port.onmessage = () => this.finish();
  }
  flush() {
    if (!this.used) return;
    const samples = this.block.slice(0, this.used);
    this.port.postMessage(samples, [samples.buffer]);
    this.used = 0;
  }
  finish() {
    if (this.done) return;
    this.done = true;
    this.flush();
    this.port.postMessage('end');
  }
  process(inputs) {
    if (this.done) return false;
    for (const v of inputs[0]?.[0] || []) {
      this.phase += 16000;
      this.sum += v;
      this.count++;
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        this.block[this.used++] = Math.round(Math.max(-1, Math.min(1, this.sum / this.count)) * 32767);
        this.sum = this.count = 0;
        if (this.used === this.block.length) this.flush();
        if (++this.sent >= 90 * 16000) { this.finish(); return false; }
      }
    }
    return true;
  }
}
registerProcessor('origin-dictation', DictationAudio);
