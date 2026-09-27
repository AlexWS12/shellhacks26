
import { create } from "zustand";

import { api, API, type FilterState } from "./api";
import { apply, hydrate, reset, run, setAgentSpecs } from "./run";
import type { AgentSpec, Health, Overlap, ResearchCategory, RunEvent, SourceSpec, ThirdParty } from "./types";

export type Panel =
  | { kind: "list" }
  | { kind: "pair"; a: string; b: string }
  | { kind: "project"; id: string }
  | { kind: "agent"; id: string }
  | { kind: "research"; id: string }
  | { kind: "report" };

export type Camera = { kind: "us" } | { kind: "border" } | { kind: "river" } | { kind: "pair"; a: string; b: string } | { kind: "project"; id: string } | { kind: "point"; lon: number; lat: number };

export type RailTab = "sources" | "agents" | "bench" | "issues" | "activity";
export const RAIL_TABS: RailTab[] = ["sources", "agents", "bench", "issues", "activity"];

export const CATEGORIES: ResearchCategory[] = ["electric", "gas", "roads_water"];
const RESEARCH_KEY = "tandem.research";
const RAIL_KEY = "tandem.rail";

export type RailMode = "full" | "icons" | "hidden"; // icon + name, icon only, nothing (a handle brings it back)
export const NEXT_RAIL: Record<RailMode, RailMode> = { full: "icons", icons: "hidden", hidden: "full" };

function savedRail(): { railOpen?: boolean; railMode?: RailMode } {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(RAIL_KEY) : null;
    if (!raw) return {};
    const v = JSON.parse(raw) as { railOpen?: boolean; railMode?: RailMode; railExpanded?: boolean };
    // older saves kept only on/off labels
    const railMode = v.railMode ?? (v.railExpanded === false ? "icons" : v.railExpanded === true ? "full" : undefined);
    return { ...(v.railOpen != null ? { railOpen: v.railOpen } : {}), ...(railMode ? { railMode } : {}) };
  } catch {
    return {};
  }
}

function saveRail(s: { railOpen: boolean; railMode: RailMode }): void {
  try {
    localStorage.setItem(RAIL_KEY, JSON.stringify({ railOpen: s.railOpen, railMode: s.railMode }));
  } catch {
    // private mode: the layout just isn't remembered
  }
}

function savedResearch(): Record<ResearchCategory, boolean> {
  const all = { electric: true, gas: true, roads_water: true };
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(RESEARCH_KEY) : null;
    return raw ? { ...all, ...(JSON.parse(raw) as Partial<Record<ResearchCategory, boolean>>) } : all;
  } catch {
    return all;
  }
}

interface UIState {
  panel: Panel;
  filters: FilterState;
  results: Overlap[] | null; // from the API after a run
  others: ThirdParty[] | null; // other utilities near those results, from the API
  research: Record<ResearchCategory, boolean>; // which categories the research team covers next run
  hiddenCats: ResearchCategory[]; // other-owner categories hidden on the map (legend toggle)
  toggleCat: (c: ResearchCategory) => void;
  setResearch: (c: ResearchCategory, on: boolean) => void;
  visibleProjects: number | null;
  health: Health | null;
  basemap: "streets" | "simple";
  templateFallback: boolean; // live runs: write a template when Gemini fails, instead of failing the agent
  setTemplateFallback: (on: boolean) => void;
  year: number | null; // timeline: only show work active in this year
  setYear: (y: number | null) => void;
  camera: Camera & { n: number }; // n lets you fly to the same place twice
  error: string | null;
  setPanel: (p: Panel) => void;
  setFilters: (f: Partial<FilterState>) => void;
  refreshResults: () => Promise<void>;
  flyTo: (c: Camera) => void;
  setBasemap: (b: UIState["basemap"]) => void;
  railTab: RailTab; // which pipeline section the left panel shows
  railOpen: boolean; // left panel visible
  railMode: RailMode; // full (icon + name), icons, or hidden
  selectRailTab: (t: RailTab) => void; // clicking the open tab closes the panel
  closeRail: () => void;
  stepRail: () => void; // full -> icons -> hidden
  showRail: () => void; // the handle: back to full
}

const SERVER_KEYS = ["allSponsors", "townLevel", "hideFinished"] as const;
let requests = 0; // only the newest filter request wins

