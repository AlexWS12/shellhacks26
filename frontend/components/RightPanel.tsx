"use client";

import { useEffect, useRef, useState } from "react";

import { api, type FilterState } from "@/lib/api";
import { activeIn, lineCheck } from "@/lib/filters";
import {
  ACTOR_LABEL, CONF_LABEL, TYPE_LABEL, WRITERS, fmtDate, gapLabel, money, plural, shortName, statedDate, utilityName,
} from "@/lib/format";
import { run, useRev } from "@/lib/run";
import type { Brief, CostBlock, CostEstimate, Endpoint, Overlap, PairDetail, Project, Shared, ThirdParty, Tier, Written } from "@/lib/types";
import {
  approxDate, colorOf, costKind, dateWord, gapText, legendSources, ownerName, ownerShort, ownStyle, serviceDate, shapeOf,
  useSources, whereFrom,
} from "@/lib/owners";
import { type PairTab, useUI } from "@/lib/ui";
import { KIND_LABEL, kindOf } from "@/lib/utilityIcons";

import Gantt, { overlapText } from "./Gantt";
import UtilityIcon from "./UtilityIcon";

const Chip = ({ a }: { a: string }) => <i className={`chipe ${a.startsWith("jev") ? "jev" : a}`}>{ACTOR_LABEL[a] ?? a}</i>;

export default function RightPanel() {
  useRev((s) => s.rev);
  const panel = useUI((s) => s.panel);
  if (panel.kind === "pair") return <PairView a={panel.a} b={panel.b} />;
  if (panel.kind === "project") return <ProjectView id={panel.id} />;
  if (panel.kind === "agent") return <AgentView id={panel.id} />;
  if (panel.kind === "research") return <ResearchView id={panel.id} />;
  if (panel.kind === "report") return <ReportView />;
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

// list and map marker: each source's shape (Georgia a diamond) in its color, both from /api/sources
function Mk({ p }: { p: Project }) {
  return <i className={`mk ${shapeOf(p)}`} style={ownStyle(p)} aria-label={ownerShort(p)} role="img" />;
}

const BASIS = { filed: "from the filing", published: "published, quote checked", benchmark: "modeled" } as const;

// From a list: the detail panel starts on Overview.
function open(o: Overlap) {
  useUI.getState().setPairTab("overview");
  go(o);
}

// Step to another opportunity; the detail panel keeps its tab. dir picks the card's slide.
let slide: -1 | 0 | 1 = 0;
function go(o: Overlap, dir: -1 | 0 | 1 = 0) {
  slide = dir;
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

// The opportunities as the list shows them: filtered by chip and search, sorted. The detail panel steps through
// the same order.
function useShownOverlaps(): { overlaps: Overlap[]; shown: Overlap[] } {
  const filters = useUI((s) => s.filters);
  const overlaps = useOverlaps();
  const q = filters.q.trim().toLowerCase();
  const chip = CHIPS.find((c) => c.id === filters.chip) ?? CHIPS[0];
  const sort = SORTS.find((x) => x.id === filters.sort) ?? SORTS[0];
  const shown = overlaps.filter((o) => chip.test(o) && matches(o, q));
  // active pairs stay on top whatever the sort, same as the server's ranking
  if (run.phase === "done") shown.sort((x, y) => Number(Boolean(x.finished)) - Number(Boolean(y.finished)) || sort.cmp(x, y) || x.rank - y.rank);
  return { overlaps, shown };
}

function matches(o: Overlap, q: string): boolean {
  if (!q) return true;
  return [run.projects[o.project_a], run.projects[o.project_b]].some((p) => p &&
    [p.id, p.name, ...p.endpoints.map((e) => e.name)].some((t) => t.toLowerCase().includes(q)));
}

export const TIER_LABEL: Record<Tier, string> = {
  touching: "Touching or crossing: must coordinate outages and crossing structures",
  row: "Under 1 mile: can share the land itself (right-of-way, access roads, permits)",
  site: "Under 5 miles: can share site logistics (laydown yards, deliveries)",
  crew: "Under 25 miles: can share crews and equipment",
};

function Action({ o }: { o: Overlap }) {
  if (o.tier === "touching") return <span className="act crews">Must coordinate</span>;
  if (o.tier === "row") return <span className="act crews">Share land</span>;
  if (o.tier === "site") return <span className="act crews">Share site</span>;
  if (o.windows_overlap === true && !o.finished) return <span className="act crews">Share crews</span>;
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
          {run.report && <a role="menuitem" href={api.reportUrl} onClick={() => setOpen(false)}>Report .md</a>}
          {run.report && <a role="menuitem" href={api.reportPdfUrl} onClick={() => setOpen(false)}>Report .pdf</a>}
        </div>
      )}
    </div>
  );
}

