"use client";

// Which kinds of other utilities the research team covers in the next live run.

import { CATEGORY_LABEL } from "@/lib/format";
import { run, useRev } from "@/lib/run";
import { CATEGORIES, useUI } from "@/lib/ui";

export default function ResearchPicker({ compact = false }: { compact?: boolean }) {
  useRev((s) => s.rev);
  const research = useUI((s) => s.research);
  const setResearch = useUI((s) => s.setResearch);
  const running = run.phase === "running";
  return (
    <div className={`filters research ${compact ? "compact" : ""}`} role="group" aria-label="Research team covers"
      title="Before a live run: which other utilities the research team looks up near the river. Replays show what was recorded.">
      <span className="lead">Research</span>
      {CATEGORIES.map((c) => (
        <label key={c} className={`cat-${c}`}>
          <input type="checkbox" checked={research[c]} disabled={running} onChange={(e) => setResearch(c, e.target.checked)} />
          {CATEGORY_LABEL[c]}
        </label>
      ))}
    </div>
  );
}
