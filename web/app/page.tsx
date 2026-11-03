"use client";

// EchoVoiice Assistant: talk in Ewe, hear the reply in Ewe.
// Two ways to talk: tap to start and stop, or a hands-free live conversation.
import { FormEvent, useEffect, useRef, useState } from "react";
import PauseControl from "@/components/PauseControl";
import { converseStream, Mode, startSession } from "@/lib/api";
import { SpeechQueue, toWav16k } from "@/lib/audio";
import { ListenState, LiveListener } from "@/lib/live";
import { loadPause, Pause, PAUSES, savePause } from "@/lib/pause";

type TalkStyle = "tap" | "live";

interface Bubble {
  id: number;
  role: "assistant" | "patient";
  ewe: string;
  fr?: string | null;
  audios?: string[]; // Ewe audio, one per sentence
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
  const [pause, setPause] = useState<Pause>("normal");

  const sessionId = useRef<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const live = useRef<LiveListener | null>(null);
  const speech = useRef<SpeechQueue | null>(null);
  const nextId = useRef(0);
  const chatRef = useRef<HTMLElement>(null);
  const micRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [bubbles]);

  useEffect(() => {
    setPause(loadPause());
    return () => {
      live.current?.stop();
      speech.current?.stop();
    };
  }, []);

  function addBubble(item: Omit<Bubble, "id">): number {
    const id = nextId.current++;
    setBubbles((prev) => [...prev, { ...item, id }]);
    return id;
  }

  function updateBubble(id: number, change: (b: Bubble) => Partial<Bubble>) {
    setBubbles((prev) => prev.map((b) => (b.id === id ? { ...b, ...change(b) } : b)));
  }

  // Play a reply sentence by sentence; a newer reply or the person talking cuts it off.
  function startSpeaking(audios: string[] = []): SpeechQueue {
    speech.current?.stop();
    const queue = new SpeechQueue(audios.map((audio) => ({ audio })));
    speech.current = queue;
    live.current?.setAssistantSpeaking(true);
    setTalking(true);
    queue.finished.then(() => {
      if (speech.current !== queue) return; // a newer reply took over
      live.current?.setAssistantSpeaking(false);
      setTalking(false);
    });
    return queue;
  }

  function replay(audios?: string[]) {
    if (audios?.length) startSpeaking(audios).end();
  }

  function stopSpeaking() {
    speech.current?.stop();
  }

  function choosePause(next: Pause) {
    setPause(next);
    savePause(next);
    live.current?.setEndSilence(PAUSES[next].ms);
  }

  async function ensureSession(): Promise<string> {
    if (sessionId.current) return sessionId.current;
    const data = await startSession(mode);
    sessionId.current = data.session_id;
    const audios = data.audio_wav_base64 ? [data.audio_wav_base64] : [];
    addBubble({ role: "assistant", ewe: data.greeting_ewe, fr: data.greeting_fr, audios });
    replay(audios);
    return data.session_id;
  }

  // One exchange: send what the person said; the reply is shown and spoken sentence by
  // sentence while Muse Spark is still writing the rest.
  async function runTurn(input: { audio?: Blob; text?: string }) {
    const listener = live.current;
    listener?.pause();
    setThinking(true);
    setStatus("");
    let replyId: number | null = null;
    let queue: SpeechQueue | null = null;
    try {
      const id = await ensureSession();
      for await (const event of converseStream(id, input)) {
        if (event.type === "heard") {
          const alternatives = event.alternatives.length ? `Also heard: ${event.alternatives.join(" / ")}` : null;
          addBubble({ role: "patient", ewe: event.text, fr: alternatives });
        } else if (event.type === "sentence") {
          const audio = event.audio_wav_base64;
          if (replyId === null) {
            replyId = addBubble({ role: "assistant", ewe: event.text, audios: audio ? [audio] : [] });
            setThinking(false);
            if (listener && listener !== live.current) return; // live conversation was ended meanwhile
            listener?.resume(); // listen while speaking, so the person can interrupt
            if (!listener) setStatus("Tap to answer");
            queue = startSpeaking();
          } else {
            updateBubble(replyId, (b) => ({
              ewe: `${b.ewe} ${event.text}`,
              audios: audio ? [...(b.audios ?? []), audio] : b.audios,
            }));
          }
          if (audio) queue?.add({ audio });
          if (event.tts_error) setStatus("Reply ready (voice unavailable)");
        } else if (event.type === "done") {
          if (replyId !== null) updateBubble(replyId, () => ({ urgent: !!event.urgent }));
          setStaff({
            summary: event.staff_summary_fr || "Aucun résumé pour le moment.",
            urgent: !!event.urgent,
            department: event.department,
          });
        }
      }
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      queue?.end();
      setThinking(false);
      if (listener === live.current) listener?.resume();
    }
  }

  // --- Live conversation ---
  async function startLive() {
    const listener = new LiveListener({
      onUtterance: (wav) => runTurn({ audio: wav }),
      onState: (s) => {
        setHeard(s);
        if (s === "hearing") setStatus("");
      },
      onLevel: (rms) => micRef.current?.style.setProperty("--level", String(Math.min(1, rms * 10))),
      onBargeIn: stopSpeaking,
    });
    listener.setEndSilence(PAUSES[pause].ms);
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
    stopSpeaking();
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
      toWav16k(new Blob(chunks, { type: rec.mimeType }))
        .then((audio) => runTurn({ audio }))
        .catch((e) => setStatus((e as Error).message));
    };
    stopSpeaking();
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
    stopSpeaking();
    await runTurn({ text: value });
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
    stopSpeaking();
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
            {!!b.audios?.length && (
              <>
                <br />
                <button className="play" onClick={() => replay(b.audios)}>▶ Play again</button>
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
        {style === "live" && <PauseControl value={pause} onChange={choosePause} />}
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
