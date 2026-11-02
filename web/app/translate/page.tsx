"use client";

// Face-to-face interpreter: one person speaks Ewe, the other English or French.
// Live mode listens hands-free and works out which language each person spoke.
import { FormEvent, useEffect, useRef, useState } from "react";
import { Lang, OtherLang, TranslateResponse, translateAudio, translateText } from "@/lib/api";
import { play, speakText, stopPlayback, toWav16k } from "@/lib/audio";
import { ListenState, LiveListener } from "@/lib/live";

type Side = "ewe" | "other";
type Speaker = "auto" | Side;

interface Entry {
  id: number;
  source: Lang;
  target: Lang;
  text: string;
  translation: string;
  audio?: string | null;
  wav?: Blob; // kept so a wrong language guess can be redone without speaking again
}

const NAMES: Record<Lang, string> = { ewe: "Eʋegbe", eng: "English", fra: "Français" };
const PLACEHOLDERS: Record<Lang, string> = {
  ewe: "Ŋlɔ le Eʋegbe me…",
  eng: "Type in English…",
  fra: "Écrivez en français…",
};
const OTHER_KEY = "echovoiice.translate.other";

export default function TranslatePage() {
  const [other, setOther] = useState<OtherLang>("fra");
  const [speaker, setSpeaker] = useState<Speaker>("auto");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [status, setStatus] = useState("");
  const [thinking, setThinking] = useState(false);
  const [talking, setTalking] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const [heard, setHeard] = useState<ListenState>("listening");
  const [recordingSide, setRecordingSide] = useState<Side | null>(null);
  const [texts, setTexts] = useState<Record<Side, string>>({ ewe: "", other: "" });
  const [editing, setEditing] = useState<{ id: number; side: Side } | null>(null);

  const live = useRef<LiveListener | null>(null);
  const otherRef = useRef<OtherLang>("fra");
  const speakerRef = useRef<Speaker>("auto");
  const recorder = useRef<MediaRecorder | null>(null);
  const nextId = useRef(0);
  const listRef = useRef<HTMLElement>(null);
  const liveRef = useRef<HTMLButtonElement>(null);
  const inputs = useRef<Record<Side, HTMLInputElement | null>>({ ewe: null, other: null });

  useEffect(() => {
    try {
      const saved = localStorage.getItem(OTHER_KEY);
      if (saved === "eng" || saved === "fra") chooseOther(saved);
    } catch {}
    return () => {
      live.current?.stop();
      stopPlayback();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length]);

  function chooseOther(lang: OtherLang) {
    otherRef.current = lang;
    setOther(lang);
    try { localStorage.setItem(OTHER_KEY, lang); } catch {}
  }

  function chooseSpeaker(next: Speaker) {
    speakerRef.current = next;
    setSpeaker(next);
  }

  const langOf = (side: Side): Lang => (side === "ewe" ? "ewe" : otherRef.current);
  const sideOf = (lang: Lang): Side => (lang === "ewe" ? "ewe" : "other");
  const speakerSource = (): Lang | "auto" =>
    speakerRef.current === "auto" ? "auto" : langOf(speakerRef.current);

  async function say(entry: Entry) {
    setTalking(true);
    if (entry.target === "ewe") await play(entry.audio);
    else await speakText(entry.translation, entry.target);
    setTalking(false);
  }

  // Translate, show it (in place of `replaceId` when redoing one), then speak it.
  async function run(send: () => Promise<TranslateResponse>, extra: { wav?: Blob; replaceId?: number } = {}) {
    const listener = live.current;
    // The microphone stays off while translating and speaking, so the translation is not heard as input
    listener?.pause();
    stopPlayback();
    setThinking(true);
    setStatus("");
    try {
      const data = await send();
      const entry: Entry = {
        id: extra.replaceId ?? nextId.current++,
        source: data.source, target: data.target,
        text: data.text, translation: data.translation, audio: data.audio_wav_base64, wav: extra.wav,
      };
      setEntries((prev) =>
        extra.replaceId === undefined ? [...prev, entry] : prev.map((e) => (e.id === extra.replaceId ? entry : e)),
      );
      setThinking(false);
      if (data.tts_error) setStatus("Translation ready (voice unavailable)");
      await say(entry);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setThinking(false);
      if (listener && listener === live.current) listener.resume();
    }
  }

  // --- Fixing a translation ---
  function sayAgain(entry: Entry) {
    stopPlayback();
    setEntries((prev) => prev.filter((e) => e.id !== entry.id));
    if (liveOn) {
      chooseSpeaker(sideOf(entry.source));
      setStatus(`Say it again in ${NAMES[entry.source]}`);
    } else {
      setStatus(`Tap the ${NAMES[entry.source]} microphone and say it again`);
    }
  }

  function wrongLanguage(entry: Entry) {
    if (!entry.wav || thinking) return;
    const source = entry.source === "ewe" ? otherRef.current : "ewe";
    run(() => translateAudio(entry.wav!, otherRef.current, source), { wav: entry.wav, replaceId: entry.id });
  }

  function edit(entry: Entry) {
    const side = sideOf(entry.source);
    setEditing({ id: entry.id, side });
    setTexts((prev) => ({ ...prev, [side]: entry.text }));
    inputs.current[side]?.focus();
  }

  // --- Live: hands-free ---
  async function startLive() {
    const listener = new LiveListener({
      onUtterance: (wav) => run(() => translateAudio(wav, otherRef.current, speakerSource()), { wav }),
      onState: (s) => {
        setHeard(s);
        if (s === "hearing") setStatus("");
      },
      onLevel: (rms) => liveRef.current?.style.setProperty("--level", String(Math.min(1, rms * 10))),
    });
    await listener.start();
    live.current = listener;
    setLiveOn(true);
    setStatus("");
  }

  function stopLive() {
    live.current?.stop();
    live.current = null;
    liveRef.current?.style.setProperty("--level", "0");
    setLiveOn(false);
    stopPlayback();
  }

  // --- Tap to talk on one side ---
  async function startRecording(side: Side) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const wav = await toWav16k(new Blob(chunks, { type: rec.mimeType }));
      run(() => translateAudio(wav, otherRef.current, langOf(side)), { wav });
    };
    stopPlayback();
    rec.start();
    recorder.current = rec;
    setRecordingSide(side);
  }

  function stopRecording() {
    recorder.current?.stop();
    recorder.current = null;
    setRecordingSide(null);
  }

  async function onSideMic(side: Side) {
    try {
      if (liveOn) {
        chooseSpeaker(speaker === side ? "auto" : side); // in live mode the side mic picks who speaks
        return;
      }
      if (thinking) return;
      if (recordingSide === side) stopRecording();
      else {
        if (recorder.current) stopRecording();
        await startRecording(side);
      }
    } catch (e) {
      setStatus((e as Error).message || "Microphone unavailable");
    }
  }

  async function onLive() {
    try {
      if (liveOn) stopLive();
      else {
        if (recorder.current) stopRecording();
        await startLive();
      }
    } catch (e) {
      setStatus((e as Error).message || "Microphone unavailable");
    }
  }

  async function onSubmit(e: FormEvent, side: Side) {
    e.preventDefault();
    const value = texts[side].trim();
    if (!value || thinking) return;
    const replaceId = editing?.side === side ? editing.id : undefined;
    setTexts((prev) => ({ ...prev, [side]: "" }));
    setEditing(null);
    const source = langOf(side);
    const target = side === "ewe" ? otherRef.current : "ewe";
    await run(() => translateText(value, source, target), { replaceId });
  }

  function changeOther(lang: OtherLang) {
    if (lang === other) return;
    chooseOther(lang);
    setEntries([]);
    setEditing(null);
    setStatus("");
  }

  let shownStatus = status;
  if (!shownStatus) {
    if (thinking) shownStatus = "Translating…";
    else if (talking) shownStatus = "Speaking the translation…";
    else if (recordingSide) shownStatus = `Listening to ${NAMES[langOf(recordingSide)]}… tap again when done`;
    else if (liveOn && heard === "hearing") shownStatus = "I'm listening…";
    else if (liveOn && speaker === "auto") shownStatus = `Speak Eʋegbe or ${NAMES[other]}, I'll work out which`;
    else if (liveOn) shownStatus = `Listening for ${NAMES[langOf(speaker as Side)]}`;
    else shownStatus = "Start live interpreting, or tap a microphone to talk";
  }

  function sidePanel(side: Side) {
    const lang = langOf(side);
    const recording = recordingSide === side;
    const chosen = liveOn && speaker === side;
    return (
      <div className={`side${chosen ? " active" : ""}`}>
        <div className="side-head">
          <strong>{NAMES[lang]}</strong>
          {chosen && <span className="turn">● listening for this language</span>}
        </div>
        <div className="side-row">
          <button
            className={`side-mic${recording ? " recording" : ""}`}
            aria-label={liveOn ? `Only listen for ${NAMES[lang]}` : recording ? "Stop" : `Talk in ${NAMES[lang]}`}
            aria-pressed={liveOn ? chosen : recording}
            disabled={!liveOn && thinking && !recording}
            onClick={() => onSideMic(side)}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-3.08A7 7 0 0 0 19 12h-2Z" />
            </svg>
          </button>
          <form className="text-form" onSubmit={(e) => onSubmit(e, side)}>
            <input
              ref={(el) => { inputs.current[side] = el; }}
              type="text"
              placeholder={editing?.side === side ? "Fix the text, then Go" : PLACEHOLDERS[lang]}
              autoComplete="off"
              lang={lang === "ewe" ? "ee" : lang === "fra" ? "fr" : "en"}
              value={texts[side]}
              onChange={(e) => setTexts((prev) => ({ ...prev, [side]: e.target.value }))}
            />
            <button type="submit" className="ghost">Go</button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="toolbar">
        <div className="pair">
          <span className="pair-fixed">Eʋegbe</span>
          <span aria-hidden="true">⇄</span>
          <div className="segmented" role="group" aria-label="Other language">
            <button aria-pressed={other === "eng"} onClick={() => changeOther("eng")}>English</button>
            <button aria-pressed={other === "fra"} onClick={() => changeOther("fra")}>Français</button>
          </div>
        </div>
      </div>

      <main ref={listRef} className="chat" aria-live="polite">
        {entries.length === 0 && (
          <p className="empty">
            One person speaks Eʋegbe, the other {NAMES[other]}. Start live interpreting and just talk: each
            hears the other in their own language.
          </p>
        )}
        {entries.map((e) => (
          <div key={e.id} className={`bubble ${e.source === "ewe" ? "patient" : "assistant"} translation`}>
            <span className="from">{NAMES[e.source]} → {NAMES[e.target]}</span>
            <span className="said">{e.text}</span>
            <span className="meaning">{e.translation}</span>
            <div className="entry-actions">
              <button onClick={() => say(e)}>▶ Play</button>
              <button onClick={() => sayAgain(e)}>↺ Say again</button>
              {e.wav && <button onClick={() => wrongLanguage(e)} disabled={thinking}>⇄ Wrong language</button>}
              <button onClick={() => edit(e)}>✎ Edit</button>
            </div>
          </div>
        ))}
      </main>

      <footer className="controls">
        {liveOn && (
          <div className="who">
            <span className="muted">Who speaks</span>
            <div className="segmented" role="group" aria-label="Who speaks">
              <button aria-pressed={speaker === "auto"} onClick={() => chooseSpeaker("auto")}>Auto</button>
              <button aria-pressed={speaker === "ewe"} onClick={() => chooseSpeaker("ewe")}>Eʋegbe</button>
              <button aria-pressed={speaker === "other"} onClick={() => chooseSpeaker("other")}>{NAMES[other]}</button>
            </div>
          </div>
        )}
        <p className="status">{shownStatus}</p>
        <button
          ref={liveRef}
          className={`live-toggle${liveOn ? " live" : ""}${liveOn && heard === "hearing" ? " hearing" : ""}`}
          aria-pressed={liveOn}
          onClick={onLive}
        >
          {liveOn ? "■ Stop live interpreting" : "● Start live interpreting"}
        </button>
        <div className="sides">
          <p className="label">{liveOn ? "Or type" : "Tap a microphone to talk, or type"}</p>
          {sidePanel("ewe")}
          {sidePanel("other")}
        </div>
      </footer>
    </>
  );
}
