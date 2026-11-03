// Hands-free listening: keeps the microphone open, detects when the person starts
// and stops talking, and hands each finished utterance over as a 16 kHz WAV.
import { pcmToWav16k } from "./audio";

export type ListenState = "listening" | "hearing";

interface Callbacks {
  onUtterance: (wav: Blob) => void;
  onState: (state: ListenState) => void;
  onLevel?: (level: number) => void;
  // The person started talking while the assistant was speaking
  onBargeIn?: () => void;
}

// Collects ~20 ms chunks of raw samples on the audio thread and posts them to the page.
const TAP_WORKLET = `
class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(1024); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("tap", Tap);
`;

const MIN_THRESHOLD = 0.012; // RMS below this is never speech
const START_MS = 150; // loud this long to count as speech
const BARGE_IN_MS = 300; // stricter while the assistant talks, so its own voice does not trigger it
// Quiet this long ends the utterance. People pause to breathe or think mid-sentence,
// so it sits above a normal pause between words. The page can change it (Pause setting).
const END_SILENCE_MS = 1100;
const MIN_SPEECH_MS = 350; // shorter bursts are noise (a cough, a click)
const MAX_SPEECH_MS = 60_000;
const PREROLL_MS = 300; // keep audio from just before speech was detected

export class LiveListener {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private paused = false;
  private assistantSpeaking = false;
  private floor = 0.004;
  private inSpeech = false;
  private loudMs = 0;
  private quietMs = 0;
  private speechMs = 0;
  private preroll: Float32Array[] = [];
  private prerollMs = 0;
  private speech: Float32Array[] = [];
  private endSilenceMs = END_SILENCE_MS;

  constructor(private cb: Callbacks) {}

  setEndSilence(ms: number) {
    this.endSilenceMs = ms;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    this.ctx = ctx;
    const url = URL.createObjectURL(new Blob([TAP_WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const tap = new AudioWorkletNode(ctx, "tap");
    tap.port.onmessage = (e) => this.onFrame(e.data as Float32Array);
    // A silent path to the speakers keeps the audio graph running
    const mute = ctx.createGain();
    mute.gain.value = 0;
    ctx.createMediaStreamSource(this.stream).connect(tap).connect(mute).connect(ctx.destination);
    this.cb.onState("listening");
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.ctx = undefined;
    this.stream = undefined;
    this.reset();
  }

  // Ignore the microphone while a reply is being prepared.
  pause() {
    this.paused = true;
    this.reset();
  }

  resume() {
    this.paused = false;
    this.cb.onState("listening");
  }

  setAssistantSpeaking(speaking: boolean) {
    this.assistantSpeaking = speaking;
  }

  private reset() {
    this.inSpeech = false;
    this.loudMs = this.quietMs = this.speechMs = this.prerollMs = 0;
    this.preroll = [];
    this.speech = [];
  }

  private onFrame(frame: Float32Array) {
    if (!this.ctx || this.paused) return;
    const ms = (frame.length / this.ctx.sampleRate) * 1000;
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / frame.length);
    this.cb.onLevel?.(rms);

    const threshold = Math.max(MIN_THRESHOLD, this.floor * 3) * (this.assistantSpeaking ? 2 : 1);

    if (!this.inSpeech) {
      this.preroll.push(frame);
      this.prerollMs += ms;
      while (this.prerollMs > PREROLL_MS && this.preroll.length > 1) {
        this.prerollMs -= (this.preroll.shift()!.length / this.ctx.sampleRate) * 1000;
      }
      if (rms <= threshold) {
        // Track background noise: follow it down quickly but up slowly, so soft
        // speech does not raise the floor and with it the bar for counting as speech
        this.floor = rms < this.floor ? this.floor * 0.9 + rms * 0.1 : this.floor * 0.998 + rms * 0.002;
        this.loudMs = 0;
        return;
      }
      this.loudMs += ms;
      if (this.loudMs < (this.assistantSpeaking ? BARGE_IN_MS : START_MS)) return;
      if (this.assistantSpeaking) this.cb.onBargeIn?.();
      this.inSpeech = true;
      this.speech = this.preroll;
      this.speechMs = this.prerollMs;
      this.preroll = [];
      this.prerollMs = 0;
      this.quietMs = 0;
      this.cb.onState("hearing");
      return;
    }

    this.speech.push(frame);
    this.speechMs += ms;
    // A much lower bar to stay in speech than to enter it, tied to the room's noise
    // rather than the entry bar, so soft syllables and word endings still count
    const stayThreshold = Math.max(MIN_THRESHOLD * 0.4, this.floor * 1.8);
    this.quietMs = rms > stayThreshold ? 0 : this.quietMs + ms;
    if (this.quietMs >= this.endSilenceMs || this.speechMs >= MAX_SPEECH_MS) this.finish();
  }

  private finish() {
    const chunks = this.speech;
    const voicedMs = this.speechMs - this.quietMs;
    const rate = this.ctx!.sampleRate;
    this.reset();
    this.cb.onState("listening");
    if (voicedMs < MIN_SPEECH_MS) return;

    const samples = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
    let offset = 0;
    for (const c of chunks) {
      samples.set(c, offset);
      offset += c.length;
    }
    pcmToWav16k(samples, rate).then(this.cb.onUtterance);
  }
}
