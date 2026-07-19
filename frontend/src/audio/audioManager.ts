// Web Audio impact sounds synthesized in-browser (no network assets, spec §20).
// Triggered from the same impact event as haptics + rebound for perceived sync.

import type { HitQuality } from "../types";

export class AudioManager {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  muted = false;

  /** Must be called from a user gesture to satisfy autoplay policies. */
  resume(): void {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.6;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  setMuted(m: boolean): void {
    this.muted = m;
    if (this.master) this.master.gain.value = m ? 0 : 0.6;
  }

  impact(quality: HitQuality, strength: number, panX = 0): void {
    if (!this.ctx || !this.master || this.muted) return;
    const base = quality === "PERFECT" ? 660 : quality === "GOOD" ? 440 : 300;
    this.blip(base, 0.06 + strength * 0.05, panX, quality === "PERFECT" ? "triangle" : "square");
    if (quality === "PERFECT") this.blip(base * 1.5, 0.05, panX, "triangle", 0.02);
  }

  miss(): void {
    if (!this.ctx || this.muted) return;
    this.blip(160, 0.12, 0, "sawtooth", 0, 0.25);
  }

  countdown(step: number): void {
    if (!this.ctx || this.muted) return;
    this.blip(step === 0 ? 880 : 520, 0.12, 0, "sine");
  }

  private blip(
    freq: number,
    dur: number,
    panX: number,
    type: OscillatorType,
    delay = 0,
    gain = 0.4,
  ): void {
    const ctx = this.ctx!;
    const t0 = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    const pan = ctx.createStereoPanner();
    pan.pan.value = Math.max(-1, Math.min(1, panX));
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    osc.frequency.exponentialRampToValueAtTime(Math.max(60, freq * 0.6), t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.005);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(pan).connect(this.master!);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }
}
