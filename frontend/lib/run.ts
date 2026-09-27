// Run state, built only from events. Re-renders at most once per frame.

import { create } from "zustand";

import { readerHome } from "./owners";

import type {
  AgentSpec, Brief, Check, CostBlock, ModelIssue, Overlap, Project, ReferenceResult, Report, ResearchCategory, ResearchProject, RunEvent,
  SourceSpec, ThirdParty, Written,
} from "./types";

export type AgentStatus = "idle" | "working" | "done" | "error";

export interface AgentView extends AgentSpec {
  status: AgentStatus;
  count: number | null;
  total: number | null;
  label: string;
  summary: string;
  judgments: number;
  lastTool: string;
  ms: number | null;
}

export type AgentEntry =
  | { kind: "tool"; tool: string; args: unknown; summary: string | null; ok: boolean | null; actor: string }
  | { kind: "judgment"; actor: string; question: string; subject: string; answer: unknown; confidence: number; ms: number; cached: boolean }
  | { kind: "note"; text: string };

export interface SourceView extends SourceSpec {
  read: number;
  current: string;
  method: string;
}

export interface JudgeTotals {
  n: number;
  cost: number;
  ms: number;
  cached: number;
}

export interface RunData {
  runId: string | null;
  mode: "live" | "replay" | "results" | null;
  replayOf: string | null;
  phase: "idle" | "running" | "done" | "failed";
  ok: boolean | null; // false = some agents failed
  failMsg: string;
  lastSeq: number;
  agents: Record<string, AgentView>;
  agentOrder: string[];
  sources: Record<string, SourceView>;
  sourceOrder: string[];
  projects: Record<string, Project>;
  unlocated: Record<string, string>;
  checks: Check[];
  overlaps: Overlap[];
  reference: ReferenceResult[];
  analyses: Record<string, Written>;
  costs: Record<string, CostBlock>;
  briefs: Record<string, Brief>;
  research: Record<string, ResearchProject>; // other utilities' projects
  researchSelected: ResearchCategory[];
  thirdParty: ThirdParty[];
  modelIssues: ModelIssue[]; // model failures and fallbacks, listed in the pipeline panel (live and replay)
  report: Report | null; // the Writer's final report
  log: { text: string; agent?: string }[];
  ticker: string;
  judges: Record<string, JudgeTotals>;
  agentLog: Record<string, AgentEntry[]>;
  thinking: Record<string, string>;
  health: Record<string, { stuck: number; progress: number; actor: string }>;
  handoffs: { id: number; from: string; to: string; t: number }[];
  recent: Record<string, number>; // when each pin dropped, for the animation
  agentPos: Record<string, { lon: number; lat: number; t: number; label: string }>; // where each agent is working
  linkBorn: Record<string, number>; // when each overlap was found, for the connection animation
  stats: Record<string, number> | null;
  startTs: number | null;
  endTs: number | null;
  lastTs: number | null;
  totalCost: number;
}

const empty = (): RunData => ({
  runId: null, mode: null, replayOf: null, phase: "idle", ok: null, failMsg: "", lastSeq: -1,
  agents: {}, agentOrder: [], sources: {}, sourceOrder: [], projects: {}, unlocated: {}, checks: [], overlaps: [],
  reference: [], analyses: {}, costs: {}, briefs: {}, research: {}, researchSelected: [], thirdParty: [], modelIssues: [], report: null, log: [], ticker: "Not started.", judges: {}, agentLog: {},
  thinking: {}, health: {}, handoffs: [], recent: {}, agentPos: {}, linkBorn: {}, stats: null, startTs: null, endTs: null, lastTs: null, totalCost: 0,
});

export const run: RunData = empty();

