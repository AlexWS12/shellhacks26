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
  utility: string; // "DESC" | "GA" | a submitted owner's key
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
  distance_mi: number;
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

export interface Shared {
  timing: "concurrent" | "sequential" | "unknown";
  items: string[];
  level: "high" | "medium" | "low";
  types: string[];
}

export interface CostBlock {
  desc_cost: number | null;
  ga_cost: null;
  desc_miles: number | null;
  desc_cost_per_mile: number | null;
  savings: number | null;
  source: string | null;
  statement: string;
  shared?: Shared;
}

export interface Written {
  text: string;
  actor: string;
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
  time_gap_days: number;
  built_at_same_time: boolean | null;
  location: Confidence;
  benchmark_pair: boolean;
  a: ReportSide;
  b: ReportSide;
  shared_level: string | null;
  shared_items: string[];
  timing: string | null;
  a_cost: number | null;
  savings: number | null;
  cost_source: string | null;
  analysis: Written | null;
  joint_agenda: Written | null;
  other_utilities: { owner: string; name: string; category: string; miles_to_a: number; miles_to_b: number; in_service: string | null; sources: number }[];
}

export interface Report {
  title: string;
  as_of: string;
  owners: Record<string, number>;
  counts: Record<string, number>;
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
