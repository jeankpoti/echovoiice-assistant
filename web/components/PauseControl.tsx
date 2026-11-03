"use client";

import { Pause, PAUSES } from "@/lib/pause";

// Short answers sooner; Long gives more time to think mid-sentence without being cut off.
export default function PauseControl({ value, onChange }: { value: Pause; onChange: (p: Pause) => void }) {
  return (
    <div className="who">
      <span className="muted">Pause before reply</span>
      <div className="segmented" role="group" aria-label="Pause before reply">
        {(Object.keys(PAUSES) as Pause[]).map((p) => (
          <button key={p} aria-pressed={value === p} onClick={() => onChange(p)} title={`${PAUSES[p].ms / 1000} s`}>
            {PAUSES[p].label}
          </button>
        ))}
      </div>
    </div>
  );
}
