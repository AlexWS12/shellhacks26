"use client";

// Setup · Run pipeline: what the next run will use (keys, models, sources), then how to run it.

import { CATEGORY_LABEL } from "@/lib/format";
import { useSources } from "@/lib/owners";
import { run, useRev } from "@/lib/run";
import type { SourceView } from "@/lib/types";
import { CATEGORIES, closeSetup, startRun, useUI } from "@/lib/ui";
import { CATEGORY_KINDS } from "@/lib/utilityIcons";

import UtilityIcon from "../UtilityIcon";
import { plural } from "./common";
import type { Go, ModelStatus } from "./types";

export default function RunSection({ go, modelStatus, mode, setMode }: {
  go: Go; modelStatus: ModelStatus | null; mode: "live" | "replay"; setMode: (m: "live" | "replay") => void;
}) {
  useRev((s) => s.rev);
  const health = useUI((s) => s.health);
  const research = useUI((s) => s.research);
  const setResearch = useUI((s) => s.setResearch);
  const fallback = useUI((s) => s.templateFallback);
  const setFallback = useUI((s) => s.setTemplateFallback);
  const sources = useSources((st) => st.list);
  const running = run.phase === "running";

  const jevOn = Boolean(health && health.jev !== "off" && health.jev !== "mock");
  const keys = health?.gemini && jevOn ? { text: "Gemini and Jev set", ok: true }
    : health?.gemini ? { text: "Gemini set", warn: "Jev isn't set: quick decisions use Gemini or rules" }
      : jevOn ? { text: "Jev set", warn: "Gemini isn't set" } : { text: "No keys yet", warn: "Add a key under Models" };
  const ms = health?.models_setup;
  const missing = ms?.problems.length ?? 0;
  const models = missing ? { text: `${plural(missing, "job")} without a working model`, bad: true }
    : { text: "Every job has one", ok: true };
  const backupWarn = modelStatus?.backupFails ? "A backup is over its limit" : null;
  const active = sources.filter((s) => s.status === "active");
  const waiting = sources.filter((s) => s.status === "review");

  return (
    <>
      <div className="su-body">
        <div className="su-run">
          <div className="su-ready">
            <button onClick={() => go("models", { step: "keys" })}>
              <span className="label">Keys</span>
              <b className={keys.ok ? "good" : ""}>{keys.text}</b>
              {keys.warn && <span className="warn">{keys.warn}</span>}
            </button>
            <button onClick={() => go("models", { step: "jobs" })}>
              <span className="label">Models</span>
              <b className={models.bad ? "fail" : "good"}>{models.text}</b>
              {backupWarn && <span className="warn">{backupWarn}</span>}
            </button>
            <button onClick={() => go("sources")}>
              <span className="label">Sources</span>
              <b>{plural(active.length, "utility", "utilities")} active</b>
              {waiting.length > 0 && <span className="warn">{waiting.length} waiting for review</span>}
            </button>
          </div>

          <div>
            <p className="label">Mode</p>
            <div className="su-modes" role="radiogroup" aria-label="Mode">
              <ModeCard on={mode === "live"} onPick={() => setMode("live")} title="Live run"
                text="Reads every active source, calls the models, and records the run." />
              <ModeCard on={mode === "replay"} onPick={() => setMode("replay")} title="Replay"
                text="Plays back the newest recorded run. Works offline, no keys needed." />
            </div>
          </div>

          <div>
            <p className="label">Utilities compared <span className="hint">pair by pair, every project within 25 miles</span>
              <button className="linkbtn right" onClick={() => go("sources")}>Manage sources</button></p>
            <div className="su-utils">
              {active.map((s) => <UtilityRow key={s.id} s={s} />)}
              {waiting.map((s) => (
                <div key={s.id} className="su-util waiting">
                  <Marker s={s} hollow />
                  <span className="note">{s.display_name} is left out until its {s.draft_projects ?? ""} projects are reviewed.</span>
                  <button onClick={() => go("sources", { review: s.id })}>Review</button>
                </div>
              ))}
              {active.length === 0 && <p className="empty">No active sources yet.</p>}
            </div>
          </div>

          <div className={mode === "replay" ? "su-off" : ""} inert={mode === "replay" || undefined}>
            <p className="label">Research team looks up
              {mode === "replay" && <span className="hint">A replay shows what was recorded.</span>}</p>
            <div className="chips">
              {CATEGORIES.map((c) => (
                <button key={c} type="button" className={`chip rpill cat-${c}`} aria-pressed={research[c]} disabled={running}
                  onClick={() => setResearch(c, !research[c])}>
                  {CATEGORY_KINDS[c].map((k) => <UtilityIcon key={k} kind={k} />)}
                  {CATEGORY_LABEL[c]}
                </button>
              ))}
            </div>
          </div>

          <label className="su-toggle">
            <input type="checkbox" role="switch" checked={fallback} disabled={running} onChange={(e) => setFallback(e.target.checked)} />
            <span className="track" aria-hidden="true" />
            <span><b>Template fallback</b>
              <span className="note">When Gemini keeps failing during a live run, write that text from a template instead of
                stopping the agent.</span></span>
          </label>
        </div>
      </div>
      <div className="su-foot">
        <span className="note">{running ? "A run is in progress."
          : mode === "live" ? "Uses the keys and models set under Models, and the active sources."
            : "Plays back what was recorded. Nothing is sent to a model."}</span>
        <span className="spacer" />
        <button className="primary" disabled={running} onClick={() => { closeSetup(); void startRun(mode); }}>
          {mode === "live" ? "Run pipeline" : "Replay the run"}
        </button>
      </div>
    </>
  );
}

function ModeCard({ on, onPick, title, text }: { on: boolean; onPick: () => void; title: string; text: string }) {
  return (
    <button role="radio" aria-checked={on} className={`su-mode ${on ? "on" : ""}`} onClick={onPick}>
      <i className="radio" aria-hidden="true" />
      <span><b>{title}</b>{text}</span>
    </button>
  );
}

export function Marker({ s, hollow = false }: { s: SourceView; hollow?: boolean }) {
  return <i className={`mk ${s.display.shape === "diamond" ? "diamond" : "circle"} ${hollow ? "hollow" : ""}`}
    style={{ "--own": s.color } as React.CSSProperties} aria-hidden="true" />;
}

function UtilityRow({ s }: { s: SourceView }) {
  return (
    <div className="su-util">
      <Marker s={s} />
      <b>{s.display_name}</b>
      <span className="code">{s.code}</span>
      <span className="spacer" />
      <span className="num">{s.projects != null ? plural(s.projects, "project") : ""}</span>
    </div>
  );
}
