"use client";

// Setup · Sources: every utility the pipeline reads, and adding more. A PDF filing goes through the AI reader
// (AddFiling: upload, describe, read, review, activate); a spreadsheet or link through PlanForm. Built-in sources are
// marked and can't be removed. On a hosted server changing anything needs the admin passcode.

import { useEffect, useState } from "react";

import { api, ApiError, setPasscode } from "@/lib/api";
import { setSources } from "@/lib/owners";
import { run, useRev } from "@/lib/run";
import type { SourceView } from "@/lib/types";
import { closeSetup, refreshAgents, showLatestResults, useUI } from "@/lib/ui";

import AddFiling from "../AddFiling";
import PlanForm from "../PlanForm";
import { errText } from "./common";
import { Marker } from "./RunSection";
import type { Go } from "./types";

type Kind = "pdf" | "sheet" | "url";
const KINDS: { id: Kind; label: string }[] = [{ id: "pdf", label: "Filing (PDF)" }, { id: "sheet", label: "Spreadsheet" }, { id: "url", label: "Link" }];
const STATUS: Record<string, string> = {
  active: "Active", review: "Waiting for review", extracting: "Reading…", draft: "Not read yet", failed: "Failed",
};
const FILE_INPUT = "su-filing-file";
const PLAN_FORM = "su-planform";

const uploaded = (s: SourceView) => (s.display as { origin?: string }).origin === "upload";

