// How long live mode waits after you stop talking before it answers.
export const PAUSES = {
  short: { ms: 700, label: "Short" },
  normal: { ms: 1100, label: "Normal" },
  long: { ms: 1600, label: "Long" },
} as const;
export type Pause = keyof typeof PAUSES;

const KEY = "echovoiice.pause";

export function loadPause(): Pause {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && saved in PAUSES) return saved as Pause;
  } catch {}
  return "normal";
}

export function savePause(pause: Pause) {
  try { localStorage.setItem(KEY, pause); } catch {}
}
