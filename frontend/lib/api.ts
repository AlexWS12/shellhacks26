import type {
  Check, ChatReply, ChatTurn, Health, ModelList, Overlap, PairDetail, Project, ReferenceResult, Report, ResearchCategory,
  ResearchProject, SetupConfig, Estimate, Review, SetupProblem, SetupProvider, SetupRef, SetupResult, SourceView,
  SubmissionView, ThirdParty,
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

// An API error that keeps the server's details: the run check sends which jobs have no working model.
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail: unknown) {
    super(message);
  }
}

async function fail(r: Response, path: string): Promise<never> {
  const detail = await r.json().then((j: { detail?: unknown }) => j.detail).catch(() => null);
  const msg = typeof detail === "string" ? detail
    : detail && typeof detail === "object" && "message" in detail ? String((detail as { message: unknown }).message)
      : `${path}: HTTP ${r.status}`;
  throw new ApiError(msg, r.status, detail);
}

// The setup screen's passcode (hosted mode only), kept for this browser tab.
const PASSCODE_KEY = "tandem.passcode";
let passcode = "";
try {
  passcode = typeof sessionStorage !== "undefined" ? sessionStorage.getItem(PASSCODE_KEY) ?? "" : "";
} catch {
  // storage blocked: the passcode is asked again
}
export function setPasscode(p: string): void {
  passcode = p;
  try {
    sessionStorage.setItem(PASSCODE_KEY, p);
  } catch {
    // not remembered; fine
  }
}

