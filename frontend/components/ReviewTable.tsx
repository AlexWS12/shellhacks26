"use client";

// Review of what the AI reader found. Each row is accepted or rejected; clicking one shows every field with the page
// it came from and the exact text, next to a picture of that page with the text highlighted. A person can correct a
// field; the edit is kept with the value it replaced (human_override).

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { fmtDate, money } from "@/lib/format";
import type { Cite, Project, Review } from "@/lib/types";

const FLAG: Record<string, string> = {
  in_service_date: "No in-service date: can't be compared",
  endpoints: "No endpoints: can't be placed on the map",
};

type Field = { key: string; label: string; value: (p: Project) => string; cites: (p: Project) => Cite[]; edit?: string };
const prov = (p: Project) => (p.provenance ?? {}) as Record<string, unknown>;
const one = (p: Project, k: string): Cite[] => {
  const c = prov(p)[k];
  return c && typeof c === "object" && !Array.isArray(c) ? [c as Cite] : [];
};
const many = (p: Project, k: string): Cite[] => (Array.isArray(prov(p)[k]) ? (prov(p)[k] as Cite[]) : []);

const FIELDS: Field[] = [
  { key: "project_id", label: "Project ID", value: (p) => p.source_ref.split("ID ")[1] ?? "", cites: (p) => one(p, "project_id") },
  { key: "name", label: "Name", value: (p) => p.name, cites: (p) => one(p, "name"), edit: "name" },
  { key: "description", label: "Description", value: (p) => p.description, cites: (p) => one(p, "description"), edit: "description" },
  { key: "status", label: "Status", value: (p) => p.status, cites: (p) => one(p, "status"), edit: "status" },
  { key: "in_service_date", label: "In service", edit: "in_service_date", cites: (p) => one(p, "in_service_date"),
    value: (p) => (p.in_service_date ? `${fmtDate(p.in_service_date)}${p.in_service_raw ? ` (written “${p.in_service_raw}”)` : ""}` : "") },
  { key: "start_date", label: "Construction start", value: (p) => (p.build_start ? fmtDate(p.build_start) : ""), cites: (p) => one(p, "start_date"), edit: "start_date" },
  { key: "cost_total", label: "Total cost", value: (p) => (p.cost_total != null ? money(p.cost_total) : ""), edit: "cost_total",
    cites: (p) => [...many(p, "costs").filter((c) => /total/i.test(c.label ?? "")), ...one(p, "cost_total")] },
  { key: "costs", label: "Cost by year", cites: (p) => many(p, "costs").filter((c) => !/total/i.test(c.label ?? "")),
    value: (p) => Object.entries(p.cost_by_year ?? {}).map(([y, v]) => `${y === "prev" ? "Previous" : y}: ${v == null ? "?" : money(v)}`).join(" · ") },
  { key: "voltage_kv", label: "Voltage", value: (p) => (prov(p).voltage_kv_parsed ? `${prov(p).voltage_kv_parsed} kV` : ""),
    cites: (p) => one(p, "voltage_kv"), edit: "voltage_kv" },
  { key: "owner", label: "Owner (as the page says)", value: (p) => one(p, "owner")[0]?.snippet ?? "", cites: (p) => one(p, "owner") },
  { key: "endpoints", label: "Endpoints", value: (p) => p.endpoints.map((e) => e.name).join(" – "), edit: "endpoints",
    cites: (p) => [...many(p, "endpoints"), ...one(p, "endpoints")] },
];

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

function Highlight({ text, snippet }: { text: string; snippet?: string }) {
  const t = norm(text);
  const s = snippet ? norm(snippet) : "";
  const i = s ? t.indexOf(s) : -1;
  if (i < 0) return <p className="pagetext">{t}</p>;
  return <p className="pagetext">{t.slice(0, i)}<mark>{s}</mark>{t.slice(i + s.length)}</p>;
}

