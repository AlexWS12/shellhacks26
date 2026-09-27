// Keep in sync with backend/app/core/models.py and CLAUDE.md.

export type Confidence = "verified" | "confirmed_osm" | "partial" | "town" | "unlocated";

export interface Endpoint {
  name: string;
  lat: number | null;
  lon: number | null;
  method: string;
  confidence: Confidence;
  evidence: Record<string, unknown>;
  role?: "endpoint" | "context"; // context: a place the description names, used only to place the project
}

export interface Project {
  id: string;
  utility: string; // "DESC" | "GA" | a submitted owner's key: the source's utility_key
  source_id?: string | null; // row in the sources table (GET /api/sources)
  provenance?: Record<string, unknown> | null; // AI reader: field -> {page, snippet} (and human_override)
  state?: string | null;
  date_precision?: "day" | "month" | "year" | null;
  sponsor: string;
  name: string;
  description: string;
  need_text: string;
  status: string;
  in_service_date: string;
  in_service_raw: string;
  build_start: string | null;
  build_active_from: string | null;
  cost_total: number | null;
  cost_by_year: Record<string, number | null> | null;
  miles: number | null;
  zone: string | null;
  project_type: string | null;
  project_type_actor: string | null;
  endpoints: Endpoint[];
  lat: number | null;
  lon: number | null;
  location_confidence: Confidence;
  source_file: string;
  source_page: number;
  source_ref: string;
  extracted_by: string;
  sponsor_ref_id: string | null;
}

export interface Check {
  id: string;
  level: "error" | "warn" | "info";
  rule: string;
  title: string;
  detail: string;
  source: string;
  project_id: string | null;
  actor: string;
}

export interface Overlap {
  id: string;
  project_a: string;
  project_b: string;
  distance_mi: number; // closest points between the two projects
  tier?: Tier; // what that distance lets them share; older recorded runs don't carry it
  center_mi?: number | null; // center to center, the benchmark's rule
  time_gap_days: number;
  windows_overlap: boolean | null;
  pair_confidence: Confidence;
  in_sponsor_sample: boolean;
  finished?: boolean; // either project already in service; older recorded runs don't carry it
  distance_slack_mi?: number; // how far the distance could move if unlocated ends were found
  rank: number;
}

export interface ReferenceResult {
  overlap_id: string;
  a: string;
  b: string;
  a_project: string | null;
  b_project: string | null;
  expected_mi: number;
  got_mi: number | null;
  expected_days: number;
  got_days: number | null;
  passed: boolean;
  blind_mi?: number | null; // the same pair from our own geocoding, without the file's coordinates
  blind_passed?: boolean | null;
}

// The challenge's distance tiers: touching or crossing, under 1 mi, under 5 mi, under 25 mi.
export type Tier = "touching" | "row" | "site" | "crew";

export interface Shared {
  timing: "concurrent" | "sequential" | "unknown";
  tier?: Tier;
  must_coordinate?: boolean;
  items: string[];
  level: "high" | "medium" | "low";
  types: string[];
}

// One project's cost: from its filing, published on the web (quote checked by Jev), or a Dominion benchmark.
export interface CostEstimate {
  project_id: string;
  amount: number;
  basis: "filed" | "published" | "benchmark";
  source: string;
  source_title?: string;
  quote?: string;
  method: string;
  check: { actor: string; p: number } | null;
}

// Savings for one pair: an assumption range on the smaller project's cost, unless Jev rules the pair out.
export interface CostBlock {
  a: CostEstimate | null;
  b: CostEstimate | null;
  savings_low: number | null;
  savings_high: number | null;
  share: [number, number] | null;
  applies_to: string | null;
  for?: string[]; // what the pair's distance tier lets them share, given their timing
  tier?: Tier;
  together?: boolean; // both under construction at once (neither finished)
  assumption: string;
  check: { actor: string; p: number } | null;
  statement: string;
  shared?: Shared;
}

export interface Written {
  text: string;
  actor: string;
  model?: string | null; // the model that wrote it; null for a template
  unsupported_numbers?: string[];
}

export interface Brief {
  dominion?: Written;
  georgia?: Written;
  mediator?: Written;
}

export interface AgentSpec {
  id: string;
  name: string;
  role: string;
  actors: string[];
  depends_on: string[];
  kind: "agent" | "tool";
  engine: string;
  team?: "core" | "research";
  roles?: string[]; // the model jobs it calls (config/models.json); none for plain code
}

