"use client";

// Learn Ewe words: type a word in English, French or Ewe, see and hear it.
import { FormEvent, useEffect, useState } from "react";
import { Lang, learn, Lesson, OtherLang } from "@/lib/api";
import { play, speakText, stopPlayback } from "@/lib/audio";

const NAMES: Record<Lang, string> = { ewe: "Eʋegbe", eng: "English", fra: "Français" };
const RECENT_KEY = "echovoiice.learn.recent";
const MAX_RECENT = 12;

interface Recent {
  text: string;
  source: Lang;
  other: OtherLang;
}

export default function LearnPage() {
  const [source, setSource] = useState<Lang>("eng");
  const [other, setOther] = useState<OtherLang>("eng");
  const [text, setText] = useState("");
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [recent, setRecent] = useState<Recent[]>([]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      if (Array.isArray(saved)) setRecent(saved.slice(0, MAX_RECENT));
    } catch {}
    return () => stopPlayback();
  }, []);

  function remember(item: Recent) {
    setRecent((prev) => {
      const next = [item, ...prev.filter((r) => r.text.toLowerCase() !== item.text.toLowerCase())].slice(0, MAX_RECENT);
      try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  async function lookUp(word: string, from: Lang, into: OtherLang) {
    if (!word || loading) return;
    stopPlayback();
    setLoading(true);
    setError("");
    try {
      const data = await learn(word, from, into);
      setLesson(data);
      remember({ text: word, source: from, other: into });
      play(data.audio_word_base64);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    lookUp(text.trim(), source, source === "ewe" ? other : source);
  }

  function openRecent(r: Recent) {
    setSource(r.source);
    setOther(r.other);
    setText(r.text);
    lookUp(r.text, r.source, r.other);
  }

  const placeholder =
    source === "ewe" ? "Ŋlɔ nya aɖe… (e.g. akpe)" : source === "fra" ? "Un mot… (ex. merci)" : "A word… (e.g. thank you)";

  return (
    <main className="learn">
      <form className="learn-form" onSubmit={onSubmit}>
        <div className="learn-langs">
          <span className="muted">From</span>
          <div className="segmented" role="group" aria-label="Word language">
            {(["eng", "fra", "ewe"] as Lang[]).map((l) => (
              <button type="button" key={l} aria-pressed={source === l} onClick={() => setSource(l)}>
                {NAMES[l]}
              </button>
            ))}
          </div>
          {source === "ewe" && (
            <>
              <span className="muted">into</span>
              <div className="segmented" role="group" aria-label="Meaning language">
                {(["eng", "fra"] as OtherLang[]).map((l) => (
                  <button type="button" key={l} aria-pressed={other === l} onClick={() => setOther(l)}>
                    {NAMES[l]}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="text-form">
          <input
            type="text"
            placeholder={placeholder}
            autoComplete="off"
            lang={source === "ewe" ? "ee" : source === "fra" ? "fr" : "en"}
            value={text}
            maxLength={200}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="primary" disabled={loading || !text.trim()}>
            {loading ? "…" : "Learn"}
          </button>
        </div>
      </form>

      {error && <p className="status error">{error}</p>}

      {lesson && (
        <article className="lesson" aria-live="polite">
          <div className="lesson-word">
            <div>
              <p className="label">Eʋegbe</p>
              <p className="ewe">{lesson.ewe}</p>
              {lesson.pronunciation && <p className="muted">Say it: {lesson.pronunciation}</p>}
            </div>
            <button className="listen" aria-label="Listen to the Ewe word" onClick={() => play(lesson.audio_word_base64)}
              disabled={!lesson.audio_word_base64}>
              ▶
            </button>
          </div>

          <div className="lesson-meaning">
            <p className="label">{NAMES[lesson.other]}</p>
            <p>
              {lesson.other_text}{" "}
              <button className="play" onClick={() => speakText(lesson.other_text, lesson.other)}>▶</button>
            </p>
          </div>

          {lesson.example_ewe && (
            <div className="lesson-example">
              <p className="label">Example</p>
              <p className="ewe-sentence">
                {lesson.example_ewe}{" "}
                {lesson.audio_example_base64 && (
                  <button className="play" onClick={() => play(lesson.audio_example_base64)}>▶</button>
                )}
              </p>
              <p className="muted">{lesson.example_other}</p>
            </div>
          )}

          {lesson.note && <p className="note">{lesson.note}</p>}
          {lesson.tts_error && <p className="muted">Voice unavailable right now.</p>}
        </article>
      )}

      {!lesson && !loading && (
        <p className="empty">Type a word or short phrase to see it in Eʋegbe, hear how it sounds, and use it in a sentence.</p>
      )}

      {recent.length > 0 && (
        <section className="recent">
          <p className="label">Recent words</p>
          <div className="chips">
            {recent.map((r) => (
              <button key={`${r.source}-${r.text}`} className="chip" onClick={() => openRecent(r)}>
                {r.text}
              </button>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
