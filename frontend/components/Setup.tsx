"use client";

// The Setup modal: Run pipeline · Models · Sources, switched from a sidebar. Every section stays mounted while
// another is showing, so a model draft or an upload in progress survives switching.

import { useRef, useState } from "react";

import { useFocusTrap } from "@/lib/focus";
import { useSources } from "@/lib/owners";
import { closeSetup, useUI, type SetupTab } from "@/lib/ui";

import { plural } from "./setup/common";
import ModelsSection from "./setup/ModelsSection";
import RunSection from "./setup/RunSection";
import SourcesSection from "./setup/SourcesSection";
import type { Go, ModelStatus, ModelStep } from "./setup/types";

const TITLE: Record<SetupTab, string> = { run: "Run pipeline", models: "Models", sources: "Sources" };

export default function Setup() {
  const setup = useUI((s) => s.setup)!;
  const health = useUI((s) => s.health);
  const sources = useSources((st) => st.list);
  const [tab, setTab] = useState<SetupTab>(setup.tab ?? "run");
  const [step, setStep] = useState<ModelStep>(setup.step === "keys" ? "keys" : "jobs");
  const [review, setReview] = useState<string | null>(setup.reviewSource ?? null);
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useFocusTrap(box, closeSetup);

  const go: Go = (t, opts = {}) => {
    setTab(t);
    if (opts.step) setStep(opts.step);
    if (opts.review !== undefined) setReview(opts.review);
  };

  const missing = health?.models_setup?.problems.length ?? 0;
  const active = sources.filter((s) => s.status === "active").length;
  const waiting = sources.filter((s) => s.status === "review").length;
  const nav: { id: SetupTab; meta: string; warn: boolean }[] = [
    { id: "run", meta: `${mode === "live" ? "Live" : "Replay"} · ${plural(active, "utility", "utilities")}`, warn: false },
    { id: "models", warn: missing > 0 || Boolean(status?.noneWork || status?.backupFails),
      meta: missing ? `${plural(missing, "job")} need a model` : status?.noneWork ? `${plural(status.noneWork, "job")} failing`
        : status?.backupFails ? "A backup is failing" : status?.tested ? "Every job has one" : "Keys, jobs, test" },
    { id: "sources", warn: waiting > 0, meta: `${active} active${waiting ? ` · ${waiting} to review` : ""}` },
  ];

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && closeSetup()}>
      <div ref={box} className="su" role="dialog" aria-modal="true" aria-labelledby="su-title" tabIndex={-1}>
        <nav className="su-side" aria-label="Setup sections">
          <div className="su-side-top"><span className="label">Setup</span></div>
          <div className="su-nav">
            {nav.map((n, i) => (
              <button key={n.id} className={`su-navitem ${tab === n.id ? "on" : ""}`} aria-current={tab === n.id ? "page" : undefined}
                onClick={() => go(n.id)}>
                <span className="n">{i + 1}</span>
                <span className="t">{TITLE[n.id]}</span>
                <span className={`m ${n.warn ? "warn" : ""}`}>{n.meta}</span>
              </button>
            ))}
          </div>
          <p className="su-side-note">Opens by itself on first launch, and when a run is refused.</p>
        </nav>
        <div className="su-main">
          <div className="su-head">
            <h2 id="su-title">{TITLE[tab]}</h2>
            <button className="linkbtn" onClick={closeSetup}>Close</button>
          </div>
          <section className="su-section" hidden={tab !== "run"} aria-label="Run pipeline">
            <RunSection go={go} modelStatus={status} mode={mode} setMode={setMode} />
          </section>
          <section className="su-section" hidden={tab !== "models"} aria-label="Models">
            <ModelsSection setup={setup} step={step} setStep={setStep} go={go} onStatus={setStatus} />
          </section>
          <section className="su-section" hidden={tab !== "sources"} aria-label="Sources">
            <SourcesSection go={go} review={review} />
          </section>
        </div>
      </div>
    </div>
  );
}