export interface SourceSpec {
  id: string;
  label: string;
  detail: string;
  file: string;
  total: number;
  owner_key?: string; // submitted plans: the owner their projects carry in Project.utility
}

export interface PairDetail {
  overlap: Overlap;
  a: Project;
  b: Project;
  shared: Shared;
  cost: CostBlock;
  analysis: Written | null;
  brief: Brief | null;
  others?: ThirdParty[];
}

// Other utilities' projects, found by the research team. Every record cites its sources.
export type ResearchCategory = "electric" | "gas" | "roads_water";

export interface Source {
  url: string;
  title: string;
  publisher: string;
  quote: string;
  accessed: string | null;
}

export interface ResearchPlace {
  name: string;
  kind: string;
  state: string | null;
  role: string;
}

export interface ResearchProject {
  id: string;
  category: ResearchCategory;
  utility: string;
  utility_kind: string;
  name: string;
  description: string;
  status: string;
  start: string | null; // as precise as the source
  in_service: string | null;
  date_quote: string | null;
  start_date: string | null;
  in_service_date: string | null;
  date_precision: "day" | "month" | "year" | null;
  places: ResearchPlace[];
  endpoints: Endpoint[];
  lat: number | null;
  lon: number | null;
  location_confidence: Confidence;
  miles: number | null;
  cost_usd: number | null;
  cost_quote: string | null;
  sources: Source[];
  found_by: string;
  verification: {
    verifiers?: number; confirmed?: number; quotes_found?: number; notes?: string[]; note?: string;
    merged_from?: { name: string; in_service: string | null; start: string | null; sources: string[] }[]; // same project, other scouts
  };
}

export interface ThirdParty {
  overlap_id: string;
  research_id: string;
  category: ResearchCategory;
  dist_a_mi: number;
  dist_b_mi: number;
  gap_a_days: number | null;
  gap_b_days: number | null;
  approx_date: boolean;
  confidence: Confidence;
}

export interface Health {
  status: string;
  dataset_run: string | null;
  projects: number;
  gemini: boolean;
  gemini_model: string;
  models?: RoleInfo; // each job's plain name and models, in fallback order
  app_mode?: "local" | "hosted";
  models_setup?: { ready: boolean; first_launch: boolean; problems: SetupProblem[]; error: string | null };
  jev: string;
  tiger: boolean;
  today: string;
}

export interface RunEvent {
  type: string;
  run_id: string;
  seq: number;
  ts: number;
  agent_id?: string;
  actor?: string;
  [key: string]: unknown;
}

// The Writer's final report. Every number is from code; summary and next steps are prose (see actor).
export interface ReportSide {
  id: string;
  name: string;
  owner: string;
  in_service: string;
  status: string;
  source: string;
  date_precision: "day" | "month" | "year";
}

export interface ReportTop {
  rank: number;
  overlap_id: string;
  distance_mi: number;
  center_mi?: number | null;
  tier?: Tier;
  time_gap_days: number;
  built_at_same_time: boolean | null;
  location: Confidence;
  benchmark_pair: boolean;
  a: ReportSide;
  b: ReportSide;
  shared_level: string | null;
  shared_items: string[];
  timing: string | null;
  a_cost: { amount: number; basis: CostEstimate["basis"]; source: string } | null;
  b_cost: { amount: number; basis: CostEstimate["basis"]; source: string } | null;
  savings_low: number | null;
  savings_high: number | null;
  analysis: Written | null;
  joint_agenda: Written | null;
  other_utilities: { owner: string; name: string; category: string; miles_to_a: number; miles_to_b: number; in_service: string | null; sources: number }[];
}

export interface Report {
  title: string;
  as_of: string;
  owners: Record<string, number>;
  counts: Record<string, number>;
  tiers?: Record<Tier, number>; // pairs by closest distance; older reports don't carry it
  research_categories: string[];
  top: ReportTop[];
  issues: { level: string; title: string; source: string }[];
  method: string[];
  summary: Written;
  next_steps: Written;
  markdown: string;
}

// A plan submitted in the Sources menu (see backend/app/store/submissions.py).
export interface SubmissionView {
  id: string;
  owner: string;
  label: string;
  owner_key: string;
  state: "SC" | "GA";
  kind: "spreadsheet" | "pdf" | "url";
  filename: string;
  url: string | null;
  size: number;
  created: string;
  columns: string[];
  mapping: Record<string, string>;
  status: "needs_mapping" | "ready";
}

