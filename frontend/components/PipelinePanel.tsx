"use client";

import { useState } from "react";

import { PLAIN, providerName } from "@/lib/alerts";
import { run, useRev } from "@/lib/run";
import type { ModelIssue, RoleInfo } from "@/lib/types";
import { useUI } from "@/lib/ui";

import AgentGraph from "./AgentGraph";
import SourcesMenu from "./SourcesMenu";

// Model failures and fallbacks, same kind together with a count. A replay shows them here instead of notifying.
function issueRows(list: ModelIssue[], roles: RoleInfo): { key: string; text: string; level: string; n: number }[] {
  const label = (id: string) => roles[id]?.label ?? id;
  const short = (ref?: string) => (ref ? ref.slice(ref.indexOf("/") + 1) : "");
  const rows = new Map<string, { key: string; text: string; level: string; n: number }>();
  for (const i of list) {
    const [key, text, level] = i.type === "model.call_failed"
      ? [`f/${i.role}/${i.model}/${i.errorClass}`, `${label(i.role)}: ${providerName(i.provider)} ${i.model}: ${PLAIN[i.errorClass ?? ""] ?? i.errorClass}`,
        ["AuthError", "ModelNotFound", "QuotaExceeded"].includes(i.errorClass ?? "") ? "error" : "warn"]
      : i.type === "model.fallback_used"
        ? [`b/${i.role}/${i.to}`, `${label(i.role)}: switched from ${short(i.from)} to backup ${short(i.to)}`, "info"]
        : [`x/${i.role}`, `${label(i.role)}: no model worked for one item`, "error"];
    const row = rows.get(key);
    if (row) row.n += 1;
    else rows.set(key, { key, text, level, n: 1 });
  }
  return [...rows.values()];
}

