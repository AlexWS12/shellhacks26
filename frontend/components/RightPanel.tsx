"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { activeIn } from "@/lib/filters";
import { ACTOR_LABEL, CONF_LABEL, OWNER, TYPE_LABEL, fmtDate, money, plural, utilityName } from "@/lib/format";
import { run, useRev } from "@/lib/run";
import type { Endpoint, Overlap, PairDetail, Project } from "@/lib/types";
import { useUI } from "@/lib/ui";

import Gantt from "./Gantt";

const Chip = ({ a }: { a: string }) => <i className={`chipe ${a.startsWith("jev") ? "jev" : a}`}>{ACTOR_LABEL[a] ?? a}</i>;

export default function RightPanel() {
  useRev((s) => s.rev);
  const panel = useUI((s) => s.panel);
  if (panel.kind === "pair") return <PairView a={panel.a} b={panel.b} />;
  if (panel.kind === "project") return <ProjectView id={panel.id} />;
  if (panel.kind === "agent") return <AgentView id={panel.id} />;
  return <OpportunityList />;
}

function Back() {
  return (
    <button className="back" onClick={() => { useUI.getState().setPanel({ kind: "list" }); useUI.getState().flyTo({ kind: "border" }); }}>
      ← All opportunities
    </button>
  );
}

function useOverlaps(): Overlap[] {
  const results = useUI((s) => s.results);
  const year = useUI((s) => s.year);
  const list = run.phase === "done" && results ? results : run.overlaps;
  if (year == null) return list;
  return list.filter((o) => {
    const a = run.projects[o.project_a], b = run.projects[o.project_b];
    return a && b && activeIn(a, year) && activeIn(b, year);
  });
}

function open(o: Overlap) {
  useUI.getState().setPanel({ kind: "pair", a: o.project_a, b: o.project_b });
  useUI.getState().flyTo({ kind: "pair", a: o.project_a, b: o.project_b });
}

