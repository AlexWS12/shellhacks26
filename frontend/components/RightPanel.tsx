"use client";

import { useEffect, useRef, useState } from "react";

import { api, type FilterState } from "@/lib/api";
import { activeIn, lineCheck } from "@/lib/filters";
import {
  ACTOR_LABEL, CATEGORY_LABEL, CONF_LABEL, OWNER, TYPE_LABEL, fmtDate, gapLabel, money, plural, shortName, statedDate, utilityName,
} from "@/lib/format";
import { run, useRev } from "@/lib/run";
import type { Endpoint, Overlap, PairDetail, Project, ThirdParty } from "@/lib/types";
import { useUI } from "@/lib/ui";

import Gantt from "./Gantt";

const Chip = ({ a }: { a: string }) => <i className={`chipe ${a.startsWith("jev") ? "jev" : a}`}>{ACTOR_LABEL[a] ?? a}</i>;

export default function RightPanel() {
  useRev((s) => s.rev);
  const panel = useUI((s) => s.panel);
  if (panel.kind === "pair") return <PairView a={panel.a} b={panel.b} />;
  if (panel.kind === "project") return <ProjectView id={panel.id} />;
  if (panel.kind === "agent") return <AgentView id={panel.id} />;
  if (panel.kind === "research") return <ResearchView id={panel.id} />;
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

// Other utilities near each opportunity: this run's events while running, the API (current filters) after.
function useOthers(): ThirdParty[] {
  const others = useUI((s) => s.others);
  return run.phase === "done" && others ? others : run.thirdParty;
}

function byOverlap(links: ThirdParty[]): Record<string, ThirdParty[]> {
  const out: Record<string, ThirdParty[]> = {};
  for (const t of links) (out[t.overlap_id] ??= []).push(t);
  return out;
}

const days = (n: number | null, approx: boolean) => (n == null ? "date unknown" : `${approx ? "≈ " : ""}${n.toLocaleString()} d`);

function open(o: Overlap) {
  useUI.getState().setPanel({ kind: "pair", a: o.project_a, b: o.project_b });
  useUI.getState().flyTo({ kind: "pair", a: o.project_a, b: o.project_b });
}

type ChipId = FilterState["chip"];
type SortId = FilterState["sort"];

const CHIPS: { id: ChipId; label: string; test: (o: Overlap) => boolean }[] = [
  { id: "all", label: "All", test: () => true },
  { id: "same", label: "Same time", test: (o) => o.windows_overlap === true },
  { id: "bench", label: "Benchmark", test: (o) => o.in_sponsor_sample },
  { id: "verified", label: "Verified location", test: (o) => o.pair_confidence === "verified" },
];

const SORTS: { id: SortId; label: string; cmp: (x: Overlap, y: Overlap) => number }[] = [
  { id: "distance", label: "Closest", cmp: (x, y) => x.distance_mi - y.distance_mi },
  { id: "gap", label: "Soonest", cmp: (x, y) => x.time_gap_days - y.time_gap_days || x.distance_mi - y.distance_mi },
  { id: "strength", label: "Strongest", cmp: (x, y) => Number(y.windows_overlap === true) - Number(x.windows_overlap === true) || x.distance_mi - y.distance_mi },
];

function matches(o: Overlap, q: string): boolean {
  if (!q) return true;
  return [run.projects[o.project_a], run.projects[o.project_b]].some((p) => p &&
    [p.id, p.name, ...p.endpoints.map((e) => e.name)].some((t) => t.toLowerCase().includes(q)));
}

function Action({ o }: { o: Overlap }) {
  if (o.windows_overlap === true) return <span className="act crews">Share crews</span>;
  if (o.windows_overlap == null) return <span className="act unknown">Timing undecided</span>;
  return <span className="act records">Share records</span>;
}

function ExportMenu() {
  const filters = useUI((s) => s.filters);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button className="export" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>Export ▾</button>
      {open && (
        <div className="menu-list" role="menu">
          <a role="menuitem" href={api.exportUrl(filters, "xlsx")} onClick={() => setOpen(false)}>.xlsx</a>
          <a role="menuitem" href={api.exportUrl(filters, "csv")} onClick={() => setOpen(false)}>.csv</a>
        </div>
      )}
    </div>
  );
}

