"use client";

import { run, useRev } from "@/lib/run";
import { skipToResults, startRun, useUI } from "@/lib/ui";

import ResearchPicker from "./ResearchPicker";

export default function Header() {
  useRev((s) => s.rev);
  const { filters, setFilters, health, templateFallback, setTemplateFallback } = useUI();
  const running = run.phase === "running";
  const jevOn = Boolean(health && health.jev !== "off" && health.jev !== "mock");
  return (
    <header className="top">
      <div className="brand">
        <svg className="mark" viewBox="0 0 22 22" fill="none" strokeWidth="2" aria-hidden="true">
          <circle className="a" cx="8" cy="11" r="6" />
          <circle className="b" cx="14" cy="11" r="6" />
        </svg>
        <h1>UtiliTies</h1>
        <span className="tag">Coordinating utility construction across state lines</span>
      </div>
      <div className="status" aria-label="Status">
        {running && <span className="live"><i />{run.mode === "replay" ? "Replay" : "Live"}</span>}
        <span className={health?.gemini ? "on" : ""} title={health?.gemini_model}><i />Gemini</span>
        <span className={jevOn ? "on" : ""} title={`Jev: ${health?.jev ?? "…"}`}><i />Jev{health?.jev === "mock" ? " (mock)" : ""}</span>
        <span className={health?.tiger ? "on" : ""}><i />Tiger Data</span>
      </div>
      <span className="spacer" />
      <div className="filters">
        <label><input type="checkbox" checked={filters.allSponsors} onChange={(e) => setFilters({ allSponsors: e.target.checked })} /> GTC, MEAG, DU</label>
        <label><input type="checkbox" checked={filters.townLevel} onChange={(e) => setFilters({ townLevel: e.target.checked })} /> Approx. locations</label>
        <label><input type="checkbox" checked={filters.hideFinished} onChange={(e) => setFilters({ hideFinished: e.target.checked })} /> Hide finished</label>
      </div>
      <ResearchPicker compact />
      {running && <button onClick={() => void skipToResults()}>Skip</button>}
      <label className="fallback" title="Live runs: when Gemini fails after retries, write the text from a template. Off: the agent fails instead.">
        <input type="checkbox" checked={templateFallback} disabled={running}
          onChange={(e) => setTemplateFallback(e.target.checked)} /> Template fallback
      </label>
      <button onClick={() => void startRun("replay")} disabled={running} title="Replays the newest recorded run. Works offline.">Replay</button>
      <button className="primary" onClick={() => void startRun("live")} disabled={running}>
        {running ? "Running…" : run.phase === "done" ? "Run again" : "Run pipeline"}
      </button>
    </header>
  );
}
