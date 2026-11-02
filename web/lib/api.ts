export type Mode = "general" | "hospital";

export interface SessionResponse {
  session_id: string;
  mode: Mode;
  greeting_ewe: string;
  greeting_fr?: string | null;
  audio_wav_base64?: string | null;
}

export interface TurnResponse {
  transcript: string;
  transcript_alternatives?: string[];
  reply_ewe: string;
  audio_wav_base64?: string | null;
  tts_error?: string | null;
  staff_summary_fr?: string | null;
  urgent?: boolean;
  department?: string | null;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Error ${res.status}`);
  return body as T;
}

export const startSession = (mode: Mode) =>
  api<SessionResponse>("/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });

export function converseAudio(sessionId: string, wav: Blob) {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("audio", wav, "speech.wav");
  return api<TurnResponse>("/v1/converse", { method: "POST", body: form });
}

export const converseText = (sessionId: string, text: string) =>
  api<TurnResponse>("/v1/converse/text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  });

// --- Translation: always Ewe on one side, English or French on the other ---
export type Lang = "ewe" | "eng" | "fra";
export type OtherLang = "eng" | "fra";

export interface TranslateResponse {
  source: Lang;
  target: Lang;
  text: string;
  text_alternatives: string[];
  translation: string;
  audio_wav_base64?: string | null;
  tts_error?: string | null;
}

export const translateText = (text: string, source: Lang, target: Lang) =>
  api<TranslateResponse>("/v1/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source, target }),
  });

export function translateAudio(wav: Blob, source: Lang, target: Lang) {
  const form = new FormData();
  form.append("source", source);
  form.append("target", target);
  form.append("audio", wav, "speech.wav");
  return api<TranslateResponse>("/v1/translate/audio", { method: "POST", body: form });
}

// --- Learning: a word or phrase, with its Ewe, pronunciation and an example ---
export interface Lesson {
  source: Lang;
  other: OtherLang;
  text: string;
  ewe: string;
  other_text: string;
  pronunciation: string;
  example_ewe: string;
  example_other: string;
  note: string;
  audio_word_base64?: string | null;
  audio_example_base64?: string | null;
  tts_error?: string | null;
}

export const learn = (text: string, source: Lang, other: OtherLang) =>
  api<Lesson>("/v1/learn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source, other }),
  });
