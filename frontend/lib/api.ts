import type {
  Check, Health, Overlap, PairDetail, Project, ReferenceResult, Report, ResearchCategory, ResearchProject, SubmissionView, ThirdParty,
} from "./types";

// Dev talks to localhost:8000. Production calls /api on the same domain.
const DEFAULT_API = process.env.NODE_ENV === "development" ? "http://localhost:8000" : "";
export const API = (process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API).replace(/\/$/, "");

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

// For the Sources menu: errors carry the server's own message ("Choose a column for: ...").
async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method, headers: body ? { "content-type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const detail = await r.json().then((j: { detail?: unknown }) => j.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : `${path}: HTTP ${r.status}`);
  }
  return (await r.json()) as T;
}

export interface SubmissionPreview { columns: string[]; rows: string[][]; total_rows: number }
export interface SubmissionMenu {
  submissions: SubmissionView[];
  fields: Record<string, { label: string; required: boolean }>;
  limits: { max_mb: number; max_plans: number };
  gemini: boolean;
}

export interface FilterState {
  allSponsors: boolean;
  townLevel: boolean;
  hideFinished: boolean;
  sort: "distance" | "gap" | "strength"; // the API sorts distance | gap; strength is sorted in the list
  chip: "all" | "same" | "bench" | "verified"; // list-only, never sent to the API
  q: string; // list-only search text
}

export function filterQuery(f: FilterState): string {
  const q = new URLSearchParams({
    all_sponsors: String(f.allSponsors),
    min_conf: f.townLevel ? "town" : "confirmed_osm",
    hide_finished: String(f.hideFinished),
    sort: f.sort === "gap" ? "gap" : "distance",
  });
  return q.toString();
}

export const api = {
  health: () => get<Health>("/api/health"),
  projects: () => get<Project[]>("/api/projects"),
  checks: () => get<Check[]>("/api/checks"),
  reference: () => get<ReferenceResult[]>("/api/reference-test"),
  overlaps: (f: FilterState) =>
    get<{ overlaps: Overlap[]; visible_projects: number; total_projects: number }>(`/api/overlaps?${filterQuery(f)}`),
  pair: (a: string, b: string) => get<PairDetail>(`/api/pair/${encodeURIComponent(a)}/${encodeURIComponent(b)}`),
  runs: () => get<{ recorded: { run_id: string; complete: boolean; ok: boolean; seconds: number }[] }>("/api/runs"),
  research: (f: FilterState) =>
    get<{ selected: ResearchCategory[]; records: ResearchProject[]; links: ThirdParty[] }>(`/api/research?${filterQuery(f)}`),
  startRun: async (mode: "live" | "replay", research: ResearchCategory[], speed = 1, templates = true): Promise<{ run_id: string }> => {
    const r = await fetch(`${API}/api/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode, speed, templates, research }),
    });
    if (!r.ok) throw new Error(`start run: HTTP ${r.status} ${await r.text()}`);
    return r.json();
  },
  skip: (runId: string) => fetch(`${API}/api/runs/${runId}/skip`, { method: "POST" }),
  exportUrl: (f: FilterState, kind: "xlsx" | "csv") => `${API}/api/export.${kind}?${filterQuery(f)}`,
  report: async (): Promise<Report | null> => {
    const r = await fetch(`${API}/api/report`);
    if (r.status === 404) return null; // no report yet (older run)
    if (!r.ok) throw new Error(`/api/report: HTTP ${r.status}`);
    return (await r.json()) as Report;
  },
  reportUrl: `${API}/api/report.md`,
  submissions: () => get<SubmissionMenu>("/api/submissions"),
  addSubmission: (body: Record<string, unknown>) =>
    send<{ submission: SubmissionView; preview?: SubmissionPreview; suggested?: Record<string, string> }>("POST", "/api/submissions", body),
  submissionPreview: (id: string) =>
    get<{ submission: SubmissionView; preview: SubmissionPreview; suggested: Record<string, string> }>(`/api/submissions/${encodeURIComponent(id)}/preview`),
  setMapping: (id: string, mapping: Record<string, string>) =>
    send<{ submission: SubmissionView; rows: number; usable: number; skipped: string[] }>("PUT", `/api/submissions/${encodeURIComponent(id)}/mapping`, { mapping }),
  removeSubmission: (id: string) => send<{ ok: boolean }>("DELETE", `/api/submissions/${encodeURIComponent(id)}`),
  eventsUrl: (runId: string) => `${API}/api/runs/${runId}/events`,
};