// Moved here from the left column so the numbers stay in view when the rail's panel is closed.
function Kpis() {
  const visibleProjects = useUI((s) => s.visibleProjects);
  const results = useUI((s) => s.results);
  const total = Object.keys(run.projects).length;
  const onMap = visibleProjects ?? Object.values(run.projects).filter((p) => p.lat != null).length;
  const pairs = (run.phase === "done" && results ? results : run.overlaps).length;
  return (
    <div className="kpis">
      <div><b>{total}</b>projects read</div>
      <div><b>{onMap}</b>on the map</div>
      <div><b>{pairs}</b>under 25 mi</div>
    </div>
  );
}

function OpportunityList() {
  const { filters, setFilters } = useUI();
  const keySources = legendSources(useSources((st) => st.list), Object.values(run.projects));
  const { overlaps, shown } = useShownOverlaps();
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

  return (
    <div className="opps">
      <Kpis />
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
      {run.report && (
        <button className="reportbtn" onClick={() => useUI.getState().setPanel({ kind: "report" })}>
          <b>Read the report</b>
          <span>The Writer&apos;s summary of these opportunities, with next steps</span>
        </button>
      )}
      <div className="key">
        {keySources.map((x) => (
          <span key={x.id}><i className={`mk ${x.display.shape === "diamond" ? "diamond" : "circle"}`} style={{ "--own": x.color } as React.CSSProperties} />
            {x.display.short_name ?? x.display_name}</span>
        ))}
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
              title={`${ownerName(a)}: ${a.name}\n${ownerName(b)}: ${b.name}`}
              onClick={() => open(o)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(o))}>
              <div className="names">
                <div><Mk p={a} /><span>{shortName(a)}</span></div>
                <div><Mk p={b} /><span>{shortName(b)}</span></div>
              </div>
              <div className="dist"><b>{o.distance_mi.toFixed(2)}</b>miles</div>
              <div className="meta">
                <Action o={o} />
                <span>{approxDate(a) || approxDate(b) ? "about " : ""}{gapLabel(o.time_gap_days)} apart</span>
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

const TABS: { id: PairTab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "money", label: "Savings" },
  { id: "meeting", label: "Meeting" }, { id: "evidence", label: "Evidence" },
];
const DOTS = 7; // at most this many dots, centered on the current opportunity

const savingsRange = (c: CostBlock | undefined): string | null =>
  c && "savings_high" in c && c.savings_high != null ? `${c.savings_low ? money(c.savings_low) : "$0"}–${money(c.savings_high)}` : null;