// The model setup screen (/api/models/*). Keys never come back from the server, only whether one is set.
export interface SetupRef { provider: string; model: string }
export interface SetupResult extends SetupRef {
  status: string; // ok | NoKey | Off | AuthError | ModelNotFound | RateLimited | QuotaExceeded | Timeout | ...
  plain: string; // what the screen shows: "Works", "Key rejected", "Model name not found", ...
  message: string;
  ok: boolean;
  temporary: boolean; // busy or out of quota for now: trying later may work
}
export interface SetupField { var: string; label: string; secret: boolean; present?: boolean; source?: string | null }
export interface SetupProvider {
  id: string;
  label: string;
  what: string;
  get_key: string;
  kinds: string[];
  fields: SetupField[];
  key_present: boolean;
  can_enter_key: boolean;
  host?: string; // Jev: where it runs
  off_reason?: string | null;
  hosts?: { id: string; label: string; fields: SetupField[] }[];
}
export interface SetupRole {
  name: string;
  label: string;
  group: string;
  description: string;
  kind: string;
  not_used: string | null; // why runs don't need this job right now
  models: SetupRef[];
  default_models: SetupRef[];
  customized: boolean;
  providers: string[]; // which providers can do this job
}
export interface SetupConfig {
  mode: "local" | "hosted";
  providers: SetupProvider[];
  roles: SetupRole[];
  error: string | null;
  local_file: boolean;
  problems: SetupProblem[];
  max_models_per_role: number;
}
export interface SetupProblem { role: string; label: string; reason: string; tried?: SetupResult[]; temporary?: boolean }
export interface ModelList { provider: string; models: { id: string; label: string; description: string }[]; status: string; plain: string }

export type RoleInfo = Record<string, { kind: string; label?: string; models: string[] }>;

// A model event from a run (model.call_failed, model.fallback_used, role.exhausted), kept for the pipeline panel.
export interface ModelIssue {
  seq: number;
  type: "model.call_failed" | "model.fallback_used" | "role.exhausted";
  role: string;
  provider?: string;
  model?: string;
  errorClass?: string;
  from?: string;
  to?: string;
}

// A utility the pipeline knows (GET /api/sources): its name, code, color and how the UI draws it.
export interface SourceView {
  id: string;
  code: string;
  display_name: string;
  states: string[];
  osm_operator_patterns: string[];
  color: string;
  reader: string; // builtin:desc | builtin:gpc | sheet | ai
  status: "draft" | "extracting" | "review" | "active" | "failed";
  utility_key: string;
  position: number;
  sponsors: { code: string; name: string; default: boolean }[]; // owners inside one filing
  display: {
    short_name?: string;
    ui_name?: string;
    legend?: string; // the map legend's label
    card_label?: string;
    shape?: "circle" | "diamond";
    date_label?: string;
    costs?: "public" | "redacted" | "stated";
    show_status?: boolean;
    hq?: { lon: number; lat: number; label: string };
  };
  builtin: boolean;
  agent_id: string;
  file_path?: string | null;
  file_sha256?: string | null;
  projects?: number; // in the results on screen
  draft_projects?: number | null; // read by the AI reader, waiting for (or past) review
}

// The Sources menu: one field's provenance, as the AI reader recorded it (plus a person's edit).
export interface Cite { page?: number; snippet?: string; label?: string; human_override?: { value: unknown; previous: unknown; at: string } }
export interface ReviewRow {
  status: "pending" | "accepted" | "rejected";
  incomplete: string[];
  edited?: boolean;
  other_owner?: string; // a joint filing: the page names this utility as the owner, so it starts rejected
}
export interface Review {
  source: SourceView;
  projects: Project[];
  review: Record<string, ReviewRow>;
  confidence: Record<string, number>;
  counts: { pending: number; accepted: number; rejected: number };
  ready: boolean;
  candidates: { page: number; kind: string; reason: string }[];
  checks: Check[];
  estimate: Estimate;
  pages: number[];
}
export interface Estimate {
  model: string | null; pages: number; calls: number; input_tokens: number; output_tokens: number;
  usd: number | null; limit_usd: number | null; price_known: boolean;
}