export default function PipelinePanel() {
  useRev((s) => s.rev);
  const setPanel = useUI((s) => s.setPanel);
  const visibleProjects = useUI((s) => s.visibleProjects);
  const results = useUI((s) => s.results);
  const [allChecks, setAllChecks] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const unl = Object.keys(run.unlocated);
  const passed = run.reference.filter((r) => r.passed).length;
  const blind = run.reference.filter((r) => r.blind_passed).length;
  const hasBlind = run.reference.some((r) => r.blind_passed != null);
  const jev = run.judges["jev"];
  const gem = run.judges["gemini"];
  const other = Object.entries(run.judges).filter(([k]) => k !== "jev" && k !== "gemini").reduce((n, [, t]) => n + t.n, 0);
  const total = Object.keys(run.projects).length;
  const onMap = visibleProjects ?? Object.values(run.projects).filter((p) => p.lat != null).length;
  const pairs = (run.phase === "done" && results ? results : run.overlaps).length;
  const checks = allChecks ? run.checks : run.checks.slice(0, 5);
  const roles = useUI((st) => st.health?.models) ?? {};
  const [allIssues, setAllIssues] = useState(false);
  const issues = issueRows(run.modelIssues, roles);

  return (
    <>
      {run.phase !== "idle" && (
        <div className="kpis">
          <div><b>{total}</b>projects read</div>
          <div><b>{onMap}</b>on the map</div>
          <div><b>{pairs}</b>under 25 mi</div>
        </div>
      )}
      <div className="section">
        <p className="label">Sources
          <button className="linkbtn addplan" onClick={() => setMenuOpen(true)}
            title="Every utility the pipeline reads; add a filing, spreadsheet or link">Manage · + Add</button>
        </p>
        {menuOpen && <SourcesMenu onClose={() => setMenuOpen(false)} />}
        {run.sourceOrder.length === 0 && <p className="empty">Dominion&apos;s project list, Georgia&apos;s IRP Vol. 3, and a surveyed benchmark set.</p>}
        {run.sourceOrder.map((id) => {
          const s = run.sources[id];
          return (
            <div className="src" key={id}>
              <b>{s.label}</b>
              <span className="num">{s.read}/{s.total}</span>
              <div className="bar"><i style={{ transform: `scaleX(${Math.min(1, s.read / s.total)})` }} /></div>
            </div>
          );
        })}
        <div className="ticker" aria-live="polite">{run.ticker}</div>
      </div>

      <div className="section">
        <p className="label">Agents <span className="hint">click to inspect</span></p>
        <AgentGraph onSelect={(id) => setPanel({ kind: "agent", id })} />
        {Object.values(run.agents).some((a) => a.team === "research") && (
          <>
            <p className="label team-label">Research team <span className="hint">other utilities near the river</span></p>
            <AgentGraph team="research" onSelect={(id) => setPanel({ kind: "agent", id })} />
          </>
        )}
        {(jev || gem || other > 0) && (
          <div className="decisions">
            {jev && <div><b>{jev.n}</b>Jev calls{jev.n > jev.cached ? ` · ${Math.round(jev.ms / Math.max(1, jev.n - jev.cached))}ms` : ""}</div>}
            {gem && <div><b>{gem.n}</b>Gemini calls</div>}
            {other > 0 && <div><b>{other}</b>rule-based</div>}
          </div>
        )}
      </div>

      <div className="section">
        <p className="label">Accuracy benchmark</p>
        {run.reference.length === 0 ? (
          <p className="empty">Scores the results against 6 known overlaps once the overlap engine finishes.</p>
        ) : (
          <>
            <div className="bench">
              <span className={`big ${passed === run.reference.length ? "" : "fail"}`}>{passed}/{run.reference.length}</span>
              <span>exact on distance and days</span>
            </div>
            {hasBlind && (
              <div className="bench">
                <span className={`big ${blind === run.reference.length ? "" : "fail"}`}>{blind}/{run.reference.length}</span>
                <span>within 1 mi using our own geocoding, without the file&apos;s coordinates</span>
              </div>
            )}
            <table className="mini"><tbody>
              {run.reference.map((r) => (
                <tr key={r.overlap_id}><td>{r.overlap_id}</td><td>{r.got_mi ?? "?"} mi · {r.got_days ?? "?"} d</td><td className={r.passed ? "" : "fail"}>{r.passed ? "✓" : "✗"}</td>
                  {hasBlind && <td className={r.blind_passed ? "" : "fail"} title="Our own geocoding">ours {r.blind_mi ?? "?"} mi</td>}</tr>
              ))}
            </tbody></table>
          </>
        )}
      </div>

      <div className="section">
        <p className="label">Data issues found <span className="count">{run.checks.length || ""}</span></p>
        {run.checks.length === 0 && <p className="empty">Problems the validator finds in the filings show up here.</p>}
        {checks.map((c) => (
          <div key={c.id} className={`check ${c.level}`} role="button" tabIndex={0} aria-expanded={open === c.id}
            onClick={() => setOpen(open === c.id ? null : c.id)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setOpen(open === c.id ? null : c.id))}>
            <i className="dot" />
            <div>
              {c.title}
              {open === c.id && <div className="d">{c.detail}<div className="s">{c.source}</div></div>}
            </div>
          </div>
        ))}
        {run.checks.length > 5 && <button className="linkbtn" onClick={() => setAllChecks(!allChecks)}>{allChecks ? "Show less" : `Show all ${run.checks.length}`}</button>}
        {unl.length > 0 && <p className="note">{unl.length} projects have no location yet and stay off the map.</p>}
      </div>

      {issues.length > 0 && (
        <div className="section">
          <p className="label">Model problems <span className="count">{issues.length}</span>
            {run.mode === "replay" && <span className="hint">recorded in this run</span>}</p>
          {(allIssues ? issues : issues.slice(0, 6)).map((r) => (
            <div key={r.key} className={`check issue ${r.level}`}>
              <i className="dot" />
              <div>{r.text}{r.n > 1 && <span className="sub-inline"> ×{r.n}</span>}</div>
            </div>
          ))}
          {issues.length > 6 && <button className="linkbtn" onClick={() => setAllIssues(!allIssues)}>{allIssues ? "Show less" : `Show all ${issues.length}`}</button>}
        </div>
      )}

      <div className="section">
        <p className="label">Activity</p>
        <div className="log" aria-live="polite">
          {run.log.length === 0 ? <div>Waiting to run.</div> : run.log.map((l, i) => <div key={i}>{l.text}</div>)}
        </div>
      </div>
    </>
  );
}