export const useUI = create<UIState>((set, get) => ({
  panel: { kind: "list" },
  filters: { allSponsors: false, townLevel: true, hideFinished: false, sort: "distance", chip: "all", q: "" },
  results: null,
  others: null,
  visibleProjects: null,
  research: { electric: true, gas: true, roads_water: true },
  hiddenCats: [],
  toggleCat: (c) => {
    const h = get().hiddenCats;
    set({ hiddenCats: h.includes(c) ? h.filter((x) => x !== c) : [...h, c] });
  },
  setResearch: (c, on) => {
    const research = { ...get().research, [c]: on };
    set({ research });
    try {
      localStorage.setItem(RESEARCH_KEY, JSON.stringify(research));
    } catch {
      // private mode: the choice just isn't remembered
    }
  },
  health: null,
  basemap: "streets",
  templateFallback: true,
  setTemplateFallback: (templateFallback) => set({ templateFallback }),
  year: null,
  setYear: (year) => set({ year }),
  camera: { kind: "us", n: 0 },
  error: null,
  setPanel: (panel) => set({ panel }),
  setFilters: (f) => {
    const prev = get().filters;
    set({ filters: { ...prev, ...f } });
    // chip, search and sort are applied in the list; only these change what the API returns
    if (SERVER_KEYS.some((k) => k in f && f[k] !== prev[k])) void get().refreshResults();
  },
  refreshResults: async () => {
    // failed runs aren't saved by the API, so keep their own events
    if (run.phase !== "done" || run.ok === false) return;
    const ticket = ++requests;
    try {
      const [r, research] = await Promise.all([api.overlaps(get().filters), api.research(get().filters)]);
      if (ticket !== requests) return;
      set({ results: r.overlaps, visibleProjects: r.visible_projects, others: research.links, error: null });
    } catch (e) {
      set({ error: String(e) });
    }
  },
  flyTo: (c) => set((s) => ({ camera: { ...c, n: s.camera.n + 1 } as UIState["camera"] })),
  setBasemap: (basemap) => set({ basemap }),
  railTab: "agents",
  railOpen: true,
  railMode: "full",
  selectRailTab: (railTab) => {
    const s = get();
    set(s.railOpen && s.railTab === railTab ? { railOpen: false } : { railTab, railOpen: true });
    saveRail(get());
  },
  closeRail: () => {
    set({ railOpen: false });
    saveRail(get());
  },
  stepRail: () => {
    set((s) => ({ railMode: NEXT_RAIL[s.railMode] }));
    saveRail(get());
  },
  showRail: () => {
    set({ railMode: "full" });
    saveRail(get());
  },
}));

let source: EventSource | null = null;

function follow(runId: string): void {
  source?.close();
  source = new EventSource(`${API}/api/runs/${runId}/events`);
  source.onmessage = (m) => {
    const e = JSON.parse(m.data) as RunEvent;
    apply(e);
    // zoom into the Savannah River while connections are found, then back out
    if (e.type === "agent.spawned" && e.agent_id === "overlap") useUI.getState().flyTo({ kind: "river" });
    if (e.type === "run.done") useUI.getState().flyTo({ kind: "border" });
    if (e.type === "run.done") void useUI.getState().refreshResults();
  };
  source.addEventListener("end", () => {
    source?.close();
    source = null;
  });
  source.onerror = () => {
    if (source?.readyState === EventSource.CLOSED) useUI.setState({ error: "Lost the event stream. Try a replay." });
  };
}

export async function startRun(mode: "live" | "replay"): Promise<void> {
  const ui = useUI.getState();
  source?.close();
  reset(mode);
  useUI.setState({ results: null, others: null, visibleProjects: null, panel: { kind: "list" }, error: null });
  ui.flyTo({ kind: "border" });
  try {
    const { run_id } = await api.startRun(mode, CATEGORIES.filter((c) => ui.research[c]), 1, ui.templateFallback);
    follow(run_id);
  } catch (e) {
    run.phase = "failed";
    run.failMsg = String(e);
    useUI.setState({ error: String(e) });
  }
}

export async function skipToResults(): Promise<void> {
  if (run.runId && run.phase === "running") await api.skip(run.runId);
}

export async function boot(): Promise<void> {
  useUI.setState(savedRail());
  try {
    const [health, graph] = await Promise.all([
      api.health(),
      fetch(`${API}/api/agents`).then((r) => r.json() as Promise<{ agents: AgentSpec[]; sources: SourceSpec[] }>),
    ]);
    useUI.setState({ health, research: savedResearch() });
    setAgentSpecs(graph.agents);
  } catch (e) {
    useUI.setState({ error: `Can't reach the API at ${API || "this site"}/api (${e}).` });
  }
}

export async function showLatestResults(): Promise<void> {
  const ui = useUI.getState();
  try {
    const [projects, checks, reference, ov, graph, research, report] = await Promise.all([
      api.projects(), api.checks(), api.reference(), api.overlaps(ui.filters),
      fetch(`${API}/api/agents?of=latest`).then((r) => r.json() as Promise<{ agents: AgentSpec[] }>),
      api.research(ui.filters), api.report(),
    ]);
    hydrate({ projects, checks, reference, overlaps: ov.overlaps, specs: graph.agents, research, report });
    useUI.setState({ results: ov.overlaps, others: research.links, visibleProjects: ov.visible_projects, panel: { kind: "list" } });
    ui.flyTo({ kind: "border" });
  } catch (e) {
    useUI.setState({ error: String(e) });
  }
}

export async function refreshAgents(): Promise<void> {
  if (run.phase !== "idle") return; // the new Reader shows up in the next run's graph
  try {
    const graph = await fetch(`${API}/api/agents`).then((r) => r.json() as Promise<{ agents: AgentSpec[] }>);
    setAgentSpecs(graph.agents);
  } catch (e) {
    useUI.setState({ error: `Can't reach the API (${e}).` });
  }
}
