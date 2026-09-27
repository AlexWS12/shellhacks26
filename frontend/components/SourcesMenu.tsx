"use client";

// The Sources menu: every utility the pipeline reads, and adding more. A PDF filing goes through the AI reader
// (AddFiling: upload, describe, read, review, activate); a spreadsheet or link through PlanForm. Built-in sources are
// marked and can't be removed. On a hosted server changing anything needs the admin passcode.

import { useEffect, useRef, useState } from "react";

import { api, ApiError, setPasscode } from "@/lib/api";
import { useFocusTrap } from "@/lib/focus";
import { setSources } from "@/lib/owners";
import { run } from "@/lib/run";
import type { SourceView } from "@/lib/types";
import { refreshAgents, showLatestResults, useUI } from "@/lib/ui";

import AddFiling from "./AddFiling";
import PlanForm from "./PlanForm";

type View = { kind: "list" } | { kind: "filing"; source: SourceView | null } | { kind: "plan" };

const STATUS: Record<string, string> = {
  active: "Active", review: "Waiting for review", extracting: "Reading…", draft: "Not read yet", failed: "Failed",
};

function readerLabel(s: SourceView): string {
  if (s.builtin) return "Built-in parser";
  if (s.reader === "sheet") return "Spreadsheet columns";
  return (s.display as { origin?: string }).origin === "upload" ? "AI reader, reviewed" : "AI, page by page";
}

export default function SourcesMenu({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<SourceView[] | null>(null);
  const [view, setView] = useState<View>({ kind: "list" });
  const [locked, setLocked] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useFocusTrap(box, onClose);
  const running = run.phase === "running";

  const load = () => api.sources().then((r) => { setList(r.sources); setSources(r.sources); }).catch((e) => setError(String(e)));
  useEffect(() => { void load(); }, []);

  // Any change here that needs the passcode opens the unlock form instead of failing.
  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError("");
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && [401, 429, 503].includes(e.status)) setLocked(e.message);
      else setError(e instanceof Error ? e.message : String(e));
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
    onClose();
    void refreshAgents();
    await showLatestResults(); // the map now shows the new source and its overlaps
    useUI.getState().flyTo({ kind: "border" });
  }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} className="modal sources" role="dialog" aria-modal="true" aria-labelledby="sources-title" tabIndex={-1}>
        <div className="modal-head">
          <h2 id="sources-title">{view.kind === "list" ? "Sources" : view.kind === "plan" ? "Add a spreadsheet or link" : view.source ? view.source.display_name : "Add a filing"}</h2>
          {view.kind !== "list" && <button className="linkbtn" onClick={() => { setView({ kind: "list" }); void load(); }}>All sources</button>}
          <button className="linkbtn" onClick={onClose} aria-label="Close">Close</button>
        </div>

        {locked ? (
          <form className="unlock" onSubmit={(e) => { e.preventDefault(); setPasscode(code); setLocked(null); void load(); }}>
            <p>{locked} Changing sources on this server is for its admin.</p>
            <div className="fields"><label className="field"><span>Admin passcode</span>
              <input type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)} autoFocus /></label></div>
            <div className="row2"><button className="primary" type="submit" disabled={!code}>Unlock</button></div>
          </form>
        ) : view.kind === "filing" ? (
          <AddFiling start={view.source} onChanged={() => void load()} onActivated={() => void activated()} />
        ) : view.kind === "plan" ? (
          <PlanForm onDone={(msg) => { setNote(msg); setView({ kind: "list" }); void load(); }} />
        ) : (
          <>
            <p className="sub">Every utility the pipeline reads. Projects of different utilities are compared pair by pair on every run.</p>
            <div className="row2">
              <button className="primary" onClick={() => setView({ kind: "filing", source: null })} disabled={running}>+ Add a filing (PDF)</button>
              <button onClick={() => setView({ kind: "plan" })} disabled={running}>+ Spreadsheet or link</button>
              {running && <span className="note">A run is in progress; add sources when it finishes.</span>}
            </div>
            <div className="preview srclist">
              <table>
                <thead><tr><th>Utility</th><th>Projects</th><th>Status</th><th>Read by</th><th /></tr></thead>
                <tbody>
                  {(list ?? []).map((s) => (
                    <tr key={s.id}>
                      <td><span className="who">
                        <i className={`mk ${s.display.shape === "diamond" ? "diamond" : "circle"}`} style={{ "--own": s.color } as React.CSSProperties} aria-hidden />
                        <b>{s.display_name}</b> <span className="sub-inline">{s.code}</span>
                        {s.builtin && <span className="tag">built in</span>}
                      </span></td>
                      <td>{s.projects ?? 0}{s.status === "review" && s.draft_projects ? <span className="sub-inline"> · {s.draft_projects} to review</span> : null}</td>
                      <td className={s.status === "failed" ? "fail" : s.status === "active" ? "good" : ""}>{STATUS[s.status] ?? s.status}</td>
                      <td>{readerLabel(s)}</td>
                      <td><span className="plan-actions">
                        {!s.builtin && (s.display as { origin?: string }).origin === "upload" && s.status !== "active" && (
                          <button onClick={() => setView({ kind: "filing", source: s })} disabled={running}>
                            {s.status === "review" ? "Review" : s.status === "failed" ? "Try again" : "Continue"}
                          </button>
                        )}
                        {!s.builtin && (s.display as { origin?: string }).origin === "upload" && s.status === "active" && (
                          <>
                            <button onClick={() => setView({ kind: "filing", source: s })}>Review</button>
                            <button onClick={() => void deactivate(s)} disabled={busy || running}>Deactivate</button>
                          </>
                        )}
                        {!s.builtin && (confirm === s.id
                          ? <button className="danger" onClick={() => void remove(s)} disabled={busy || running}>Confirm delete</button>
                          : <button onClick={() => setConfirm(s.id)} disabled={running}>Delete</button>)}
                      </span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!list && <p className="note">Loading…</p>}
            </div>
            <p className="note">Deactivating or deleting a source takes its projects and their overlaps off the map. A filing that&apos;s
              already a source can&apos;t be added twice.</p>
          </>
        )}
        {error && <p className="err-inline" role="alert">{error}</p>}
        {note && view.kind === "list" && <p className="ok-inline">{note}</p>}
      </div>
    </div>
  );
}