// Opportunity detail: a pinned summary card with the neighbors peeking in (step with ‹ ›, the dots, ← → or J / K),
// then four tabs. The tab stays while stepping, so Savings can be compared across pairs.
function PairView({ a, b }: { a: string; b: string }) {
  const id = `${a}|${b}`;
  const local = run.costs[id];
  const allOthers = useOthers();
  const { shown } = useShownOverlaps();
  const tab = useUI((s) => s.pairTab);
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
  const i = shown.findIndex((x) => x.id === id);
  const prev = i > 0 ? shown[i - 1] : null;
  const next = i >= 0 && i < shown.length - 1 ? shown[i + 1] : null;

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable='true'], [role='tab']")) return;
    const k = e.key.toLowerCase();
    if ((k === "arrowleft" || k === "k") && prev) { e.preventDefault(); go(prev, -1); }
    if ((k === "arrowright" || k === "j") && next) { e.preventDefault(); go(next, 1); }
  };

  const detail = {
    cost: own ? local : remote?.cost,
    shared: own ? local?.shared : remote?.shared,
    analysis: own ? run.analyses[id] : remote?.analysis ?? run.analyses[id],
    brief: own ? run.briefs[id] : remote?.brief ?? run.briefs[id],
    links: allOthers.some((t) => t.overlap_id === id) ? allOthers.filter((t) => t.overlap_id === id) : remote?.others ?? [],
  };
  const waiting = !own && !remote;

  return (
    <div className="oc" tabIndex={-1} onKeyDown={onKey}>
      <div className="oc-top">
        <Back />
        <span className="spacer" />
        {o && <span className="label">#{i >= 0 ? i + 1 : o.rank}{i >= 0 && ` of ${shown.length}`}</span>}
      </div>
      {!pa || !pb || !o ? <p className="empty oc-body">Loading…</p> : (
        <>
          <div className="oc-stage">
            {prev && <i className="oc-peek l" aria-hidden="true" />}
            {next && <i className="oc-peek r" aria-hidden="true" />}
            <div key={id} className={`oc-card ${slide < 0 ? "from-l" : slide > 0 ? "from-r" : ""}`}
              style={{ "--a": colorOf(pa), "--b": colorOf(pb) } as React.CSSProperties}>
              <div className="oc-conn" aria-hidden="true"><Mk p={pa} /><i className="oc-line" /><Mk p={pb} /></div>
              <div className="oc-names">
                <div><b>{pa.name}</b><span>{ownerName(pa)} · {serviceDate(pa, fmtDate)}</span></div>
                <div><b>{pb.name}</b><span>{ownerName(pb)} · {serviceDate(pb, fmtDate)}</span></div>
              </div>
              <div className="oc-dist"><b>{o.distance_mi.toFixed(2)}</b><span>mi apart</span><Action o={o} /></div>
            </div>
            <button className="oc-arrow l" onClick={() => prev && go(prev, -1)} disabled={!prev} aria-label="Previous opportunity">‹</button>
            <button className="oc-arrow r" onClick={() => next && go(next, 1)} disabled={!next} aria-label="Next opportunity">›</button>
          </div>
          {i >= 0 && shown.length > 1 && <Dots shown={shown} i={i} />}
          <PairTabs />
          <div className="oc-body" role="tabpanel" id={`oc-panel-${tab}`} aria-labelledby={`oc-tab-${tab}`}>
            {waiting ? <p className="empty">Loading…</p>
              : tab === "overview" ? <PairOverview o={o} pa={pa} pb={pb} {...detail} />
              : tab === "money" ? <PairSavings pa={pa} pb={pb} cost={detail.cost} />
              : tab === "meeting" ? <PairMeeting pa={pa} pb={pb} brief={detail.brief} />
              : <PairEvidence pa={pa} pb={pb} />}
          </div>
        </>
      )}
    </div>
  );
}

function Dots({ shown, i }: { shown: Overlap[]; i: number }) {
  const start = Math.max(0, Math.min(i - Math.floor(DOTS / 2), shown.length - DOTS));
  return (
    <div className="oc-dots">
      {shown.slice(start, start + DOTS).map((x, k) => (
        <button key={x.id} className={`oc-dot ${start + k === i ? "on" : ""}`} aria-label={`Opportunity ${start + k + 1}`}
          aria-current={start + k === i} onClick={() => start + k !== i && go(x, start + k < i ? -1 : 1)} />
      ))}
    </div>
  );
}

function PairTabs() {
  const tab = useUI((s) => s.pairTab);
  const setTab = useUI((s) => s.setPairTab);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, k: number) => {
    const to = e.key === "ArrowRight" ? (k + 1) % TABS.length : e.key === "ArrowLeft" ? (k - 1 + TABS.length) % TABS.length
      : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    setTab(TABS[to].id);
    refs.current[to]?.focus();
  };
  return (
    <div className="oc-tabs" role="tablist" aria-label="Opportunity details">
      {TABS.map((t, k) => (
        <button key={t.id} ref={(el) => { refs.current[k] = el; }} id={`oc-tab-${t.id}`} role="tab" className="oc-tab"
          aria-selected={tab === t.id} aria-controls={`oc-panel-${t.id}`} tabIndex={tab === t.id ? 0 : -1}
          onClick={() => setTab(t.id)} onKeyDown={(e) => onKey(e, k)}>{t.label}</button>
      ))}
    </div>
  );
}