export default function SourcesSection({ go, review }: { go: Go; review: string | null }) {
  useRev((s) => s.rev);
  const [list, setList] = useState<SourceView[] | null>(null);
  const [view, setView] = useState<"list" | "add">("list");
  const [kind, setKind] = useState<Kind>("pdf");
  const [resume, setResume] = useState<SourceView | null>(null);
  const [filingStep, setFilingStep] = useState(0);
  const [plan, setPlan] = useState({ ready: false, busy: false, mapping: false });
  const [locked, setLocked] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const running = run.phase === "running";

  const load = () => api.sources().then((r) => { setList(r.sources); setSources(r.sources); return r.sources; })
    .catch((e) => { setError(errText(e)); return null; });
  useEffect(() => { void load(); }, []);

  // Opened from "Review" elsewhere: go straight to that source's step.
  useEffect(() => {
    if (!review || !list) return;
    const s = list.find((x) => x.id === review);
    if (s) openFiling(s);
  }, [review, list]);

  function openFiling(s: SourceView | null) {
    setResume(s);
    setKind("pdf");
    setFilingStep(s ? 2 : 0);
    setView("add");
  }

  function back() {
    setView("list");
    setResume(null);
    void load();
  }

  // Any change here that needs the passcode opens the unlock form instead of failing.
  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError("");
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && [401, 429, 503].includes(e.status)) setLocked(e.message);
      else setError(errText(e));
      return undefined;
    }
  }

  async function deactivate(s: SourceView) {
    setBusy(true);
    const r = await guarded(() => api.admin.deactivate(s.id));
    if (r) {
      setNote(`${s.display_name} is off: ${r.removed_projects} projects and their overlaps left the map.`);
      await showLatestResults();
    }
    setBusy(false);
    void load();
  }

  async function remove(s: SourceView) {
    setBusy(true);
    const r = await guarded(() => api.admin.remove(s.id));
    if (r) {
      setNote(`${s.display_name} removed${r.removed_projects ? `, with ${r.removed_projects} projects and their overlaps` : ""}.`);
      setConfirm(null);
      void refreshAgents();
      if (r.removed_projects) await showLatestResults();
    }
    setBusy(false);
    void load();
  }

  async function activated() {
    closeSetup();
    void refreshAgents();
    await showLatestResults(); // the map now shows the new source and its overlaps
    useUI.getState().flyTo({ kind: "border" });
  }

  const primary = kind === "pdf" ? (filingStep === 0 && !resume ? "Upload" : null)
    : plan.mapping ? null : kind === "sheet" ? "Upload and match columns" : "Save link";

  return (
    <>
      <div className="su-body">
        {locked ? (
          <form className="unlock" onSubmit={(e) => { e.preventDefault(); setPasscode(code); setLocked(null); void load(); }}>
            <p>{locked} Changing sources on this server is for its admin.</p>
            <div className="fields"><label className="field"><span>Admin passcode</span>
              <input type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)} autoFocus /></label></div>
            <div className="row2"><button className="primary" type="submit" disabled={!code}>Unlock</button></div>
          </form>
        ) : view === "add" ? (
          <div className="su-add">
            <div className="su-add-head">
              <button className="linkbtn" onClick={back}>← All sources</button>
              {!resume && (
                <div className="seg" role="group" aria-label="What to add">
                  {KINDS.map((k) => <button key={k.id} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>{k.label}</button>)}
                </div>
              )}
              {resume && <b>{resume.display_name}</b>}
            </div>
            {kind === "pdf" ? (
              <AddFiling key={resume?.id ?? "new"} start={resume} inputId={FILE_INPUT} onStep={setFilingStep}
                onChanged={() => void load()} onActivated={() => void activated()} />
            ) : (
              <PlanForm key={kind} kind={kind === "sheet" ? "spreadsheet" : "url"} formId={PLAN_FORM} onState={setPlan}
                onDone={(msg) => { setNote(msg); back(); }} />
            )}
          </div>
        ) : (
          <>
            <p className="sub">Every utility the pipeline reads. Projects of different utilities are compared pair by pair on every run.</p>
            <div className="row2">
              <button className="primary" onClick={() => openFiling(null)} disabled={running}>+ Add a filing (PDF)</button>
              <button onClick={() => { setKind("sheet"); setResume(null); setView("add"); }} disabled={running}>+ Spreadsheet or link</button>
              {running && <span className="note">A run is in progress; add sources when it finishes.</span>}
            </div>
            <div className="su-srcs" role="table" aria-label="Sources">
              <div className="su-src head" role="row">
                <span role="columnheader">Utility</span><span role="columnheader">Projects</span><span role="columnheader">Status</span>
                <span role="columnheader">Read by</span><span role="columnheader" aria-label="Actions" />
              </div>
              {(list ?? []).map((s) => (
                <div key={s.id} className="su-src" role="row">
                  <span role="cell" className="who">
                    <Marker s={s} />
                    <span><b>{s.display_name}</b><span className="sub-inline">{s.code}{s.builtin && <span className="tag">built in</span>}</span></span>
                  </span>
                  <span role="cell" className="num">{s.projects ?? 0}{s.status === "review" && s.draft_projects ? <span className="sub-inline"> · {s.draft_projects} to review</span> : null}</span>
                  <span role="cell" className={s.status === "failed" ? "fail" : s.status === "active" ? "good" : s.status === "review" ? "warn" : ""}>{STATUS[s.status] ?? s.status}</span>
                  <span role="cell">{readerLabel(s)}</span>
                  <span role="cell" className="plan-actions">
                    {!s.builtin && uploaded(s) && s.status !== "active" && (
                      <button onClick={() => openFiling(s)} disabled={running}>
                        {s.status === "review" ? "Review" : s.status === "failed" ? "Try again" : "Continue"}
                      </button>
                    )}
                    {!s.builtin && uploaded(s) && s.status === "active" && (
                      <>
                        <button onClick={() => openFiling(s)}>Review</button>
                        <button onClick={() => void deactivate(s)} disabled={busy || running}>Deactivate</button>
                      </>
                    )}
                    {!s.builtin && (confirm === s.id
                      ? <button className="danger" onClick={() => void remove(s)} disabled={busy || running}>Confirm</button>
                      : <button onClick={() => setConfirm(s.id)} disabled={running}>Delete</button>)}
                  </span>
                </div>
              ))}
              {!list && <p className="note">Loading…</p>}
            </div>
            <p className="note">Deactivating or deleting a source takes its projects and their overlaps off the map. A filing that&apos;s
              already a source can&apos;t be added twice.</p>
          </>
        )}
        {error && <p className="err-inline" role="alert">{error}</p>}
      </div>
      {!locked && (
        <div className="su-foot">
          <span className={`note ${note ? "good" : ""}`}>{note && view === "list" ? note
            : view === "add" ? (kind === "pdf" ? "Nothing reaches the map until you review and activate it." : "Its Reader joins every live run.")
              : "Built-in sources can't be removed."}</span>
          <span className="spacer" />
          {view === "list" ? <button className="primary" onClick={() => go("run")}>Done</button> : (
            <>
              <button onClick={back}>Cancel</button>
              {primary && (kind === "pdf"
                ? <button className="primary" onClick={() => document.getElementById(FILE_INPUT)?.click()} disabled={running}>{primary}</button>
                : <button className="primary" type="submit" form={PLAN_FORM} disabled={!plan.ready || plan.busy || running}>{plan.busy ? "Saving…" : primary}</button>)}
            </>
          )}
        </div>
      )}
    </>
  );
}

function readerLabel(s: SourceView): string {
  if (s.builtin) return "Built-in parser";
  if (s.reader === "sheet") return "Spreadsheet columns";
  return uploaded(s) ? "AI reader, reviewed" : "AI, page by page";
}
