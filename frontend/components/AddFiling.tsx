"use client";

// Add a utility's filing: 1 upload the PDF, 2 describe the utility, 3 read it with the AI reader (live progress from
// the run's event stream), 4 review what it found, 5 activate: its projects are placed on the map and compared with
// every other utility's. A source already added opens at the step it's at.

import { useEffect, useRef, useState } from "react";

import { setRetry, showPreflight } from "@/lib/alerts";
import { api, ApiError, type PreflightDetail } from "@/lib/api";
import { followRun } from "@/lib/follow";
import type { Estimate, Review, RunEvent, SourceView } from "@/lib/types";
import { useUI } from "@/lib/ui";

import ReviewTable from "./ReviewTable";

const STEPS = ["Upload", "Describe", "Read", "Review", "Activate"] as const;
type Step = 0 | 1 | 2 | 3 | 4;

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const suggestCode = (name: string) =>
  name.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w) && !/^(of|and|the|&)$/i.test(w)).map((w) => w[0]).join("").toUpperCase().slice(0, 8);

export default function AddFiling({ start, onActivated, onChanged, inputId, onStep }: {
  start: SourceView | null; // resume an added source at its step
  onActivated: () => void;
  onChanged: () => void; // the list should reload
  inputId: string; // the Setup modal's Upload button opens this file input
  onStep: (step: number) => void;
}) {
  const roles = useUI((s) => s.health?.models) ?? {};
  const [step, setStep] = useState<Step>(start ? (start.status === "review" || start.status === "active" ? 3 : 2) : 0);
  const [meta, setMeta] = useState<{ states: string[]; max_upload_mb: number; page_images: boolean } | null>(null);
  const [upload, setUpload] = useState<{ upload_id: string; filename: string; size: number; pages: number; has_text: boolean } | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [codeTouched, setCodeTouched] = useState(false);
  const [states, setStates] = useState<string[]>([]);
  const [ops, setOps] = useState<string[]>([]);
  const [opDraft, setOpDraft] = useState("");
  const [pages, setPages] = useState("");
  const [source, setSource] = useState<SourceView | null>(start);
  const [est, setEst] = useState<{ estimate: Estimate | null; allowed: boolean; message?: string } | null>(null);
  const [progress, setProgress] = useState<{ located: number | null; total: number; found: string[]; failed: number; done: boolean; msg: string; noModel: boolean }>(
    { located: null, total: 0, found: [], failed: 0, done: false, msg: "", noModel: false });
  const [review, setReview] = useState<Review | null>(null);
  const [placing, setPlacing] = useState<{ placed: number; unlocated: number; pairs: number; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const stop = useRef<(() => void) | null>(null);

  const [over, setOver] = useState(false); // a file is being dragged over the drop zone
  useEffect(() => onStep(step), [step]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void api.admin.meta().then(setMeta).catch(() => undefined);
    return () => { stop.current?.(); setRetry(null); };
  }, []);
  useEffect(() => { // the steps that load something when opened
    if (step === 2 && source && !est) void api.admin.estimate(source.id).then(setEst).catch((e) => setError(errText(e)));
    if (step >= 3 && source && !review) void api.admin.review(source.id).then(setReview).catch((e) => setError(errText(e)));
  }, [step, source]); // eslint-disable-line react-hooks/exhaustive-deps

  async function pick(file: File | undefined) {
    if (!file) return;
    setError("");
    if (meta && file.size > meta.max_upload_mb * 1024 * 1024) return setError(`Files up to ${meta.max_upload_mb} MB.`);
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return setError("Filings are added as PDFs.");
    setBusy(true);
    try {
      const up = await api.admin.upload(file.name, await toBase64(file));
      setUpload(up);
      setStep(1);
    } catch (e) {
      setError(errText(e)); // e.g. "This filing is already a source: ..." stops here
    } finally {
      setBusy(false);
    }
  }

  function addOp() {
    const v = opDraft.trim();
    if (v && !ops.some((o) => o.toLowerCase() === v.toLowerCase())) setOps([...ops, v]);
    setOpDraft("");
  }

  async function describe(e: React.FormEvent) {
    e.preventDefault();
    if (!upload) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.admin.create({ upload_id: upload.upload_id, filename: upload.filename, display_name: name, code,
        states, operators: opDraft.trim() ? [...ops, opDraft.trim()] : ops, pages: pages.trim() || null });
      setSource(r.source);
      onChanged();
      setStep(2);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  async function read() {
    if (!source) return;
    setBusy(true);
    setError("");
    setProgress({ located: null, total: 0, found: [], failed: 0, done: false, msg: "Opening the filing…", noModel: false });
    setRetry(() => void read()); // the failure modal's "Retry run" reads the filing again
    try {
      const { run_id } = await api.admin.extract(source.id);
      stop.current?.();
      stop.current = followRun(run_id, roles, (ev: RunEvent) => {
        setProgress((p) => {
          if (ev.type === "source.opened") return { ...p, msg: `${ev.pages} pages opened. Finding the project pages…` };
          if (ev.type === "pages.located") {
            const c = (ev.candidates as unknown[]) ?? [];
            return { ...p, located: c.length, total: Number(ev.total), msg: `${c.length} of ${ev.total} pages list projects. Reading them…` };
          }
          if (ev.type === "project.extracted") return { ...p, found: [...p.found, String((ev.project as { name: string }).name)] };
          if (ev.type === "project.extract_failed") return { ...p, failed: p.failed + 1, noModel: p.noModel || /no model could/.test(String(ev.reason)) };
          if (ev.type === "role.exhausted") return { ...p, noModel: true };
          if (ev.type === "agent.error") return { ...p, msg: `Stopped: ${ev.message}` };
          if (ev.type === "run.done" || ev.type === "run.failed") return { ...p, done: true };
          return p;
        });
        if (ev.type === "run.done" && ev.ok) {
          setRetry(null);
          setBusy(false);
          onChanged();
          void api.admin.review(source.id).then((r) => {
            if (!r.projects.length) {  // nothing to review: say why here instead of opening an empty table
              setProgress((p) => ({ ...p, msg: p.noModel
                ? "No model could read the pages (see the message above). Pick another model for “Read filings without a parser” in Model setup, then read it again."
                : "No projects were found on these pages. Check the page range." }));
              return;
            }
            setReview(r);
            setStep(3);
          }).catch(() => setProgress((p) => ({ ...p, msg: "No projects were found in this filing. Check the page range, or try another model in Model setup." })));
        } else if (ev.type === "run.done" || ev.type === "run.failed") {
          setBusy(false);
          onChanged();
        }
      });
    } catch (e) {
      setBusy(false);
      const d = e instanceof ApiError && e.status === 409 ? e.detail as PreflightDetail | null : null;
      if (d?.problems?.length) {  // the reader has no working model: the failure modal says why, with a way into setup
        setProgress((p) => ({ ...p, msg: "Not read: no model can do “Read filings without a parser” right now. Nothing was sent to a model." }));
        showPreflight(d);
        return;
      }
      setError(errText(e));
    }
  }

  async function activate() {
    if (!source) return;
    setBusy(true);
    setError("");
    setPlacing({ placed: 0, unlocated: 0, pairs: 0, msg: "Placing the projects on the map…" });
    try {
      const { run_id } = await api.admin.activate(source.id);
      stop.current?.();
      stop.current = followRun(run_id, roles, (ev: RunEvent) => {
        setPlacing((p) => {
          if (!p) return p;
          if (ev.type === "project.placed") return { ...p, placed: p.placed + 1 };
          if (ev.type === "project.unlocated") return { ...p, unlocated: p.unlocated + 1 };
          if (ev.type === "overlap.found") return { ...p, pairs: p.pairs + 1, msg: "Comparing with every other utility…" };
          if (ev.type === "agent.error" || ev.type === "run.failed") return { ...p, msg: `Stopped: ${ev.message}` };
          return p;
        });
        if (ev.type === "run.done") {
          setBusy(false);
          onChanged();
          if (ev.ok) onActivated(); // back to the map, with the new source on it
        }
      });
    } catch (e) {
      setError(errText(e));
      setBusy(false);
      setPlacing(null);
    }
  }

  const accepted = review ? review.projects.filter((p) => review.review[p.id]?.status === "accepted") : [];
  const undated = accepted.filter((p) => !p.in_service_date).length;
  const unplaced = accepted.filter((p) => p.in_service_date && !p.endpoints.length).length;

  return (
    <div className="addfiling">
      <ol className="steps" aria-label="Steps">
        {STEPS.map((s, i) => <li key={s} className={i === step ? "on" : i < step ? "done" : ""}>{i + 1}. {s}</li>)}
      </ol>

      {step === 0 && (
        <div>
          <p className="sub">A PDF of another utility&apos;s construction plan, up to {meta?.max_upload_mb ?? 50} MB. Its projects are
            read by the AI reader, and you review every value against its page before anything reaches the map.</p>
          <label className={`su-drop ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); void pick(e.dataTransfer.files?.[0]); }}>
            <input id={inputId} type="file" accept=".pdf,application/pdf" onChange={(e) => void pick(e.target.files?.[0])} disabled={busy} />
            <b>Drop the filing here</b>
            <span>or click to choose a PDF, up to {meta?.max_upload_mb ?? 50} MB</span>
          </label>
          {busy && <p className="note">Uploading and opening it…</p>}
        </div>
      )}

      {step === 1 && upload && (
        <form onSubmit={(e) => void describe(e)}>
          <p className="note">{upload.filename} · {upload.pages} pages · {(upload.size / 1048576).toFixed(1)} MB
            {!upload.has_text && <span className="fail"> · this PDF has almost no text (a scan?), so there may be little to read</span>}</p>
          <div className="fields">
            <label className="field"><span>Utility name *</span>
              <input value={name} autoFocus maxLength={80} placeholder="e.g. Santee Cooper"
                onChange={(e) => { setName(e.target.value); if (!codeTouched) setCode(suggestCode(e.target.value)); }} /></label>
            <label className="field"><span>Short code * (unique, shown on exports)</span>
              <input value={code} maxLength={16} placeholder="e.g. SCPSA" onChange={(e) => { setCode(e.target.value.toUpperCase()); setCodeTouched(true); }} /></label>
            <label className="field"><span>Pages to read (optional)</span>
              <input value={pages} placeholder={`all ${upload.pages}, or e.g. 12-40`} onChange={(e) => setPages(e.target.value)} /></label>
            <div className="field wide"><span>States it covers *</span>
              <div className="chips">
                {states.map((s) => <span key={s} className="chip">{s}<button type="button" aria-label={`Remove ${s}`} onClick={() => setStates(states.filter((x) => x !== s))}>✕</button></span>)}
                <select value="" aria-label="Add a state" onChange={(e) => e.target.value && setStates([...states, e.target.value])}>
                  <option value="">Add a state…</option>
                  {(meta?.states ?? ["SC", "GA"]).filter((s) => !states.includes(s)).map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
            <div className="field wide"><span>Names on OpenStreetMap (operator)</span>
              <div className="chips">
                {ops.map((o) => <span key={o} className="chip">{o}<button type="button" aria-label={`Remove ${o}`} onClick={() => setOps(ops.filter((x) => x !== o))}>✕</button></span>)}
                <input value={opDraft} placeholder="Type a name, then Enter" onChange={(e) => setOpDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addOp(); } }} onBlur={addOp} />
              </div>
              <span className="note">How substations are tagged on OpenStreetMap. Include old or parent company names too: maps often
                keep them for years (Dominion&apos;s are still tagged SCE&amp;G).</span>
            </div>
          </div>
          <div className="row2">
            <button className="primary" type="submit" disabled={busy || !name.trim() || !code.trim() || !states.length}>{busy ? "Saving…" : "Next: read it"}</button>
          </div>
        </form>
      )}

      {step === 2 && source && (
        <div>
          <p className="sub">{source.display_name} ({source.code}). The AI reader finds the pages that list projects, then copies each
            project with the page and exact text behind every value. Code checks every snippet against its page.</p>
          {est?.estimate && (
            <table className="kv"><tbody>
              <tr><td>Model</td><td>{est.estimate.model}</td></tr>
              <tr><td>Pages</td><td>{est.estimate.pages}, in about {est.estimate.calls} requests</td></tr>
              <tr><td>Tokens (estimate)</td><td>{est.estimate.input_tokens.toLocaleString()} in, {est.estimate.output_tokens.toLocaleString()} out</td></tr>
              <tr><td>Cost (estimate)</td><td>{est.estimate.usd != null ? `$${est.estimate.usd.toFixed(4)}` : "price unknown (add it under prices in config/models.json)"}
                {est.estimate.limit_usd ? ` · limit $${est.estimate.limit_usd.toFixed(2)}` : ""}</td></tr>
            </tbody></table>
          )}
          {est && !est.allowed && <p className="err-inline">{est.message}</p>}
          {progress.msg && (
            <div className="progress" aria-live="polite">
              <p>{progress.msg}</p>
              {progress.located != null && <p className="note">{progress.found.length} projects found{progress.failed ? `, ${progress.failed} items it couldn't use` : ""}.</p>}
              {progress.found.length > 0 && <p className="note">Latest: {progress.found.slice(-3).join(" · ")}</p>}
            </div>
          )}
          <div className="row2">
            <button className="primary" onClick={() => void read()} disabled={busy || (est ? !est.allowed : true)}>
              {busy ? "Reading…" : progress.done ? "Read it again" : "Read the filing"}
            </button>
          </div>
        </div>
      )}

      {step === 3 && source && review && (
        <div>
          <ReviewTable sourceId={source.id} review={review} onChange={setReview} images={meta?.page_images ?? false} />
          <div className="row2">
            <button className="primary" onClick={() => setStep(4)} disabled={!review.ready}>Next: activate</button>
            {!review.ready && <span className="note">Accept or reject every row first{review.counts.accepted ? "" : ", and accept at least one"}.</span>}
            <button className="linkbtn" onClick={() => { setReview(null); setEst(null); setStep(2); }}
              title="Read the filing again, for example after changing the model. This replaces the review and its decisions.">
              Read it again</button>
          </div>
        </div>
      )}

      {step === 4 && source && review && (
        <div>
          <p className="sub">{accepted.length} accepted projects from {source.display_name}.
            {undated ? ` ${undated} have no in-service date, so they stay in the review and aren't compared.` : ""}
            {unplaced ? ` ${unplaced} have no endpoints and will be listed as not placed.` : ""}</p>
          <p className="note">Activating places them on the map (OpenStreetMap substations and towns, as for every source) and
            compares them with every other utility&apos;s projects. The last run&apos;s write-ups stay; run the pipeline again for
            write-ups that include {source.code}.</p>
          {placing && (
            <div className="progress" aria-live="polite">
              <p>{placing.msg}</p>
              <p className="note">{placing.placed} placed{placing.unlocated ? `, ${placing.unlocated} not placed` : ""} · {placing.pairs} pairs under 25 mi across all utilities</p>
            </div>
          )}
          <div className="row2">
            <button onClick={() => setStep(3)} disabled={busy}>Back to review</button>
            <button className="primary" onClick={() => void activate()} disabled={busy}>{busy ? "Activating…" : "Activate and show on the map"}</button>
          </div>
        </div>
      )}

      {error && <p className="err-inline" role="alert">{error}</p>}
    </div>
  );
}
