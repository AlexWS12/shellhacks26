"use client";

// Which kinds of other utilities the research team covers in the next live run: one small menu in the header, like
// Export. The Setup modal's Run pipeline section shows the same choice as pills.

import { useEffect, useRef, useState } from "react";

import { CATEGORY_LABEL } from "@/lib/format";
import { run, useRev } from "@/lib/run";
import { CATEGORIES, useUI } from "@/lib/ui";
import { CATEGORY_KINDS } from "@/lib/utilityIcons";

import UtilityIcon from "./UtilityIcon";

const TIP = "Before a live run: which other utilities the research team looks up near the river. Replays show what was recorded.";

function Pills() {
  const research = useUI((s) => s.research);
  const setResearch = useUI((s) => s.setResearch);
  const running = run.phase === "running";
  return (
    <>
      {CATEGORIES.map((c) => (
        <button key={c} type="button" className={`chip rpill cat-${c}`} aria-pressed={research[c]} disabled={running}
          onClick={() => setResearch(c, !research[c])}>
          {CATEGORY_KINDS[c].map((k) => <UtilityIcon key={k} kind={k} />)}
          {CATEGORY_LABEL[c]}
        </button>
      ))}
    </>
  );
}

export default function ResearchPicker() {
  useRev((s) => s.rev);
  const research = useUI((s) => s.research);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const on = CATEGORIES.filter((c) => research[c]);
  return (
    <div className="menu" ref={ref}>
      <button className="export research-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)} title={TIP}>
        Research
        <span className="research-icons" aria-hidden="true">
          {on.flatMap((c) => CATEGORY_KINDS[c].map((k) => <span key={k} className={`cat-${c}`}><UtilityIcon kind={k} /></span>))}
        </span>
        {on.length === 0 && <span className="research-off">off</span>} ▾
      </button>
      {open && (
        <div className="menu-list research-list" role="group" aria-label="Research team covers">
          <span className="lead">Next live run looks up</span>
          <Pills />
        </div>
      )}
    </div>
  );
}