export const useRev = create<{ rev: number }>(() => ({ rev: 0 }));
let pending = false;
export function bump(): void {
  if (pending) return;
  pending = true;
  const flush = () => {
    pending = false;
    useRev.setState((s) => ({ rev: s.rev + 1 }));
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
  else setTimeout(flush, 16);
}

export function reset(mode: RunData["mode"] = null): void {
  // keep the graph and skip the intro card
  const specs = run.agentOrder.map((id) => run.agents[id]);
  Object.assign(run, empty(), { mode, phase: mode ? "running" : "idle", ticker: mode ? "Starting…" : "Not started." });
  run.agentOrder = specs.map((a) => a.id);
  run.agents = Object.fromEntries(specs.map((a) => [a.id, newAgent(a)]));
  bump();
}

export function setAgentSpecs(specs: AgentSpec[]): void {
  run.agentOrder = specs.map((s) => s.id);
  run.agents = Object.fromEntries(specs.map((s) => [s.id, newAgent(s)]));
  bump();
}

const newAgent = (s: AgentSpec): AgentView => ({
  ...s, status: "idle", count: null, total: null, label: "", summary: "", judgments: 0, lastTool: "", ms: null,
});

let handoffId = 0;
const MAX_LOG = 200;
const MAX_AGENT_LOG = 250;

function pushLog(text: string, agent?: string) {
  run.log.unshift({ text, agent });
  if (run.log.length > MAX_LOG) run.log.length = MAX_LOG;
}

function pushAgent(id: string, entry: AgentEntry) {
  const list = (run.agentLog[id] ??= []);
  list.push(entry);
  if (list.length > MAX_AGENT_LOG) list.splice(0, list.length - MAX_AGENT_LOG);
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());


function moveTo(agentId: string | undefined, projectIds: string[], label: string) {
  if (!agentId) return;
  const pts = projectIds.map((id) => run.projects[id]).filter((p) => p && p.lat != null && p.lon != null);
  if (!pts.length) return;
  const lon = pts.reduce((sum, p) => sum + p!.lon!, 0) / pts.length;
  const lat = pts.reduce((sum, p) => sum + p!.lat!, 0) / pts.length;
  run.agentPos[agentId] = { lon, lat, t: now(), label };
}

export function apply(e: RunEvent): void {
  if (e.seq <= run.lastSeq) return; // already seen (reconnects resend old events)
  run.lastSeq = e.seq;
  run.lastTs = e.ts;
  const aid = e.agent_id;
  const agent = aid ? run.agents[aid] : undefined;

  switch (e.type) {
    case "run.started": {
      const specs = (e.agents as AgentSpec[] | undefined) ?? [];
      run.runId = e.run_id;
      run.mode = e.mode as RunData["mode"];
      run.replayOf = (e.replay_of as string) ?? null;
      run.phase = "running";
      run.startTs = e.ts;
      run.agentOrder = specs.map((s) => s.id);
      run.agents = Object.fromEntries(specs.map((s) => [s.id, newAgent(s)]));
      const sources = (e.sources as SourceSpec[] | undefined) ?? [];
      run.sourceOrder = sources.map((s) => s.id);
      run.sources = Object.fromEntries(sources.map((s) => [s.id, { ...s, read: 0, current: "", method: "" }]));
      run.researchSelected = (e.research as ResearchCategory[] | undefined) ?? [];
      pushLog(e.mode === "replay" ? `Replaying recorded run ${e.replay_of}.` : "Opened both filings and the sponsor sample.");
      break;
    }
    case "agent.spawned":
      if (agent) agent.status = "working";
      // Readers "sit" at their utility's headquarters while they read the filing (display.hq in /api/sources).
      if (aid && readerHome(aid)) run.agentPos[aid] = { ...readerHome(aid)!, t: now() };
      break;
    case "agent.done":
      if (aid) delete run.agentPos[aid];
      if (agent) {
        agent.status = "done";
        agent.summary = String(e.summary ?? "");
        agent.ms = (e.ms as number) ?? null;
        run.totalCost += (e.cost_usd as number) ?? 0;
      }
      break;
    case "agent.error":
      if (agent) {
        agent.status = "error";
        agent.summary = String(e.message);
      }
      pushLog(`${agent?.name ?? aid}: ${e.message}`, aid);
      break;
    case "agent.progress":
      if (agent) {
        agent.count = e.count as number;
        agent.total = (e.total as number) ?? null;
        agent.label = String(e.label ?? "");
      }
      break;
    case "agent.log":
      pushLog(String(e.text), aid);
      break;
    case "agent.thinking":
      if (aid) run.thinking[aid] = ((run.thinking[aid] ?? "") + String(e.chunk)).slice(-6000);
      break;
    case "agent.health":
      if (aid) run.health[aid] = { stuck: e.stuck as number, progress: e.progress as number, actor: String(e.actor) };
      break;
    case "handoff":
      run.handoffs.push({ id: ++handoffId, from: String(e.from_agent), to: String(e.to_agent), t: now() });
      if (run.handoffs.length > 40) run.handoffs.shift();
      break;
    case "tool.call":
      if (agent) agent.lastTool = String(e.tool);
      if (aid) pushAgent(aid, { kind: "tool", tool: String(e.tool), args: e.args, summary: null, ok: null, actor: String(e.actor ?? "code") });
      break;
    case "tool.result": {
      const list = aid ? run.agentLog[aid] : undefined;
      const open = list?.slice().reverse().find((x) => x.kind === "tool" && x.tool === e.tool && x.summary === null);
      if (open && open.kind === "tool") {
        open.summary = String(e.summary);
        open.ok = Boolean(e.ok);
      }
      break;
    }
    case "judgment": {
      const actor = String(e.actor);
      const t = (run.judges[actor] ??= { n: 0, cost: 0, ms: 0, cached: 0 });
      t.n += 1;
      t.cost += (e.cost_usd as number) ?? 0;
      t.ms += (e.latency_ms as number) ?? 0;
      if (e.cached) t.cached += 1;
      if (agent) agent.judgments += 1;
      if (aid) pushAgent(aid, {
        kind: "judgment", actor, question: String(e.question), subject: String(e.subject), answer: e.answer,
        confidence: e.confidence as number, ms: (e.latency_ms as number) ?? 0, cached: Boolean(e.cached),
      });
      break;
    }
    case "source.opened": {
      const s = run.sources[String(e.source_id)];
      if (s) s.method = String(e.method);
      break;
    }
    case "source.progress": {
      const s = run.sources[String(e.source_id)];
      if (s) {
        s.read = e.read as number;
        if (typeof e.total === "number" && e.total > 0) s.total = e.total; // submitted plans learn their size while reading
        s.current = String(e.current ?? "");
      }
      run.ticker = String(e.current ?? run.ticker);
      break;
    }
    case "project.extracted": {
      const p = e.project as Project;
      run.projects[p.id] = p;
      break;
    }
    case "project.placed": {
      const p = run.projects[String(e.project_id)];
      if (p) {
        p.lat = e.lat as number;
        p.lon = e.lon as number;
        p.location_confidence = e.confidence as Project["location_confidence"];
        p.endpoints = e.endpoints as Project["endpoints"];
        run.recent[p.id] = now();
        run.ticker = `Placed ${p.name}`;
        moveTo(aid, [p.id], p.name);
      }
      break;
    }
    case "project.unlocated": {
      const p = run.projects[String(e.project_id)];
      if (p) p.endpoints = e.endpoints as Project["endpoints"];
      run.unlocated[String(e.project_id)] = String(e.reason);
      break;
    }
    case "project.classified": {
      const p = run.projects[String(e.project_id)];
      if (p) {
        p.project_type = String(e.project_type);
        p.project_type_actor = String(e.actor);
        moveTo(aid, [p.id], String(e.project_type).replace(/_/g, " "));
      }
      break;
    }
    case "sample.matched": {
      for (const [ref, pid] of Object.entries(e.mapping as Record<string, string>)) {
        const p = run.projects[pid];
        if (p) p.sponsor_ref_id = ref;
      }
      break;
    }
    case "check.found": {
      const c = e.check as Check;
      run.checks.push(c);
      pushLog(`Validator: ${c.title.toLowerCase()}.`, aid);
      if (c.project_id) moveTo(aid, [c.project_id], c.title);
      break;
    }
    case "overlap.found": {
      const o = e.overlap as Overlap;
      run.overlaps.push(o);
      run.linkBorn[o.id] = now();
      moveTo(aid, [o.project_a, o.project_b], `${o.distance_mi.toFixed(1)} mi`);
      break;
    }
    case "reference.result": {
      const r = e.result as ReferenceResult;
      run.reference.push(r);
      moveTo(aid, [r.a_project ?? "", r.b_project ?? ""], r.passed ? "exact match" : "mismatch");
      break;
    }
    case "cost.estimate":
      moveTo(aid, [String(e.project_id)], "costed");
      break;
    case "cost.ready":
      run.costs[String(e.overlap_id)] = e.cost as CostBlock;
      moveTo(aid, String(e.overlap_id).split("|"), "cost");
      break;
    case "analysis.ready":
      run.analyses[String(e.overlap_id)] = {
        text: String(e.text), actor: String(e.actor), model: (e.model as string | null) ?? null, unsupported_numbers: e.unsupported_numbers as string[],
      };
      moveTo(aid, String(e.overlap_id).split("|"), "write-up");
      break;
    case "brief.side": {
      const b = (run.briefs[String(e.overlap_id)] ??= {});
      b[e.side as "dominion" | "georgia"] = { text: String(e.text), actor: String(e.actor), model: (e.model as string | null) ?? null };
      const [pa, pb] = String(e.overlap_id).split("|");
      moveTo(aid, [e.side === "dominion" ? pa : pb], "meeting prep");
      break;
    }
    case "brief.ready":
      run.briefs[String(e.overlap_id)] = e.brief as Brief;
      moveTo(aid, String(e.overlap_id).split("|"), "joint agenda");
      break;
    case "research.found": {
      const r = e.record as ResearchProject;
      run.research[r.id] = r;
      break;
    }
    case "research.placed": {
      const r = run.research[String(e.research_id)];
      if (r) {
        r.lat = e.lat as number;
        r.lon = e.lon as number;
        r.location_confidence = e.confidence as ResearchProject["location_confidence"];
        r.endpoints = e.endpoints as ResearchProject["endpoints"];
        run.recent[`research:${r.id}`] = now();
        if (aid) run.agentPos[aid] = { lon: r.lon, lat: r.lat, t: now(), label: r.utility };
      }
      break;
    }
    case "research.unlocated": {
      const r = run.research[String(e.research_id)];
      if (r) r.endpoints = e.endpoints as ResearchProject["endpoints"];
      break;
    }
    case "third_party.found": {
      const t = e.link as ThirdParty;
      run.thirdParty.push(t);
      moveTo(aid, t.overlap_id.split("|"), run.research[t.research_id]?.utility ?? "other utility");
      break;
    }
    case "report.ready":
      run.report = e.report as Report;
      pushLog("Writer: the report is ready.", aid);
      break;
    case "endpoint.rejected":
      if (aid) pushAgent(aid, { kind: "note", text: `Rejected ${e.endpoint} → ${e.candidate}` });
      break;
    case "store.saved":
      pushLog(`Saved run to Tiger Data (${e.events} events).`);
      break;
    case "store.failed":
      pushLog(`Tiger Data save failed: ${e.message}`);
      break;
    case "pages.located": {
      // AI reader: which pages list projects, and why (each candidate carries its reason)
      const c = (e.candidates as { page: number }[] | undefined) ?? [];
      pushLog(`${agent?.name ?? "Reader"}: ${c.length} of ${e.total} pages list projects${e.by === "code" ? " (found by text signals)" : ""}.`, aid);
      break;
    }
    case "project.extract_failed":
      pushLog(`Parser couldn't read ${e.source_id} page ${e.page}: ${e.reason}. Trying Gemini.`, aid);
      break;
    case "run.done":
      run.agentPos = {};
      run.phase = "done";
      run.ok = e.ok !== false;
      run.endTs = e.ts;
      run.stats = e.stats as Record<string, number>;
      run.ticker = "All sources read.";
      pushLog("Done. Pick a pair to see the details.");
      break;
    case "run.failed":
      run.phase = "failed";
      run.failMsg = String(e.message);
      pushLog(`Run failed: ${e.message}`);
      break;
    // Model registry: a model failing, a fallback taking over, or every model for a job failing.
    case "model.call_failed":
      run.modelIssues.push({ seq: e.seq, type: e.type, role: String(e.role), provider: String(e.provider), model: String(e.model), errorClass: String(e.error_class) });
      pushLog(`${e.role}: ${e.provider}/${e.model} failed (${e.error_class})${e.will_retry ? ", retrying" : e.will_fallback ? ", trying the next model" : ""}.`, aid);
      break;
    case "model.fallback_used":
      run.modelIssues.push({ seq: e.seq, type: e.type, role: String(e.role), from: String(e.from), to: String(e.to) });
      pushLog(`${e.role}: using ${e.to} instead of ${e.from}.`, aid);
      break;
    case "role.exhausted":
      run.modelIssues.push({ seq: e.seq, type: e.type, role: String(e.role) });
      pushLog(`${e.role}: every configured model failed.`, aid);
      break;
  }
  bump();
}

// "Show results": load the last finished run without running.
export function hydrate(data: {
  projects: Project[]; checks: Check[]; reference: ReferenceResult[]; overlaps: Overlap[]; specs: AgentSpec[];
  research: { selected: ResearchCategory[]; records: ResearchProject[]; links: ThirdParty[] };
  report: Report | null;
}): void {
  Object.assign(run, empty(), { mode: "results", phase: "done", ticker: "Showing the latest finished run." });
  run.projects = Object.fromEntries(data.projects.map((p) => [p.id, p]));
  run.checks = data.checks;
  run.reference = data.reference;
  run.overlaps = data.overlaps;
  run.agentOrder = data.specs.map((s) => s.id);
  run.agents = Object.fromEntries(data.specs.map((s) => [s.id, { ...newAgent(s), status: "done" as const }]));
  for (const p of data.projects) if (p.lat == null) run.unlocated[p.id] = "no source matched its endpoints";
  run.research = Object.fromEntries(data.research.records.map((r) => [r.id, r]));
  run.researchSelected = data.research.selected;
  run.thirdParty = data.research.links;
  run.report = data.report;
  bump();
}
