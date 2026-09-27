"use client";

// The Sources menu: add another utility's plan (spreadsheet, PDF or link). Saved plans stay until removed and
// each one is read by its own Reader agent on every live run.

import { useEffect, useRef, useState } from "react";

import { api, type SubmissionMenu, type SubmissionPreview } from "@/lib/api";
import { run } from "@/lib/run";
import type { SubmissionView } from "@/lib/types";
import { refreshAgents } from "@/lib/ui";

type Kind = "spreadsheet" | "pdf" | "url";
const TABS: { kind: Kind; label: string; accept?: string }[] = [
  { kind: "spreadsheet", label: "Spreadsheet", accept: ".csv,.xlsx" },
  { kind: "pdf", label: "PDF filing", accept: ".pdf,application/pdf" },
  { kind: "url", label: "Link" },
];

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export default function SourcesMenu({ onClose }: { onClose: () => void }) {
  const [menu, setMenu] = useState<SubmissionMenu | null>(null);
  const [kind, setKind] = useState<Kind>("spreadsheet");
  const [owner, setOwner] = useState("");
  const [label, setLabel] = useState("");
  const [state, setState] = useState<"SC" | "GA">("SC");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [mapping, setMapping] = useState<{ sub: SubmissionView; preview: SubmissionPreview; cols: Record<string, string> } | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);
  const running = run.phase === "running";

  const load = () => api.submissions().then(setMenu).catch((e) => setError(String(e)));
  useEffect(() => {
    void load();
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const changed = (msg: string) => {
    setNote(msg);
    void load();
    void refreshAgents();
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setNote("");
    setBusy(true);
    try {
      const body = kind === "url"
        ? { owner, label: label || null, state, kind, url }
        : { owner, label: label || null, state, kind, filename: file?.name ?? null, content_b64: file ? await toBase64(file) : null };
      const r = await api.addSubmission(body);
      void load(); // saved either way: show it in Saved plans even if its columns are matched later
      if (r.preview && r.suggested) {
        setMapping({ sub: r.submission, preview: r.preview, cols: r.suggested });
      } else {
        changed(`Saved ${r.submission.owner}. Its Reader joins the next live run.`);
      }
      setFile(null);
      setUrl("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function editColumns(s: SubmissionView) {
    setError("");
    try {
      const r = await api.submissionPreview(s.id);
      setMapping({ sub: r.submission, preview: r.preview, cols: r.suggested });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function saveMapping() {
    if (!mapping) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.setMapping(mapping.sub.id, mapping.cols);
      setMapping(null);
      changed(`Saved ${r.submission.owner}: ${r.usable} of ${r.rows} rows usable.` +
        (r.skipped.length ? ` Skipped: ${r.skipped.slice(0, 3).join("; ")}${r.skipped.length > 3 ? "; ..." : ""}` : ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setError("");
    try {
      await api.removeSubmission(id);
      setConfirmId(null);
      changed("Removed. It won't be read on the next run.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const tab = TABS.find((t) => t.kind === kind)!;
  const ready = owner.trim() && (kind === "url" ? url.trim() : file);

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="sources-title">
        <div className="modal-head">
          <h2 id="sources-title">Add another utility&apos;s plan</h2>
          <button className="linkbtn" onClick={onClose} aria-label="Close">Close</button>
        </div>
        <p className="sub">
          Dominion&apos;s and Georgia&apos;s plans are built in. Each plan you add gets its own Reader, and its projects are
          compared with every other owner&apos;s, pair by pair, on every live run until you remove it.
        </p>

        {mapping ? (
          <div className="mapping">
            <h3>Match the columns · {mapping.sub.filename}</h3>
            <p className="note">Project name and in-service date are required. Rows missing either are skipped and reported.</p>
            <div className="fields">
              {Object.entries(menu?.fields ?? {}).map(([field, f]) => (
                <label key={field} className="field">
                  <span>{f.label}{f.required ? " *" : ""}</span>
                  <select value={mapping.cols[field] ?? ""}
                    onChange={(e) => setMapping({ ...mapping, cols: { ...mapping.cols, [field]: e.target.value } })}>
                    <option value="">{f.required ? "Choose a column" : "Not in this file"}</option>
                    {mapping.preview.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
              ))}
            </div>
            <div className="preview">
              <table>
                <thead><tr>{mapping.preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{mapping.preview.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody>
              </table>
              <p className="note">First {mapping.preview.rows.length} of {mapping.preview.total_rows} rows.</p>
            </div>
            <div className="row2">
              <button className="primary" onClick={() => void saveMapping()} disabled={busy}>Save plan</button>
              <button onClick={() => setMapping(null)} disabled={busy}>Later</button>
            </div>
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)}>
            <div className="seg tabs" role="group" aria-label="Plan type">
              {TABS.map((t) => (
                <button key={t.kind} type="button" aria-pressed={kind === t.kind} onClick={() => { setKind(t.kind); setFile(null); }}>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="fields">
              <label className="field"><span>Utility *</span>
                <input ref={first} value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="e.g. Santee Cooper" maxLength={80} /></label>
              <label className="field"><span>Short name for its Reader</span>
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="defaults to the utility" maxLength={40} /></label>
              <label className="field"><span>State (for rows that don&apos;t say)</span>
                <select value={state} onChange={(e) => setState(e.target.value as "SC" | "GA")}>
                  <option value="SC">South Carolina</option><option value="GA">Georgia</option>
                </select></label>
              {kind === "url" ? (
                <label className="field wide"><span>Link to a web page, PDF or spreadsheet *</span>
                  <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></label>
              ) : (
                <label className="field wide"><span>File * ({tab.accept?.split(",")[0] === ".csv" ? "CSV or XLSX" : "PDF"}, up to {menu?.limits.max_mb ?? 20} MB)</span>
                  <input key={kind} type="file" accept={tab.accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
              )}
            </div>
            {kind !== "spreadsheet" && (
              <p className={`note ${menu && !menu.gemini ? "fail" : ""}`}>
                Read by Gemini page by page; it may only copy what the page says, and every project keeps its page number.
                {menu && !menu.gemini ? " Gemini is not configured, so this plan will be saved but not read until it is." : ""}
              </p>
            )}
            <div className="row2">
              <button className="primary" type="submit" disabled={!ready || busy}>{busy ? "Saving…" : kind === "spreadsheet" ? "Upload and match columns" : "Save plan"}</button>
            </div>
          </form>
        )}

        {error && <p className="err-inline" role="alert">{error}</p>}
        {note && <p className="ok-inline">{note}{running ? " (a run is in progress; it joins the next one)" : ""}</p>}

        <h3>Saved plans</h3>
        {menu && menu.submissions.length === 0 && <p className="empty">None yet. Dominion and Georgia are always included.</p>}
        {menu?.submissions.map((s) => (
          <div key={s.id} className="plan">
            <div>
              <b>{s.owner}</b> <span className="sub-inline">· Reader · {s.label} · {s.kind} · {s.url ?? s.filename}</span>
              <div className={s.status === "ready" ? "good" : "fail"}>{s.status === "ready" ? "Ready: read on every live run" : "Needs its columns matched"}</div>
            </div>
            <div className="plan-actions">
              {s.kind === "spreadsheet" && <button onClick={() => void editColumns(s)}>Columns</button>}
              {confirmId === s.id
                ? <button className="danger" onClick={() => void remove(s.id)}>Confirm remove</button>
                : <button onClick={() => setConfirmId(s.id)}>Remove</button>}
            </div>
          </div>
        ))}
        {menu && <p className="note">Up to {menu.limits.max_plans} plans. Replays show the plans that were saved when they were recorded.</p>}
      </div>
    </div>
  );
}