function OpportunityList() {
  const { filters, setFilters, visibleProjects, year } = useUI();
  const overlaps = useOverlaps();
  const total = Object.keys(run.projects).length;
  const onMap = visibleProjects ?? Object.values(run.projects).filter((p) => p.lat != null).length;
  const together = overlaps.filter((o) => o.windows_overlap).length;

  if (run.phase === "idle") {
    return (
      <div className="section">
        <p className="label">Coordination opportunities</p>
        <p className="empty">Run the pipeline to find Dominion and Georgia projects planned under 25 miles apart.</p>
      </div>
    );
  }
  return (
    <>
      <div className="kpis">
        <div><b>{total}</b>projects read</div>
        <div><b>{onMap}</b>on the map</div>
        <div><b>{overlaps.length}</b>{year ? `active in ${year}` : "under 25 mi"}</div>
      </div>
      <div className="toolbar">
        <span>{together} built at the same time</span>
        <span className="spacer" />
        {run.phase === "done" && (
          <div className="seg">
            <button aria-pressed={filters.sort === "distance"} onClick={() => setFilters({ sort: "distance" })}>Closest</button>
            <button aria-pressed={filters.sort === "gap"} onClick={() => setFilters({ sort: "gap" })}>Soonest</button>
          </div>
        )}
      </div>
      {overlaps.length === 0 && (
        <div className="section"><p className="empty">{run.phase === "running" ? "Reading filings…" : "Nothing matches these filters."}</p></div>
      )}
      <div role="list">
        {overlaps.map((o, i) => {
          const a = run.projects[o.project_a], b = run.projects[o.project_b];
          if (!a || !b) return null;
          return (
            <div key={o.id} role="listitem" tabIndex={0} className={`row ${run.phase === "running" ? "new" : ""}`}
              onClick={() => open(o)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(o))}>
              <span className="rk">{String(o.rank || i + 1).padStart(2, "0")}</span>
              <div>
                <div className="t"><span className="a">{a.name}</span><br /><span className="b">{b.name}</span></div>
                <div className="meta">
                  <span><span className="num">{o.distance_mi.toFixed(2)}</span> mi</span>
                  <span><span className="num">{o.time_gap_days.toLocaleString()}</span> days apart</span>
                  {o.windows_overlap === true && <span className="tag together">built at the same time</span>}
                  {o.windows_overlap == null && <span className="tag unknown">timing undecided</span>}
                  {!["verified", "confirmed_osm"].includes(o.pair_confidence) && <span className="tag approx">approx. location</span>}
                  {o.in_sponsor_sample && <span className="tag bench">benchmark</span>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {run.phase === "done" && (
        <div className="exports">
          <a href={api.exportUrl(filters, "xlsx")}>Export .xlsx</a>
          <a href={api.exportUrl(filters, "csv")}>Export .csv</a>
        </div>
      )}
    </>
  );
}

function insight(o: Overlap, timing: string): string {
  const years = o.time_gap_days / 365;
  if (timing === "concurrent") return `Both are under construction at the same time, ${o.distance_mi.toFixed(1)} miles apart. That's the strongest case for sharing crews, equipment, staging yards and outage planning.`;
  if (timing === "unknown") return `One start date isn't in the filings, so we can't tell yet whether crews overlap. Their in-service dates are ${o.time_gap_days} days apart.`;
  if (years < 2) return `Their in-service dates are ${o.time_gap_days} days apart. A small schedule shift could put both crews in the area at once.`;
  return `Their in-service dates are about ${Math.round(years)} years apart, so crews won't overlap. The value is shared information: surveys, right-of-way records, and designing the later project around the earlier one.`;
}

function PairView({ a, b }: { a: string; b: string }) {
  const id = `${a}|${b}`;
  const local = run.costs[id];
  const [fetched, setFetched] = useState<{ id: string; data: PairDetail } | null>(null);
  useEffect(() => {
    if (run.costs[id]) return; // already have it from events
    let alive = true;
    api.pair(a, b).then((data) => alive && setFetched({ id, data })).catch(() => undefined);
    return () => { alive = false; };
  }, [a, b, id]);
  const remote = fetched?.id === id ? fetched.data : null;

  // prefer this run's events; the API only fills gaps (e.g. after "Jump to results")
  const own = Boolean(local);
  const pa: Project | undefined = own ? run.projects[a] : remote?.a ?? run.projects[a];
  const pb: Project | undefined = own ? run.projects[b] : remote?.b ?? run.projects[b];
  const o: Overlap | undefined = [...run.overlaps, ...(useUI.getState().results ?? [])].find((x) => x.id === id) ?? remote?.overlap;
  if (!pa || !pb || !o) return <div className="detail"><Back /><p className="empty">Loading…</p></div>;
  const listed = (run.phase === "done" ? useUI.getState().results : null) ?? run.overlaps;
  const rank = listed.find((x) => x.id === id)?.rank || o.rank;
  const cost = own ? local : remote?.cost;
  const shared = own ? local?.shared : remote?.shared;
  const analysis = own ? run.analyses[id] : remote?.analysis ?? run.analyses[id];
  const brief = own ? run.briefs[id] : remote?.brief ?? run.briefs[id];

  return (
    <div className="detail">
      <Back />
      <p className="label">Opportunity {rank ? `#${rank}` : ""}{o.in_sponsor_sample && <span className="count c-accent">benchmark pair</span>}</p>
      <div className="pair">
        <div className="pj desc">
          <b>{pa.name}</b>Dominion Energy SC · {pa.status}<br />In service {fmtDate(pa.in_service_date)}
        </div>
        <div className="pj gpc">
          <b>{pb.name}</b>{OWNER[pb.sponsor] ?? pb.sponsor}<br />Needed by {fmtDate(pb.in_service_date)}
        </div>
      </div>
      <div className="hero"><span className="big">{o.distance_mi.toFixed(2)}</span><span>miles apart</span></div>
      <div className="sub">{o.time_gap_days.toLocaleString()} days between in-service dates · {CONF_LABEL[o.pair_confidence]}</div>

      <h3>Build windows</h3>
      <Gantt a={pa} b={pb} />
      <p className="note">Highlighted band = both under construction. A faded start means the work began before 2024.</p>

      <h3>What they could share</h3>
      {shared ? (
        <p className="share">
          <b className={shared.level === "high" ? "c-zone" : "c-ink"}>
            {shared.level === "high" ? "Strong" : shared.level === "medium" ? "Moderate" : "Limited"}
          </b>{" "}
          · {shared.items.join(", ")}
          <span className="sub block">
            {TYPE_LABEL[shared.types[0]] ?? shared.types[0]} + {TYPE_LABEL[shared.types[1]] ?? shared.types[1]}
            {shared.timing === "unknown" ? " · timing undecided" : `, ${shared.timing} build windows`}
          </span>
        </p>
      ) : <p className="empty">Waiting for the cost estimator.</p>}

      <h3>Analysis</h3>
      {analysis ? (
        <>
          <p className="written">{analysis.text}</p>
          <div className="by"><Chip a={analysis.actor} />{analysis.actor === "gemini" ? "Written by Gemini from the filing text" : "Template from the computed facts"}
            {analysis.unsupported_numbers?.length ? <span className="fail">check numbers: {analysis.unsupported_numbers.join(", ")}</span> : null}</div>
        </>
      ) : <p className="written">{insight(o, shared?.timing ?? "unknown")}</p>}

      {brief && (brief.dominion || brief.mediator) && (
        <>
          <h3>Meeting prep</h3>
          {brief.dominion && <div className="brief"><div className="who c-desc">Dominion advocate <Chip a={brief.dominion.actor} /></div>{brief.dominion.text}</div>}
          {brief.georgia && <div className="brief"><div className="who c-gpc">Georgia advocate <Chip a={brief.georgia.actor} /></div>{brief.georgia.text}</div>}
          {brief.mediator && <div className="brief"><div className="who c-ink">Mediator: joint agenda <Chip a={brief.mediator.actor} /></div>{brief.mediator.text}</div>}
        </>
      )}

      <h3>Cost</h3>
      {cost ? (
        <>
          <table className="kv"><tbody>
            <tr><td>Dominion project cost (public)</td><td>{money(cost.desc_cost)}</td></tr>
            {cost.desc_cost_per_mile && <tr><td>Dominion cost per mile ({cost.desc_miles} mi)</td><td>{money(cost.desc_cost_per_mile)}</td></tr>}
            <tr><td>Georgia project cost</td><td>redacted</td></tr>
            {cost.savings != null && <tr><td>Possible savings</td><td>{money(cost.savings)}</td></tr>}
          </tbody></table>
          <p className="note">{cost.statement}{cost.source && <> Source: {cost.source}</>}</p>
        </>
      ) : <p className="empty">Waiting for the cost estimator.</p>}

      <h3>From the filings</h3>
      <div className="quote">{pa.description}</div>
      <div className="quote">{pb.description}</div>

      <h3>Where this came from</h3>
      <div className="prov"><Provenance p={pa} /><Provenance p={pb} /></div>
    </div>
  );
}

function EndpointLine({ e }: { e: Endpoint }) {
  const ev = e.evidence as Record<string, string | number>;
  if (e.lat == null) return <>{e.name} (not located{ev.reason ? `: ${ev.reason}` : ""})</>;
  const how: Record<string, string> = {
    sponsor_file: "surveyed point", override: `verified by hand: ${ev.note ?? ""}`,
    overpass: `OpenStreetMap ${ev.osm_id ?? ""}, confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`,
    nominatim: `OpenStreetMap search ${ev.osm_id ?? ""} (${ev.kind ?? ""}), confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`,
    geonames_town: `town ${ev.town ?? ""}, confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`,
  };
  return <>{e.name} <span className="mono">({e.lat.toFixed(3)}, {e.lon!.toFixed(3)})</span> · {how[e.method] ?? e.method}</>;
}

function Provenance({ p }: { p: Project }) {
  return (
    <div>
      <b className={p.utility === "DESC" ? "c-desc" : "c-gpc"}>{p.name}</b><br />
      {p.source_file}, page {p.source_page} ({p.source_ref})
      {p.project_type && <> · {TYPE_LABEL[p.project_type] ?? p.project_type} <Chip a={p.project_type_actor ?? "code"} /></>}
      <br />{p.endpoints.length ? p.endpoints.map((e, i) => <span key={i}>{i > 0 && "; "}<EndpointLine e={e} /></span>) : "No endpoint names in the title"}
    </div>
  );
}

function ProjectView({ id }: { id: string }) {
  const p = run.projects[id];
  const overlaps = useOverlaps().filter((o) => o.project_a === id || o.project_b === id);
  if (!p) return <div className="detail"><Back /><p className="empty">Unknown project.</p></div>;
  return (
    <div className="detail">
      <Back />
      <p className="label">{utilityName(p)}</p>
      <h2>{p.name}</h2>
      <p className="sub">
        {p.utility === "DESC" ? "In service" : "Needed by"} {fmtDate(p.in_service_date)}
        {p.build_start && ` · starts ${fmtDate(p.build_start)}`} · {CONF_LABEL[p.location_confidence]}
        {p.cost_total != null && ` · ${money(p.cost_total)}`}
      </p>
      <div className="quote">{p.description}</div>
      {p.need_text && <div className="quote">Why: {p.need_text}</div>}
      <div className="prov"><Provenance p={p} /></div>
      <h3>Nearby work from the other utility</h3>
      {overlaps.length ? overlaps.map((o) => {
        const other = run.projects[o.project_a === id ? o.project_b : o.project_a];
        return (
          <div key={o.id} className="row compact" tabIndex={0} role="button" onClick={() => open(o)}>
            <span className="rk">{String(o.rank).padStart(2, "0")}</span>
            <div><div className="t">{other?.name}</div><div className="meta"><span><span className="num">{o.distance_mi.toFixed(2)}</span> mi</span><span>{plural(o.time_gap_days, "day")} apart</span></div></div>
          </div>
        );
      }) : <p className="empty">Nothing from the other utility within 25 miles. Most projects look like this.</p>}
    </div>
  );
}

function AgentView({ id }: { id: string }) {
  const a = run.agents[id];
  const entries = run.agentLog[id] ?? [];
  const think = run.thinking[id];
  const h = run.health[id];
  if (!a) return <div className="detail"><Back /><p className="empty">Unknown agent.</p></div>;
  return (
    <div className="detail">
      <Back />
      <p className="label">Agent · {a.engine}</p>
      <h2>{a.name}</h2>
      <p className="sub">{a.role}.</p>
      <table className="kv gap"><tbody>
        <tr><td>Status</td><td>{a.status}</td></tr>
        {a.summary && <tr><td>Result</td><td>{a.summary}</td></tr>}
        <tr><td>AI decisions</td><td>{a.judgments}</td></tr>
        {a.ms != null && <tr><td>Time</td><td>{(a.ms / 1000).toFixed(1)} s</td></tr>}
        {a.depends_on.length > 0 && <tr><td>Waits for</td><td>{a.depends_on.map((d) => run.agents[d]?.name ?? d).join(", ")}</td></tr>}
        {h && <tr><td>Watchdog ({ACTOR_LABEL[h.actor] ?? h.actor})</td><td>stuck {h.stuck.toFixed(2)} · progress {h.progress.toFixed(1)}/4</td></tr>}
      </tbody></table>
      {think && (<><h3>Writing</h3><div className="think">{think}</div></>)}
      <h3>Activity · latest {Math.min(entries.length, 60)} of {entries.length}</h3>
      {entries.length === 0 && <p className="empty">Nothing yet.</p>}
      {entries.slice(-60).reverse().map((e, i) => (
        <div className="entry" key={i}>
          {e.kind === "tool" && <><Chip a={e.actor} /> <code>{e.tool}</code> {e.summary ?? "…"}</>}
          {e.kind === "judgment" && <><Chip a={e.actor} /> {e.subject}: <b>{typeof e.answer === "number" ? e.answer.toFixed(2) : String(e.answer)}</b>
            <span> · {e.question}{e.cached ? " · cached" : e.ms ? ` · ${e.ms} ms` : ""}</span></>}
          {e.kind === "note" && <span>{e.text}</span>}
        </div>
      ))}
    </div>
  );
}