function Stat({ label, value, text }: { label: string; value: string; text?: boolean }) {
  return <div className="oc-stat"><span>{label}</span><b className={text ? "text" : ""}>{value}</b></div>;
}

function PairOverview({ o, pa, pb, cost, shared, analysis, links }: {
  o: Overlap; pa: Project; pb: Project; cost?: CostBlock; shared?: Shared; analysis?: Written | null; links: ThirdParty[];
}) {
  const first = links.map((t) => run.research[t.research_id]).find(Boolean);
  return (
    <div className="oc-stack">
      <div className="oc-stats">
        <Stat label="In-service gap" value={`${gapText(o.time_gap_days, pa, pb)} days`} />
        <Stat label="Location" value={CONF_LABEL[o.pair_confidence]} text />
        <Stat label="Center to center" value={o.center_mi != null ? `${o.center_mi.toFixed(2)} mi` : "—"} />
        <Stat label="Savings" value={savingsRange(cost) ?? "—"} />
      </div>
      {(o.finished || slackNote(o) || o.in_sponsor_sample) && (
        <div>
          {o.in_sponsor_sample && <p className="note c-accent">One of Sperry&apos;s benchmark pairs.</p>}
          {o.finished && <p className="note">At least one of these projects is already in service, so crews, equipment and outage timing can&apos;t be shared.</p>}
          {slackNote(o) && <p className="note">{slackNote(o)}</p>}
        </div>
      )}
      <div>
        <p className="label">Build windows</p>
        <Gantt a={pa} b={pb} colors={[colorOf(pa), colorOf(pb)]} />
        <p className="oc-caption">{overlapText(pa, pb)}</p>
      </div>
      {shared && (
        <div>
          <p className="label">Could share</p>
          <div className="oc-pills">{shared.items.map((x) => <span key={x} className="oc-pill">{x}</span>)}</div>
          {shared.tier && <p className="oc-caption">{TIER_LABEL[shared.tier]}</p>}
        </div>
      )}
      <div className={`oc-analysis ${analysis && WRITERS.has(analysis.actor) ? "gemini" : ""}`}>
        <p>{analysis ? analysis.text : insight(o, shared?.timing ?? "unknown")}</p>
        <div className="oc-byline">
          {analysis && WRITERS.has(analysis.actor) ? `Written by ${ACTOR_LABEL[analysis.actor]} from the filing text` : "From the computed facts"}
          {analysis?.unsupported_numbers?.length ? <span className="fail"> · check numbers: {analysis.unsupported_numbers.join(", ")}</span> : null}
        </div>
      </div>
      {first && (
        <button className="oc-others" onClick={() => useUI.getState().setPanel({ kind: "research", id: first.id })}>
          {plural(links.length, "other owner")} nearby: <b>{first.name}</b>{links.length > 1 && ` and ${links.length - 1} more`} ›
        </button>
      )}
    </div>
  );
}

// Georgia's costs are redacted in its filing, so its projects get a published or modeled estimate.
function CostLine({ p, e }: { p: Project; e: CostEstimate | null }) {
  return (
    <>
      <span>{ownerShort(p)} cost{e && <> · <small>{BASIS[e.basis]}</small></>}</span>
      <span className="num">{e ? money(e.amount) : costKind(p) === "redacted" ? "redacted" : "not stated"}</span>
    </>
  );
}

function PairSavings({ pa, pb, cost }: { pa: Project; pb: Project; cost?: CostBlock }) {
  if (!cost) return <p className="empty">Waiting for the savings calculator.</p>;
  if (!("savings_high" in cost)) return <p className="empty">This replay predates the savings calculator. Run again to see cost estimates.</p>;
  return (
    <div className="oc-stack tight">
      <div>
        <div className="oc-big">{savingsRange(cost) ?? "—"}</div>
        <div className="oc-caption">possible savings · team assumption</div>
      </div>
      <div className="oc-costs"><CostLine p={pa} e={cost.a} /><CostLine p={pb} e={cost.b} /></div>
      <div>
        <p className="note">{cost.statement}
          {cost.check && <> <Chip a={cost.check.actor} />{cost.check.p >= 0.5 ? "judged" : "didn't judge"} sharing worth raising between the two utilities ({Math.round(cost.check.p * 100)}%).</>}</p>
        {[cost.a, cost.b].map((e) => e && e.basis !== "filed" && (
          <p className="note" key={e.project_id}>{ownerShort(run.projects[e.project_id] ?? pa)}: {e.method}
            {e.quote && <> “{e.quote}” <a href={e.source} target="_blank" rel="noreferrer">{e.source_title || "source"}</a></>}</p>
        ))}
      </div>
    </div>
  );
}