function OpportunityList() {
  const { filters, setFilters } = useUI();
  const overlaps = useOverlaps();
  const near = byOverlap(useOthers());

  if (run.phase === "idle") {
    return (
      <div className="section">
        <p className="label">Coordination opportunities</p>
        <p className="empty">Run the pipeline to find Dominion and Georgia projects planned under 25 miles apart.</p>
      </div>
    );
  }
  const done = run.phase === "done";
  const q = filters.q.trim().toLowerCase();
  const chip = CHIPS.find((c) => c.id === filters.chip) ?? CHIPS[0];
  const sort = SORTS.find((x) => x.id === filters.sort) ?? SORTS[0];
  const shown = overlaps.filter((o) => chip.test(o) && matches(o, q));
  // active pairs stay on top whatever the sort, same as the server's ranking
  if (done) shown.sort((x, y) => Number(Boolean(x.finished)) - Number(Boolean(y.finished)) || sort.cmp(x, y) || x.rank - y.rank);

  return (
    <div className="opps">
      <div className="opps-head">
        <div className="opps-title">
          <h2>Opportunities</h2>
          <span className="count">{overlaps.length}</span>
          <span className="spacer" />
          {done && <ExportMenu />}
        </div>
        <label className="search">
          <span className="ring" aria-hidden="true" />
          <input type="search" value={filters.q} onChange={(e) => setFilters({ q: e.target.value })}
            placeholder="Search project, substation or ID" aria-label="Search opportunities" />
        </label>
        <div className="chips">
          {CHIPS.map((c) => (
            <button key={c.id} className="chip" aria-pressed={filters.chip === c.id} onClick={() => setFilters({ chip: c.id })}>
              {c.label}<span className="n">{overlaps.filter(c.test).length}</span>
            </button>
          ))}
        </div>
        <div className="opps-sort">
          <span className="nowrap">{shown.length} shown</span>
          <span className="spacer" />
          {done && (
            <>
              <span>Sort</span>
              <div className="seg">
                {SORTS.map((x) => (
                  <button key={x.id} aria-pressed={filters.sort === x.id} onClick={() => setFilters({ sort: x.id })}>{x.label}</button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      <div className="key">
        <span><i className="mk desc" />Dominion</span>
        <span><i className="mk gpc" />Georgia</span>
        <span className="note-r">same shapes on the map</span>
      </div>
      <div className="col opps-list" role="list">
        {shown.length === 0 && (
          <p className="empty pad">{run.phase === "running" && overlaps.length === 0 ? "Reading filings…" : "Nothing matches these filters."}</p>
        )}
        {shown.map((o) => {
          const a = run.projects[o.project_a], b = run.projects[o.project_b];
          if (!a || !b) return null;
          return (
            <div key={o.id} role="listitem" tabIndex={0} className={`opp ${run.phase === "running" ? "new" : ""}`}
              title={`${OWNER.DESC}: ${a.name}\n${OWNER[b.sponsor] ?? b.sponsor}: ${b.name}`}
              onClick={() => open(o)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(o))}>
              <div className="names">
                <div><i className="mk desc" aria-label="Dominion" role="img" /><span>{shortName(a)}</span></div>
                <div><i className="mk gpc" aria-label="Georgia" role="img" /><span>{shortName(b)}</span></div>
              </div>
              <div className="dist"><b>{o.distance_mi.toFixed(2)}</b>miles</div>
              <div className="meta">
                <Action o={o} />
                <span>{gapLabel(o.time_gap_days)} apart</span>
                {o.finished && <span>· one already in service</span>}
                {slackNote(o) && <span title={slackNote(o)!}>· ±{o.distance_slack_mi} mi</span>}
                {o.in_sponsor_sample && <span className="c-accent">· benchmark</span>}
                {near[o.id] && (
                  <span className={`tag others cat-${near[o.id][0].category}`}>
                    +{near[o.id].length} other {near[o.id].length === 1 ? "owner" : "owners"} nearby
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Worth a note once an unlocated end could move the distance by more than a few miles.
const SLACK_NOTE_MI = 5;

function slackNote(o: Overlap): string | null {
  const s = o.distance_slack_mi ?? 0;
  if (s <= SLACK_NOTE_MI) return null;
  const firm = o.distance_mi + s < 25;
  return `One end of a project isn't located, so the distance could be off by up to ${s} mi` +
    (firm ? "; still under 25 mi either way." : "; the pair may not really be under 25 mi.");
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
  const allOthers = useOthers();
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
      {o.finished && <p className="note">At least one of these projects is already in service, so only records and designs can be shared, not crews.</p>}
      {slackNote(o) && <p className="note">{slackNote(o)}</p>}

      <h3>Build windows</h3>
      <Gantt a={pa} b={pb} />
      <p className="note">Highlighted band = both under construction. A faded start means the work began before 2024.</p>

      <OthersNearby links={allOthers.some((t) => t.overlap_id === id)
        ? allOthers.filter((t) => t.overlap_id === id) : remote?.others ?? []} />

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
    sponsor_file: `surveyed point from the benchmark file${ev.ref_id ? ` (${ev.ref_id})` : ""}`, override: `verified by hand: ${ev.note ?? ""}`,
    overpass: `OpenStreetMap ${ev.osm_id ?? ""}, ` + (ev.accepted_by === "exact_name_rule"
      ? `the only substation with this exact name in the state (${ACTOR_LABEL[String(ev.judge)] ?? ev.judge} p=${ev.p_match}, accepted by rule)`
      : `confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`)
      + (ev.neighbor_utility ? ` · ${ev.neighbor_utility} territory, as the title says` : ""),
    nominatim: `OpenStreetMap search ${ev.osm_id ?? ""} (${ev.kind ?? ""}), confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`,
    geonames_town: `town ${ev.town ?? ""}, confirmed by ${ACTOR_LABEL[String(ev.judge)] ?? ev.judge}`,
    county_centroid: `center of ${ev.county ?? "the county"} (approximate)`,
    source: "coordinates printed in the source",
  };
  const near = e.role === "context" ? "near " : "";
  const from = e.role === "context" ? " · named in the description, not a line end" : "";
  return <>{near}{e.name} <span className="mono">({e.lat.toFixed(3)}, {e.lon!.toFixed(3)})</span> · {how[e.method] ?? e.method}{from}</>;
}

function Provenance({ p }: { p: Project }) {
  const line = lineCheck(p);
  return (
    <div>
      <b className={p.utility === "DESC" ? "c-desc" : "c-gpc"}>{p.name}</b><br />
      {p.source_file}, page {p.source_page} ({p.source_ref})
      {p.project_type && <> · {TYPE_LABEL[p.project_type] ?? p.project_type} <Chip a={p.project_type_actor ?? "code"} /></>}
      <br />{p.endpoints.length ? p.endpoints.map((e, i) => <span key={i}>{i > 0 && "; "}<EndpointLine e={e} /></span>) : "No endpoint names in the title"}
      {line.why && <><br />Line not drawn: {line.why}.</>}
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
          <div key={o.id} className="row compact" tabIndex={0} role="button" onClick={() => open(o)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(o))}>
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

function OthersNearby({ links }: { links: ThirdParty[] }) {
  const selected = run.researchSelected;
  return (
    <>
      <h3>Other utilities nearby</h3>
      {links.length === 0 && (
        <p className="empty">
          {selected.length ? "No other owner's project from the research is under 25 mi from both." : "The research team was off for this run."}
        </p>
      )}
      {links.map((t) => {
        const r = run.research[t.research_id];
        if (!r) return null;
        return (
          <div key={t.research_id} className={`other cat-${r.category}`} role="button" tabIndex={0}
            onClick={() => useUI.getState().setPanel({ kind: "research", id: r.id })}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), useUI.getState().setPanel({ kind: "research", id: r.id }))}>
            <div><span className="catname">{CATEGORY_LABEL[r.category]}</span> · {r.utility}</div>
            <b>{r.name}</b>
            <div>
              <span className="num">{t.dist_a_mi.toFixed(1)}</span> mi from Dominion&apos;s, <span className="num">{t.dist_b_mi.toFixed(1)}</span> mi from
              Georgia&apos;s · in service {statedDate(r.in_service)} ({days(t.gap_a_days, t.approx_date)} / {days(t.gap_b_days, t.approx_date)})
            </div>
          </div>
        );
      })}
      {links.length > 0 && <p className="note">Under 25 mi from both project centers. Day gaps compare in-service dates; ≈ means the source gives only a year or month.</p>}
    </>
  );
}

function ResearchView({ id }: { id: string }) {
  const r = run.research[id];
  const links = useOthers().filter((t) => t.research_id === id);
  useEffect(() => {
    if (r?.lat != null) useUI.getState().flyTo({ kind: "point", lon: r.lon!, lat: r.lat! });
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!r) return <div className="detail"><Back /><p className="empty">Unknown project.</p></div>;
  const v = r.verification ?? {};
  return (
    <div className={`detail cat-${r.category}`}>
      <Back />
      <p className="label"><span className="catname">Other utility · {CATEGORY_LABEL[r.category]}</span></p>
      <h2>{r.name}</h2>
      <p className="sub">{r.utility}{r.utility_kind ? ` · ${r.utility_kind}` : ""} · {r.status.replace("_", " ")}</p>
      {r.found_by === "gemini_search"
        ? <p className="checked live">From a live Google-grounded Gemini search. Not checked by the fact-checkers.</p>
        : v.verifiers ? <p className="checked">Confirmed by {v.confirmed} of {v.verifiers} independent fact-checkers.</p> : null}
      {r.description && <p className="written">{r.description}</p>}

      <h3>Timing</h3>
      <table className="kv"><tbody>
        <tr><td>Start</td><td>{statedDate(r.start)}</td></tr>
        <tr><td>In service</td><td>{statedDate(r.in_service)}</td></tr>
        {r.miles != null && <tr><td>Length</td><td>{r.miles} mi</td></tr>}
        {r.cost_usd != null && <tr><td>Cost (as stated)</td><td>{money(r.cost_usd)}</td></tr>}
      </tbody></table>
      {r.date_quote && <div className="quote">{r.date_quote}</div>}
      {r.date_precision && r.date_precision !== "day" && (
        <p className="note">The source gives a {r.date_precision}; day gaps use its last day and are marked ≈.</p>
      )}

      <h3>Where</h3>
      <div className="prov">
        {r.endpoints.length
          ? r.endpoints.map((e, i) => <div key={i}><EndpointLine e={e} /></div>)
          : <div>The sources name no place.</div>}
        <div>{CONF_LABEL[r.location_confidence]}</div>
      </div>

      <h3>Near these opportunities</h3>
      {links.length === 0 && <p className="empty">Not under 25 mi from both sides of any opportunity on the list.</p>}
      {links.map((t) => {
        const [a, b] = t.overlap_id.split("|");
        const pa = run.projects[a], pb = run.projects[b];
        if (!pa || !pb) return null;
        return (
          <div key={t.overlap_id} className="row compact" tabIndex={0} role="button"
            onClick={() => { useUI.getState().setPanel({ kind: "pair", a, b }); useUI.getState().flyTo({ kind: "pair", a, b }); }}>
            <span className="rk">↗</span>
            <div><div className="t"><span className="a">{pa.name}</span><br /><span className="b">{pb.name}</span></div>
              <div className="meta"><span><span className="num">{t.dist_a_mi.toFixed(1)}</span> / <span className="num">{t.dist_b_mi.toFixed(1)}</span> mi</span></div></div>
          </div>
        );
      })}

      <h3>Sources</h3>
      <div className="srcs">
        {r.sources.map((s, i) => (
          <div key={i}>
            <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title || s.url}</a>
            <div className="pub">{s.publisher}{s.accessed ? ` · accessed ${s.accessed}` : ""}</div>
            {s.quote && <div className="quote">{s.quote}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
