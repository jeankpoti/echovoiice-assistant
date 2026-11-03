const TARGET_RATE = 16000;

// Convert a MediaRecorder blob to 16 kHz mono WAV, the format MMS expects.
export async function toWav16k(blob: Blob): Promise<Blob> {
  const buf = await blob.arrayBuffer();
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new Ctx();
  const decoded = await ctx.decodeAudioData(buf);
  ctx.close();
  return pcmToWav16k(decoded.getChannelData(0), decoded.sampleRate);
}

// Resample raw mono samples to 16 kHz and wrap them in a WAV file.
export async function pcmToWav16k(samples: Float32Array, rate: number): Promise<Blob> {
  if (rate === TARGET_RATE) return encodeWav(samples, rate);
  const offline = new OfflineAudioContext(1, Math.ceil((samples.length * TARGET_RATE) / rate), TARGET_RATE);
  const buffer = offline.createBuffer(1, samples.length, rate);
  buffer.getChannelData(0).set(samples);
  const src = offline.createBufferSource();
  src.buffer = buffer;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  return encodeWav(rendered.getChannelData(0), TARGET_RATE);
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const str = (o: number, s: string) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); str(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => {
    const v = Math.max(-1, Math.min(1, s));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  });
  return new Blob([view], { type: "audio/wav" });
}

let current: HTMLAudioElement | null = null;

// Play a base64 WAV, stopping anything already playing. Resolves when it ends or is stopped.
export function play(b64?: string | null): Promise<void> {
  stopPlayback();
  if (!b64) return Promise.resolve();
  const audio = new Audio(`data:audio/wav;base64,${b64}`);
  current = audio;
  return new Promise((resolve) => {
    const done = () => {
      if (current === audio) current = null;
      resolve();
    };
    audio.onended = done;
    audio.onpause = done;
    audio.onerror = done;
    audio.play().catch(done);
  });
}

export function stopPlayback() {
  current?.pause();
  current = null;
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
}

const BROWSER_LANG: Record<string, string> = { eng: "en-US", fra: "fr-FR" };

// English and French are spoken by the browser's own voices. Resolves when done or stopped.
export function speakText(text: string, lang: "eng" | "fra"): Promise<void> {
  stopPlayback();
  if (!text || typeof speechSynthesis === "undefined") return Promise.resolve();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = BROWSER_LANG[lang];
  const prefix = utterance.lang.slice(0, 2);
  const voice = speechSynthesis.getVoices().find((v) => v.lang.replace("_", "-").startsWith(prefix));
  if (voice) utterance.voice = voice;
  return new Promise((resolve) => {
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    speechSynthesis.speak(utterance);
  });
}

export interface Spoken {
  audio?: string | null; // Ewe: base64 WAV from the server
  text?: string; // English or French: spoken by the browser
  lang?: "eng" | "fra";
}

// Plays sentences one after another as they arrive. `finished` resolves once end() was
// called and everything has played, or when stop() cuts it short.
export class SpeechQueue {
  private items: Spoken[] = [];
  private closed = false;
  private stopped = false;
  private wake: (() => void) | null = null;
  readonly finished: Promise<void>;

  constructor(items: Spoken[] = []) {
    this.items = [...items];
    this.finished = this.run();
  }

  add(item: Spoken) {
    if (this.stopped) return;
    this.items.push(item);
    this.wake?.();
  }

  end() {
    this.closed = true;
    this.wake?.();
  }

  stop() {
    this.stopped = true;
    this.items = [];
    stopPlayback();
    this.wake?.();
  }

  private async run() {
    for (;;) {
      if (this.stopped) return;
      const item = this.items.shift();
      if (item) {
        if (item.audio) await play(item.audio);
        else if (item.text && item.lang) await speakText(item.text, item.lang);
        continue;
      }
      if (this.closed) return;
      await new Promise<void>((resolve) => (this.wake = resolve));
      this.wake = null;
    }
  }
}