async function setupCall<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (passcode) headers["x-admin-passcode"] = passcode;
  const r = await fetch(`${API}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) return fail(r, path);
  return (await r.json()) as T;
}

// An image (or other file) behind the passcode: fetched with the header, handed back as an object URL.
async function setupBlob(path: string): Promise<string> {
  const r = await fetch(`${API}${path}`, { headers: passcode ? { "x-admin-passcode": passcode } : {} });
  if (!r.ok) return fail(r, path);
  return URL.createObjectURL(await r.blob());
}

export interface PreflightDetail { message: string; problems: SetupProblem[]; can_force: boolean }

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
  sources: () => get<{ sources: SourceView[]; bbox: [number, number, number, number] }>("/api/sources"),
  projects: () => get<Project[]>("/api/projects"),
  checks: () => get<Check[]>("/api/checks"),
  reference: () => get<ReferenceResult[]>("/api/reference-test"),
  overlaps: (f: FilterState) =>
    get<{ overlaps: Overlap[]; visible_projects: number; total_projects: number }>(`/api/overlaps?${filterQuery(f)}`),
  pair: (a: string, b: string) => get<PairDetail>(`/api/pair/${encodeURIComponent(a)}/${encodeURIComponent(b)}`),
  runs: () => get<{ recorded: { run_id: string; complete: boolean; ok: boolean; seconds: number }[] }>("/api/runs"),
  research: (f: FilterState) =>
    get<{ selected: ResearchCategory[]; records: ResearchProject[]; links: ThirdParty[] }>(`/api/research?${filterQuery(f)}`),
  // Ask a question about the finished run. 409 before a run, 503 when no model is set up for questions.
  chat: (question: string, history: ChatTurn[]) => send<ChatReply>("POST", "/api/chat", { question, history }),
  // A live run is refused with 409 (ApiError, detail: PreflightDetail) when a job has no working model.
  startRun: async (mode: "live" | "replay", research: ResearchCategory[], speed = 1, templates = true, force = false): Promise<{ run_id: string }> => {
    const r = await fetch(`${API}/api/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode, speed, templates, research, force }),
    });
    if (!r.ok) return fail(r, "start run");
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
  reportPdfUrl: `${API}/api/report.pdf`,
  submissions: () => get<SubmissionMenu>("/api/submissions"),
  addSubmission: (body: Record<string, unknown>) =>
    send<{ submission: SubmissionView; preview?: SubmissionPreview; suggested?: Record<string, string> }>("POST", "/api/submissions", body),
  submissionPreview: (id: string) =>
    get<{ submission: SubmissionView; preview: SubmissionPreview; suggested: Record<string, string> }>(`/api/submissions/${encodeURIComponent(id)}/preview`),
  setMapping: (id: string, mapping: Record<string, string>) =>
    send<{ submission: SubmissionView; rows: number; usable: number; skipped: string[] }>("PUT", `/api/submissions/${encodeURIComponent(id)}/mapping`, { mapping }),
  removeSubmission: (id: string) => send<{ ok: boolean }>("DELETE", `/api/submissions/${encodeURIComponent(id)}`),
  eventsUrl: (runId: string) => `${API}/api/runs/${runId}/events`,
  // The Sources menu (admin in hosted mode, like the model setup)
  admin: {
    meta: () => get<{ states: string[]; max_upload_mb: number; page_images: boolean }>("/api/sources/meta"),
    upload: (filename: string, content_b64: string) =>
      setupCall<{ upload_id: string; filename: string; size: number; pages: number; has_text: boolean }>(
        "POST", "/api/sources/upload", { filename, content_b64 }),
    create: (body: { upload_id: string; filename: string; display_name: string; code: string; states: string[]; operators: string[]; pages: string | null }) =>
      setupCall<{ source: SourceView }>("POST", "/api/sources", body),
    estimate: (id: string, pages?: string | null) =>
      setupCall<{ estimate: Estimate | null; allowed: boolean; message?: string }>("GET", `/api/sources/${id}/estimate${pages ? `?pages=${encodeURIComponent(pages)}` : ""}`),
    extract: (id: string, pages?: string | null) => setupCall<{ run_id: string; estimate: Estimate }>("POST", `/api/sources/${id}/extract`, { pages: pages ?? null }),
    review: (id: string) => setupCall<Review>("GET", `/api/sources/${id}/review`),
    edit: (id: string, pid: string, field: string, value: unknown) =>
      setupCall<Review>("PATCH", `/api/sources/${id}/review/${encodeURIComponent(pid)}`, { field, value }),
    decide: (id: string, status: "accepted" | "rejected" | "pending", project_id?: string) =>
      setupCall<Review>("POST", `/api/sources/${id}/review/decide`, { status, project_id: project_id ?? null }),
    activate: (id: string) => setupCall<{ run_id: string; projects: number }>("POST", `/api/sources/${id}/activate`),
    deactivate: (id: string) => setupCall<{ removed_projects: number }>("POST", `/api/sources/${id}/deactivate`),
    remove: (id: string) => setupCall<{ removed_projects: number }>("DELETE", `/api/sources/${id}`),
    pageText: (id: string, n: number) => setupCall<{ page: number; text: string }>("GET", `/api/sources/${id}/pages/${n}/text`),
    pageImage: (id: string, n: number) => setupBlob(`/api/sources/${id}/pages/${n}.png`),
  },
  setup: {
    config: () => setupCall<SetupConfig>("GET", "/api/models/config"),
    models: (provider: string) => setupCall<ModelList>("GET", `/api/models/providers/${encodeURIComponent(provider)}/models`),
    validate: (models: SetupRef[]) => setupCall<{ results: SetupResult[] }>("POST", "/api/models/validate", { models, fresh: true }),
    save: (roles: Record<string, SetupRef[]>) =>
      setupCall<{ saved: boolean; warnings: string[]; results: SetupResult[]; changed: string[] }>("PUT", "/api/models/config", { roles }),
    reset: () => setupCall<{ reset: boolean }>("DELETE", "/api/models/config"),
    keys: (provider: string, values: Record<string, string>, host?: string) =>
      setupCall<{ status: string; plain: string; saved: boolean; warning?: string; providers: SetupProvider[] }>(
        "PUT", "/api/models/keys", { provider, values, host }),
  },
};