export default function ReviewTable({ sourceId, review, onChange, images }: {
  sourceId: string;
  review: Review;
  onChange: (r: Review) => void;
  images: boolean;
}) {
  const [open, setOpen] = useState<string | null>(review.projects[0]?.id ?? null);
  const [cite, setCite] = useState<Cite | null>(null);
  const [img, setImg] = useState<{ page: number; url: string } | null>(null);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<{ field: string; value: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const p = review.projects.find((x) => x.id === open) ?? null;
  const page = cite?.page ?? p?.source_page ?? null;

  useEffect(() => { // the page behind the selected field
    if (!page) return;
    let gone = false;
    if (images) void api.admin.pageImage(sourceId, page).then((url) => !gone && setImg({ page, url })).catch(() => undefined);
    void api.admin.pageText(sourceId, page).then((r) => !gone && setText(r.text)).catch(() => setText(""));
    return () => { gone = true; };
  }, [sourceId, page, images]);
  useEffect(() => () => { if (img) URL.revokeObjectURL(img.url); }, [img]);

  const act = async (fn: () => Promise<Review>) => {
    setBusy(true);
    setError("");
    try {
      onChange(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const decide = (pid: string, status: "accepted" | "rejected" | "pending") => act(() => api.admin.decide(sourceId, status, pid));
  const save = () => editing && p && act(async () => {
    const r = await api.admin.edit(sourceId, p.id, editing.field, editing.value);
    setEditing(null);
    return r;
  });

  const rows = useMemo(() => review.projects, [review]);
  const { counts } = review;

  return (
    <div className="review">
      <div className="row2 review-bar">
        <span className="note">{rows.length} found · {counts.accepted} accepted · {counts.rejected} rejected · {counts.pending} to decide</span>
        <button onClick={() => void act(() => api.admin.decide(sourceId, "accepted"))} disabled={busy || counts.pending === 0}>
          Accept all {counts.pending ? `${counts.pending} remaining` : ""}
        </button>
      </div>
      <div className="preview review-table">
        <table>
          <thead><tr><th>Project</th><th>In service</th><th>Cost</th><th>Page</th><th>Checked</th><th>Decision</th></tr></thead>
          <tbody>
            {rows.map((x) => {
              const r = review.review[x.id];
              return (
                <tr key={x.id} className={`${open === x.id ? "sel" : ""} ${r.status}`} onClick={() => { setOpen(x.id); setCite(null); setEditing(null); }}
                  tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(x.id)} aria-selected={open === x.id}>
                  <td>
                    <b>{x.name}</b>{r.edited && <span className="tag">edited</span>}
                    {r.incomplete.map((f) => <div key={f} className="flag">{FLAG[f] ?? f}</div>)}
                    {r.other_owner && <div className="flag">The page says {r.other_owner} owns it: rejected unless you accept it</div>}
                  </td>
                  <td>{x.in_service_date ? fmtDate(x.in_service_date) : <span className="fail">missing</span>}</td>
                  <td>{x.cost_total != null ? money(x.cost_total) : ""}</td>
                  <td>p.{x.source_page}</td>
                  <td title="Share of the fields the model filled in that matched their page">{Math.round((review.confidence[x.id] ?? 0) * 100)}%</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <span className="seg">
                      <button aria-pressed={r.status === "accepted"} onClick={() => void decide(x.id, r.status === "accepted" ? "pending" : "accepted")} disabled={busy}>Accept</button>
                      <button aria-pressed={r.status === "rejected"} onClick={() => void decide(x.id, r.status === "rejected" ? "pending" : "rejected")} disabled={busy}>Reject</button>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <p className="err-inline" role="alert">{error}</p>}

      {p && (
        <div className="review-detail">
          <div className="fieldlist">
            <h3>Where each value came from</h3>
            {FIELDS.map((f) => {
              const cites = f.cites(p);
              const value = f.value(p);
              const human = cites.find((c) => c.human_override)?.human_override;
              return (
                <div key={f.key} className={`fieldrow ${cites.some((c) => c === cite) ? "sel" : ""}`}>
                  <div className="fhead">
                    <span className="flabel">{f.label}</span>
                    {f.edit && !editing && <button className="linkbtn" onClick={() => setEditing({ field: f.edit!, value: value.replace(/ \(written.*$/, "") })}>Edit</button>}
                  </div>
                  {editing && f.edit && editing.field === f.edit ? (
                    <div className="row2">
                      <input autoFocus value={editing.value} onChange={(e) => { const value = e.target.value; setEditing((ed) => ed && { ...ed, value }); }}
                        onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setEditing(null); }}
                        aria-label={`New ${f.label.toLowerCase()}`} />
                      <button className="primary" onClick={() => void save()} disabled={busy}>Save</button>
                      <button onClick={() => setEditing(null)}>Cancel</button>
                    </div>
                  ) : (
                    <div className={value ? "fvalue" : "fvalue empty"}>{value || "Not stated in the filing"}</div>
                  )}
                  {cites.filter((c) => c.snippet).map((c, i) => (
                    <button key={i} className="cite" onClick={() => setCite(c)} title="Show this on the page">
                      <span className="pg">p.{c.page}{c.label ? ` · ${c.label}` : ""}</span> “{c.snippet}”
                    </button>
                  ))}
                  {human && <div className="note">Edited by a person (was {JSON.stringify(human.previous) ?? "empty"}).</div>}
                  {f.key === "endpoints" && prov(p).endpoints_from === "split from the name by code" && value &&
                    <div className="note">Split from the project name by code; the filing doesn&apos;t state them.</div>}
                </div>
              );
            })}
          </div>
          <div className="pageview">
            <h3>Page {page}</h3>
            {img?.page === page
              // eslint-disable-next-line @next/next/no-img-element -- a blob URL of a page picture, not a static asset
              ? <img src={img.url} alt={`Page ${page} of the filing`} className="thumb" />
              : images ? <p className="empty">Loading the page…</p> : null}
            {text && <Highlight text={text} snippet={cite?.snippet} />}
          </div>
        </div>
      )}
    </div>
  );
}
