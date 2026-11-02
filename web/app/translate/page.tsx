"use client";

// Face-to-face interpreter: one person speaks Ewe, the other English or French.
// Live mode listens hands-free and hands the turn to the other person after each translation.
import { FormEvent, useEffect, useRef, useState } from "react";
import { Lang, OtherLang, TranslateResponse, translateAudio, translateText } from "@/lib/api";
import { play, speakText, stopPlayback, toWav16k } from "@/lib/audio";
import { ListenState, LiveListener } from "@/lib/live";

type Side = "ewe" | "other";

interface Entry {
  id: number;
  side: Side;
  source: Lang;
  target: Lang;
  text: string;
  translation: string;
  audio?: string | null;
}

const NAMES: Record<Lang, string> = { ewe: "Eʋegbe", eng: "English", fra: "Français" };
const PROMPTS: Record<Lang, string> = {
  ewe: "Ƒo nu le Eʋegbe me",
  eng: "Speak English",
  fra: "Parlez français",
};
const OTHER_KEY = "echovoiice.translate.other";

export default function TranslatePage() {
  const [other, setOther] = useState<OtherLang>("fra");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [status, setStatus] = useState("");
  const [thinking, setThinking] = useState(false);
  const [talking, setTalking] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const [heard, setHeard] = useState<ListenState>("listening");
  const [active, setActive] = useState<Side>("ewe");
  const [recordingSide, setRecordingSide] = useState<Side | null>(null);
  const [texts, setTexts] = useState<Record<Side, string>>({ ewe: "", other: "" });

  const live = useRef<LiveListener | null>(null);
  const activeRef = useRef<Side>("ewe");
  const otherRef = useRef<OtherLang>("fra");
  const recorder = useRef<MediaRecorder | null>(null);
  const nextId = useRef(0);
  const listRef = useRef<HTMLElement>(null);
  const micRef = useRef<HTMLButtonElement>(null);

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
  }, [entries]);

  function chooseOther(lang: OtherLang) {
    otherRef.current = lang;
    setOther(lang);
    try { localStorage.setItem(OTHER_KEY, lang); } catch {}
  }

  function chooseActive(side: Side) {
    activeRef.current = side;
    setActive(side);
  }

  const langOf = (side: Side): Lang => (side === "ewe" ? "ewe" : otherRef.current);

  async function say(entry: Entry) {
    setTalking(true);
    if (entry.target === "ewe") await play(entry.audio);
    else await speakText(entry.translation, entry.target);
    setTalking(false);
  }

  // Translate what one side said, speak it to the other side, then hand them the turn.
  async function run(side: Side, send: (source: Lang, target: Lang) => Promise<TranslateResponse>) {
    const source = langOf(side);
    const target = side === "ewe" ? otherRef.current : "ewe";
    const listener = live.current;
    // The microphone stays off while translating and speaking, so the translation is not heard as input
    listener?.pause();
    stopPlayback();
    setThinking(true);
    setStatus("");
    try {
      const data = await send(source, target);
      const entry: Entry = {
        id: nextId.current++, side, source, target,
        text: data.text, translation: data.translation, audio: data.audio_wav_base64,
      };
      setEntries((prev) => [...prev, entry]);
      setThinking(false);
      if (data.tts_error) setStatus("Translation ready (voice unavailable)");
      chooseActive(side === "ewe" ? "other" : "ewe");
      await say(entry);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setThinking(false);
      if (listener && listener === live.current) listener.resume();
    }
  }

  // --- Live: hands-free, the highlighted side speaks next ---
  async function startLive() {
    const listener = new LiveListener({
      onUtterance: (wav) => run(activeRef.current, (s, t) => translateAudio(wav, s, t)),
      onState: (s) => {
        setHeard(s);
        if (s === "hearing") setStatus("");
      },
      onLevel: (rms) => micRef.current?.style.setProperty("--level", String(Math.min(1, rms * 10))),
    });
    await listener.start();
    live.current = listener;
    setLiveOn(true);
    setStatus("");
  }

  function stopLive() {
    live.current?.stop();
    live.current = null;
    micRef.current?.style.setProperty("--level", "0");
    setLiveOn(false);
    stopPlayback();
  }

  // --- Tap to talk on one side ---
  async function startRecording(side: Side) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      run(side, async (s, t) => translateAudio(await toWav16k(new Blob(chunks, { type: rec.mimeType })), s, t));
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
        chooseActive(side); // in live mode, tapping a side just gives it the turn
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
    setTexts((prev) => ({ ...prev, [side]: "" }));
    await run(side, (s, t) => translateText(value, s, t));
  }

  function changeOther(lang: OtherLang) {
    if (lang === other) return;
    chooseOther(lang);
    setEntries([]);
    setStatus("");
  }

  let shownStatus = status;
  if (!shownStatus) {
    if (thinking) shownStatus = "Translating…";
    else if (talking) shownStatus = "Speaking the translation…";
    else if (recordingSide) shownStatus = `Listening to ${NAMES[langOf(recordingSide)]}… tap again when done`;
    else if (liveOn && heard === "hearing") shownStatus = `Hearing ${NAMES[langOf(active)]}…`;
    else if (liveOn) shownStatus = `${PROMPTS[langOf(active)]} · tap the other side to switch`;
    else shownStatus = "Start live interpreting, or tap a side to talk";
  }

  function sidePanel(side: Side) {
    const lang = langOf(side);
    const isActive = liveOn && active === side;
    const recording = recordingSide === side;
    return (
      <div className={`side${isActive ? " active" : ""}`}>
        <div className="side-head">
          <strong>{NAMES[lang]}</strong>
          {isActive && <span className="turn">● {PROMPTS[lang]}</span>}
        </div>
        <div className="side-row">
          <button
            className={`side-mic${recording ? " recording" : ""}`}
            aria-label={liveOn ? `${NAMES[lang]} speaks next` : recording ? "Stop" : `Talk in ${NAMES[lang]}`}
            aria-pressed={liveOn ? isActive : recording}
            disabled={!liveOn && thinking && !recording}
            onClick={() => onSideMic(side)}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-3.08A7 7 0 0 0 19 12h-2Z" />
            </svg>
          </button>
          <form className="text-form" onSubmit={(e) => onSubmit(e, side)}>
            <input
              type="text"
              placeholder={side === "ewe" ? "Ŋlɔ le Eʋegbe me…" : lang === "fra" ? "Écrivez en français…" : "Type in English…"}
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
            One person speaks Eʋegbe, the other {NAMES[other]}. Each hears the other in their own language.
          </p>
        )}
        {entries.map((e) => (
          <div key={e.id} className={`bubble ${e.side === "ewe" ? "patient" : "assistant"} translation`}>
            <span className="from">{NAMES[e.source]} → {NAMES[e.target]}</span>
            <span className="said">{e.text}</span>
            <span className="meaning">{e.translation}</span>
            <button className="play" onClick={() => say(e)}>▶ Play again</button>
          </div>
        ))}
      </main>

      <footer className="controls">
        {sidePanel("ewe")}
        {sidePanel("other")}
        <p className="status">{shownStatus}</p>
        <button
          ref={micRef}
          className={`live-toggle${liveOn ? " live" : ""}${liveOn && heard === "hearing" ? " hearing" : ""}`}
          aria-pressed={liveOn}
          onClick={onLive}
        >
          {liveOn ? "■ Stop live interpreting" : "● Start live interpreting"}
        </button>
      </footer>
    </>
  );
}
