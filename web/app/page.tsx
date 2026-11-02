"use client";

// EchoVoiice Assistant: talk in Ewe, hear the reply in Ewe.
// Two ways to talk: tap to start and stop, or a hands-free live conversation.
import { FormEvent, useEffect, useRef, useState } from "react";
import { converseAudio, converseText, Mode, startSession, TurnResponse } from "@/lib/api";
import { play, stopPlayback, toWav16k } from "@/lib/audio";
import { ListenState, LiveListener } from "@/lib/live";

type TalkStyle = "tap" | "live";

interface Bubble {
  id: number;
  role: "assistant" | "patient";
  ewe: string;
  fr?: string | null;
  audio?: string | null;
  urgent?: boolean;
}

interface Staff {
  summary: string;
  urgent: boolean;
  department?: string | null;
}

const NO_STAFF: Staff = { summary: "Aucune conversation pour le moment.", urgent: false };

export default function Home() {
  const [mode, setMode] = useState<Mode>("general");
  const [style, setStyle] = useState<TalkStyle>("live");
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [status, setStatus] = useState("");
  const [thinking, setThinking] = useState(false);
  const [talking, setTalking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const [heard, setHeard] = useState<ListenState>("listening");
  const [text, setText] = useState("");
  const [staffOpen, setStaffOpen] = useState(false);
  const [staff, setStaff] = useState<Staff>(NO_STAFF);

  const sessionId = useRef<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const live = useRef<LiveListener | null>(null);
  const speechToken = useRef(0);
  const nextId = useRef(0);
  const chatRef = useRef<HTMLElement>(null);
  const micRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [bubbles]);

  useEffect(() => () => live.current?.stop(), []);

  function addBubbles(...items: Omit<Bubble, "id">[]) {
    setBubbles((prev) => [...prev, ...items.map((b) => ({ ...b, id: nextId.current++ }))]);
  }

  async function speak(b64?: string | null) {
    if (!b64) return;
    const token = ++speechToken.current;
    live.current?.setAssistantSpeaking(true);
    setTalking(true);
    await play(b64);
    if (token !== speechToken.current) return; // a newer reply took over
    live.current?.setAssistantSpeaking(false);
    setTalking(false);
  }

  function showTurn(data: TurnResponse) {
    addBubbles(
      {
        role: "patient",
        ewe: data.transcript,
        fr: data.transcript_alternatives?.length ? `Also heard: ${data.transcript_alternatives.join(" / ")}` : null,
      },
      { role: "assistant", ewe: data.reply_ewe, audio: data.audio_wav_base64, urgent: data.urgent },
    );
    setStaff({
      summary: data.staff_summary_fr || "Aucun résumé pour le moment.",
      urgent: !!data.urgent,
      department: data.department,
    });
    if (data.tts_error) setStatus("Reply ready (voice unavailable)");
  }

  async function ensureSession(): Promise<string> {
    if (sessionId.current) return sessionId.current;
    const data = await startSession(mode);
    sessionId.current = data.session_id;
    addBubbles({ role: "assistant", ewe: data.greeting_ewe, fr: data.greeting_fr, audio: data.audio_wav_base64 });
    speak(data.audio_wav_base64);
    return data.session_id;
  }

  // One exchange: send what the person said, show the reply, then speak it.
  async function runTurn(send: (id: string) => Promise<TurnResponse>) {
    const listener = live.current;
    listener?.pause();
    setThinking(true);
    setStatus("");
    try {
      const data = await send(await ensureSession());
      showTurn(data);
      setThinking(false);
      if (listener && listener !== live.current) return; // live conversation was ended meanwhile
      listener?.resume(); // listen while speaking, so the person can interrupt
      if (!listener && !data.tts_error) setStatus("Tap to answer");
      await speak(data.audio_wav_base64);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setThinking(false);
      if (listener === live.current) listener?.resume();
    }
  }

  // --- Live conversation ---
  async function startLive() {
    const listener = new LiveListener({
      onUtterance: (wav) => runTurn((id) => converseAudio(id, wav)),
      onState: (s) => {
        setHeard(s);
        if (s === "hearing") setStatus("");
      },
      onLevel: (rms) => micRef.current?.style.setProperty("--level", String(Math.min(1, rms * 10))),
      onBargeIn: stopPlayback,
    });
    await listener.start();
    live.current = listener;
    setLiveOn(true);
    setStatus("");
    if (!sessionId.current) {
      listener.pause();
      setThinking(true);
      try {
        await ensureSession();
      } finally {
        setThinking(false);
        listener.resume();
      }
    }
  }

  function stopLive() {
    live.current?.stop();
    live.current = null;
    micRef.current?.style.setProperty("--level", "0");
    setLiveOn(false);
    stopPlayback();
    setStatus("Conversation paused. Tap to continue.");
  }

  // --- Tap to talk ---
  async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      runTurn(async (id) => converseAudio(id, await toWav16k(new Blob(chunks, { type: rec.mimeType }))));
    };
    stopPlayback();
    rec.start();
    recorder.current = rec;
    setRecording(true);
    setStatus("Listening… tap again when you finish");
  }

  function stopRecording() {
    recorder.current?.stop();
    recorder.current = null;
    setRecording(false);
  }

  async function onMic() {
    try {
      if (style === "live") {
        if (liveOn) stopLive();
        else await startLive();
        return;
      }
      if (thinking) return;
      if (!sessionId.current) {
        setThinking(true);
        try { await ensureSession(); } finally { setThinking(false); }
        setStatus("Tap to talk");
        return;
      }
      if (recorder.current) stopRecording();
      else await startRecording();
    } catch (e) {
      setStatus((e as Error).message || "Microphone unavailable");
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value || thinking) return;
    setText("");
    stopPlayback();
    await runTurn((id) => converseText(id, value));
  }

  function changeStyle(next: TalkStyle) {
    if (next === style) return;
    if (liveOn) stopLive();
    if (recorder.current) stopRecording();
    setStyle(next);
    setStatus("");
  }

  function changeMode(next: Mode) {
    if (next === mode) return;
    if (liveOn) stopLive();
    if (recorder.current) stopRecording();
    stopPlayback();
    sessionId.current = null;
    setBubbles([]);
    setStaff(NO_STAFF);
    setStaffOpen(false);
    setMode(next);
    setStatus("");
  }

  let shownStatus = status;
  if (!shownStatus) {
    if (thinking) shownStatus = "Thinking…";
    else if (style === "tap") shownStatus = sessionId.current ? "Tap to talk" : "Tap the button to start";
    else if (!liveOn) shownStatus = "Tap to start a live conversation";
    else if (talking) shownStatus = "Speaking… just talk to interrupt";
    else if (heard === "hearing") shownStatus = "I'm listening…";
    else shownStatus = "Go ahead, I'm listening";
  }

  const micLabel =
    style === "live"
      ? liveOn ? "End live conversation" : "Start live conversation"
      : recording ? "Stop talking" : "Start talking";

  return (
    <>
      <header className="top">
        <div className="top-actions">
          <div className="segmented" role="group" aria-label="Assistant">
            <button aria-pressed={mode === "general"} onClick={() => changeMode("general")}>Chat</button>
            <button aria-pressed={mode === "hospital"} onClick={() => changeMode("hospital")}>Hospital</button>
          </div>
          {mode === "hospital" && (
            <button className="ghost" aria-pressed={staffOpen} onClick={() => setStaffOpen((o) => !o)}>
              Staff
            </button>
          )}
        </div>
      </header>

      <section className="staff" hidden={mode !== "hospital" || !staffOpen}>
        <div className="staff-head">
          <strong>Résumé pour le personnel</strong>
          {staff.urgent && <span className="badge">URGENT</span>}
        </div>
        <p className="summary">{staff.summary}</p>
        <p className="muted">{staff.department ? `Service : ${staff.department}` : ""}</p>
      </section>

      <main ref={chatRef} className="chat" aria-live="polite">
        {bubbles.length === 0 && (
          <p className="empty">
            {mode === "general"
              ? "Talk about anything: say hello, ask a question, or just chat."
              : "Hospital guide: explain why you came and where you need to go."}
          </p>
        )}
        {bubbles.map((b) => (
          <div key={b.id} className={`bubble ${b.role}${b.urgent ? " urgent" : ""}`}>
            <span>{b.ewe}</span>
            {b.fr && <span className="fr">{b.fr}</span>}
            {b.audio && (
              <>
                <br />
                <button className="play" onClick={() => speak(b.audio)}>▶ Play again</button>
              </>
            )}
          </div>
        ))}
      </main>

      <footer className="controls">
        <div className="segmented" role="group" aria-label="How to talk">
          <button aria-pressed={style === "live"} onClick={() => changeStyle("live")}>Live</button>
          <button aria-pressed={style === "tap"} onClick={() => changeStyle("tap")}>Tap to talk</button>
        </div>
        <p className="status">{shownStatus}</p>
        <button
          ref={micRef}
          className={`mic${recording ? " recording" : ""}${liveOn ? " live" : ""}${liveOn && heard === "hearing" ? " hearing" : ""}`}
          aria-label={micLabel}
          aria-pressed={style === "live" ? liveOn : recording}
          disabled={style === "tap" && thinking && !recording}
          onClick={onMic}
        >
          {liveOn ? (
            <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="40" height="40" aria-hidden="true">
              <path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-3.08A7 7 0 0 0 19 12h-2Z" />
            </svg>
          )}
        </button>
        <form className="text-form" onSubmit={onSubmit}>
          <input
            type="text"
            placeholder="Or type in Ewe…"
            autoComplete="off"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="ghost">Send</button>
        </form>
      </footer>
    </>
  );
}