function PairMeeting({ pa, pb, brief }: { pa: Project; pb: Project; brief?: Brief | null }) {
  if (!brief || !(brief.dominion || brief.georgia || brief.mediator)) {
    return <p className="empty">The advocates and the mediator prepare the top opportunities only.</p>;
  }
  return (
    <div className="oc-stack tight">
      {brief.dominion && <div className="brief"><div className="who" style={{ color: colorOf(pa) }}>{ownerShort(pa)} advocate <Chip a={brief.dominion.actor} /></div>{brief.dominion.text}</div>}
      {brief.georgia && <div className="brief"><div className="who" style={{ color: colorOf(pb) }}>{ownerShort(pb)} advocate <Chip a={brief.georgia.actor} /></div>{brief.georgia.text}</div>}
      {brief.mediator && <div className="brief"><div className="who c-ink">Mediator · joint agenda <Chip a={brief.mediator.actor} /></div>{brief.mediator.text}</div>}
    </div>
  );
}

function PairEvidence({ pa, pb }: { pa: Project; pb: Project }) {
  return (
    <div className="oc-stack tight">
      {[pa, pb].map((p) => (
        <div key={p.id} className="oc-quote" style={ownStyle(p)}>{p.description}<span>{whereFrom(p)} ↗</span></div>
      ))}
      <div>
        <p className="label">Where this came from</p>
        <div className="prov"><Provenance p={pa} /><Provenance p={pb} /></div>
      </div>
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
      <b className="own c" style={ownStyle(p)}>{p.name}</b><br />
      {whereFrom(p)}
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
        {dateWord(p)} {serviceDate(p, fmtDate)}
        {p.build_start && ` · starts ${fmtDate(p.build_start)}`} · {CONF_LABEL[p.location_confidence]}
        {p.cost_total != null && ` · ${money(p.cost_total)}`}
      </p>
      <div className="quote">{p.description}</div>
      {p.need_text && <div className="quote">Why: {p.need_text}</div>}
      <div className="prov"><Provenance p={p} /></div>
      <h3>Nearby work from other owners</h3>
      {overlaps.length ? overlaps.map((o) => {
        const other = run.projects[o.project_a === id ? o.project_b : o.project_a];
        return (
          <div key={o.id} className="row compact" tabIndex={0} role="button" onClick={() => open(o)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(o))}>
            <span className="rk">{String(o.rank).padStart(2, "0")}</span>
            <div><div className="t">{other && <span className="own" style={ownStyle(other)}>{other.name}</span>}{other && <span className="sub-inline"> · {ownerName(other)}</span>}</div><div className="meta"><span><span className="num">{o.distance_mi.toFixed(2)}</span> mi</span><span>{gapText(o.time_gap_days, run.projects[o.project_a], run.projects[o.project_b])} days apart</span></div></div>
          </div>
        );
      }) : <p className="empty">Nothing from another owner within 25 miles. Most projects look like this.</p>}
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
      <p className="label"><span className="catname"><UtilityIcon kind={kindOf(r)} /> Other utility · {KIND_LABEL[kindOf(r)]}</span></p>
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
      {v.merged_from?.length ? (
        <div className="note">Also reported as: {v.merged_from.map((m, i) => (
          <span key={i}>{i > 0 && "; "}{m.name} (in service {statedDate(m.in_service)})</span>
        ))}. Where dates differ, the most precise one is shown above.</div>
      ) : null}
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
            <div><div className="t"><span className="own" style={ownStyle(pa)}>{pa.name}</span><br /><span className="own" style={ownStyle(pb)}>{pb.name}</span></div>
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

function Byline({ w }: { w: Written }) {
  return (
    <div className="by"><Chip a={w.actor} />{WRITERS.has(w.actor) ? `Written by ${ACTOR_LABEL[w.actor]} from the facts below` : "Template from the computed facts"}
      {w.unsupported_numbers?.length ? <span className="fail">check numbers: {w.unsupported_numbers.join(", ")}</span> : null}</div>
  );
}

function ReportView() {
  const r = run.report;
  if (!r) return <div className="detail"><Back /><p className="empty">No report yet. It appears when the Writer finishes a run.</p></div>;
  const c = r.counts;
  return (
    <div className="detail report">
      <Back />
      <p className="label">Report · as of {fmtDate(r.as_of)}</p>
      <h2>{r.title}</h2>
      <p className="sub">Every number here comes from the pipeline&apos;s code. Only the summary and next steps are written text.</p>

      <h3>Summary</h3>
      <p className="written">{r.summary.text}</p>
      <Byline w={r.summary} />

      <h3>At a glance</h3>
      <table className="kv"><tbody>
        {Object.entries(r.owners).map(([o, n]) => <tr key={o}><td>{o}</td><td>{plural(n, "project")}</td></tr>)}
        <tr><td>On the map</td><td>{c.placed} ({c.unlocated} unlocated)</td></tr>
        <tr><td>Pairs under 25 mi</td><td>{c.opportunities} ({c.built_at_same_time} same time)</td></tr>
        {r.tiers && <tr><td>By closest distance</td><td>{r.tiers.touching} touching · {r.tiers.row} under 1 mi · {r.tiers.site} under 5 mi</td></tr>}
        <tr><td>Benchmark</td><td>{c.benchmark_passed}/{c.benchmark_total} exact</td></tr>
        <tr><td>Data issues</td><td>{c.issues_error} errors · {c.issues_warn} warnings</td></tr>
        {r.research_categories.length > 0 && <tr><td>Other utilities ({r.research_categories.join(", ")})</td><td>{c.other_utility_projects} projects · near {c.opportunities_with_other_utilities} pairs</td></tr>}
      </tbody></table>

      <h3>Top opportunities</h3>
      {r.top.length === 0 && <p className="empty">No pairs from different owners under 25 mi in the default view.</p>}
      {r.top.map((t) => {
        const [a, b] = t.overlap_id.split("|");
        return (
          <div key={t.overlap_id} className="row compact" tabIndex={0} role="button"
            onClick={() => { useUI.getState().setPanel({ kind: "pair", a, b }); useUI.getState().flyTo({ kind: "pair", a, b }); }}>
            <span className="rk">{String(t.rank).padStart(2, "0")}</span>
            <div>
              <div className="t">{t.a.name} <span className="sub-inline">· {t.a.owner}</span><br />{t.b.name} <span className="sub-inline">· {t.b.owner}</span></div>
              <div className="meta">
                <span><span className="num">{t.distance_mi.toFixed(2)}</span> mi</span>
                {t.tier === "touching" && <span className="tag together">touching</span>}
                <span><span className="num">{t.a.date_precision !== "day" || t.b.date_precision !== "day" ? "about " : ""}{t.time_gap_days.toLocaleString()}</span> days apart</span>
                {t.built_at_same_time && <span className="tag together">built at the same time</span>}
                {t.other_utilities.length > 0 && <span className="tag unknown">+{t.other_utilities.length} other owners nearby</span>}
              </div>
            </div>
          </div>
        );
      })}

      <h3>Recommended next steps</h3>
      <p className="written">{r.next_steps.text}</p>
      <Byline w={r.next_steps} />

      {r.issues.length > 0 && (
        <>
          <h3>Data issues the pipeline caught</h3>
          {r.issues.map((i, k) => <div key={k} className="entry"><b>{i.title}</b> · {i.source}</div>)}
        </>
      )}

      <h3>Method</h3>
      <div className="prov">{r.method.map((m, k) => <div key={k}>{m}</div>)}</div>
      <div className="exports"><a href={api.reportUrl}>Download report (.md)</a> · <a href={api.reportPdfUrl}>Download report (.pdf)</a></div>
    </div>
  );
}
