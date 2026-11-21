// Alert sounds with the Web Audio API. Beeps cut through wind and engine noise
// better than speech and need no language. Audio follows the phone's output, so a
// Bluetooth helmet headset plays them too.
//
//   caution -> two short mid-pitch beeps, once
//   urgent  -> rapid high-pitched triple beeps, repeating until speed is safe
//   hotspot -> one long low tone

const PATTERNS = {
  // [frequency Hz, duration ms, gap ms]
  caution: { tones: [[1200, 120, 100], [1200, 120, 0]], repeatMs: 0 },
  urgent: { tones: [[2400, 80, 50], [2400, 80, 50], [2400, 80, 0]], repeatMs: 450 },
  hotspot: { tones: [[700, 450, 0]], repeatMs: 0 },
};

export class Beeper {
  constructor() {
    this.ctx = null;
    this.volume = 1.0;
    this.loopTimer = null;
    this.looping = null;
  }

  /** Must be called from a user tap (browsers block audio until then). */
  unlock() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === "suspended") this.ctx.resume();
    // a silent blip keeps iOS/Android happy
    this._tone(1, 30, this.ctx.currentTime + 0.01, 0.0001);
  }

  _tone(freq, ms, at, gain = this.volume) {
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = "square"; // harsher than sine = more noticeable on a bike
    osc.frequency.value = freq;
    // short attack/release avoids clicks while staying sharp
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(gain * 0.6, at + 0.005);
    g.gain.setValueAtTime(gain * 0.6, Math.max(at + 0.005, at + ms / 1000 - 0.01));
    g.gain.linearRampToValueAtTime(0, at + ms / 1000);
    osc.connect(g).connect(this.ctx.destination);
    osc.start(at);
    osc.stop(at + ms / 1000 + 0.02);
  }

  /** Play a pattern once. Returns its length in ms. */
  play(name) {
    if (!this.ctx) return 0;
    const p = PATTERNS[name];
    let t = this.ctx.currentTime + 0.01;
    let total = 0;
    for (const [f, ms, gap] of p.tones) {
      this._tone(f, ms, t);
      t += (ms + gap) / 1000;
      total += ms + gap;
    }
    return total;
  }

  /** Repeat a pattern until stop() or a different pattern starts. */
  loop(name) {
    if (this.looping === name) return;
    this.stop();
    this.looping = name;
    const once = () => {
      const len = this.play(name);
      this.loopTimer = setTimeout(once, len + PATTERNS[name].repeatMs);
    };
    once();
  }

  stop() {
    clearTimeout(this.loopTimer);
    this.loopTimer = null;
    this.looping = null;
  }
}

/** Optional voice, using the phone's built-in text-to-speech. */
export function speak(text, lang = "fil-PH") {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  u.voice = voices.find((v) => v.lang === lang) || voices.find((v) => v.lang.startsWith("en")) || null;
  u.lang = u.voice ? u.voice.lang : "en-US";
  u.rate = 1.1;
  window.speechSynthesis.speak(u);
}
