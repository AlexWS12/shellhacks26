
import { create } from "zustand";

import { api, API, type FilterState } from "./api";
import { apply, hydrate, reset, run, setAgentSpecs } from "./run";
import type { AgentSpec, Health, Overlap, RunEvent, SourceSpec } from "./types";

export type Panel =
  | { kind: "list" }
  | { kind: "pair"; a: string; b: string }
  | { kind: "project"; id: string }
  | { kind: "agent"; id: string };

export type Camera = { kind: "us" } | { kind: "border" } | { kind: "pair"; a: string; b: string } | { kind: "project"; id: string };

interface UIState {
  panel: Panel;
  filters: FilterState;
  results: Overlap[] | null; // from the API after a run
  visibleProjects: number | null;
  health: Health | null;
  basemap: "streets" | "simple";
  year: number | null; // timeline: only show work active in this year
  setYear: (y: number | null) => void;
  camera: Camera & { n: number }; // n lets you fly to the same place twice
  error: string | null;
  setPanel: (p: Panel) => void;
  setFilters: (f: Partial<FilterState>) => void;
  refreshResults: () => Promise<void>;
  flyTo: (c: Camera) => void;
  setBasemap: (b: UIState["basemap"]) => void;
}

let requests = 0; // only the newest filter request wins

export const useUI = create<UIState>((set, get) => ({
  panel: { kind: "list" },
  filters: { allSponsors: false, townLevel: true, hideFinished: false, sort: "distance" },
  results: null,
  visibleProjects: null,
  health: null,
  basemap: "streets",
  year: null,
  setYear: (year) => set({ year }),
  camera: { kind: "us", n: 0 },
  error: null,
  setPanel: (panel) => set({ panel }),
  setFilters: (f) => {
    set({ filters: { ...get().filters, ...f } });
    void get().refreshResults();
  },
  refreshResults: async () => {
    // failed runs aren't saved by the API, so keep their own events
    if (run.phase !== "done" || run.ok === false) return;
    const ticket = ++requests;
    try {
      const r = await api.overlaps(get().filters);
      if (ticket !== requests) return;
      set({ results: r.overlaps, visibleProjects: r.visible_projects, error: null });
    } catch (e) {
      set({ error: String(e) });
    }
  },
  flyTo: (c) => set((s) => ({ camera: { ...c, n: s.camera.n + 1 } as UIState["camera"] })),
  setBasemap: (basemap) => set({ basemap }),
}));

let source: EventSource | null = null;

function follow(runId: string): void {
  source?.close();
  source = new EventSource(`${API}/api/runs/${runId}/events`);
  source.onmessage = (m) => {
    const e = JSON.parse(m.data) as RunEvent;
    apply(e);
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
  useUI.setState({ results: null, visibleProjects: null, panel: { kind: "list" }, error: null });
  ui.flyTo({ kind: "border" });
  try {
    const { run_id } = await api.startRun(mode);
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
  try {
    const [health, graph] = await Promise.all([
      api.health(),
      fetch(`${API}/api/agents`).then((r) => r.json() as Promise<{ agents: AgentSpec[]; sources: SourceSpec[] }>),
    ]);
    useUI.setState({ health });
    setAgentSpecs(graph.agents);
  } catch (e) {
    useUI.setState({ error: `Can't reach the API at ${API || "this site"}/api (${e}).` });
  }
}

export async function showLatestResults(): Promise<void> {
  const ui = useUI.getState();
  try {
    const [projects, checks, reference, ov, graph] = await Promise.all([
      api.projects(), api.checks(), api.reference(), api.overlaps(ui.filters),
      fetch(`${API}/api/agents`).then((r) => r.json() as Promise<{ agents: AgentSpec[] }>),
    ]);
    hydrate({ projects, checks, reference, overlaps: ov.overlaps, specs: graph.agents });
    useUI.setState({ results: ov.overlaps, visibleProjects: ov.visible_projects, panel: { kind: "list" } });
    ui.flyTo({ kind: "border" });
  } catch (e) {
    useUI.setState({ error: String(e) });
  }
}
