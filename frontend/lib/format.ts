import { namePrefixes, sourceOf, useSources } from "./owners";
import type { Confidence, Project, ResearchCategory } from "./types";

export const CATEGORY_LABEL: Record<ResearchCategory, string> = {
  electric: "Electric",
  gas: "Gas",
  roads_water: "Roads & water",
};

// A research date as precise as its source: '2028', 'Jun 2027' or 'Oct 1, 2026'.
export function statedDate(s: string | null | undefined): string {
  if (!s) return "unknown";
  const [y, m, d] = s.split("-");
  if (d) return fmtDate(s);
  if (m) return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
  return y;
}

export const CONF_LABEL: Record<Confidence, string> = {
  verified: "Verified location",
  confirmed_osm: "Confirmed in OpenStreetMap",
  partial: "Partly located",
  town: "Approximate",
  unlocated: "No location",
};

export const TYPE_LABEL: Record<string, string> = {
  new_line: "New line",
  rebuild: "Line rebuild",
  substation_construction: "Substation build",
  in_substation_equipment: "In-substation equipment",
  other: "Other",
};

export const ACTOR_LABEL: Record<string, string> = {
  code: "Code",
  gemini: "Gemini",
  claude: "Claude",
  openai: "OpenAI",
  jev: "Jev",
  "jev-mock": "Jev (mock)",
  heuristic: "Rule",
  osm: "OSM",
  sponsor_file: "Benchmark",
  override: "Human",
  template: "Template",
  research_file: "Research",
};

// The owner line of a project card: a built-in filing by name (with the row's owner when the filing lists several),
// an added plan as such.
export function utilityName(p: Project): string {
  const s = sourceOf(p);
  if (!s?.builtin) return `${p.sponsor} · submitted plan`;
  return s.sponsors.length > 1 ? `${s.display.short_name ?? s.display_name} · ${p.sponsor}` : s.display.ui_name ?? s.display_name;
}

// list rows drop the owner prefix a filing puts in its names ('SAV: ...'; the marker and tooltip carry it) and use
// en-dashes. 'CC' is a name prefix, not an owner.
export function shortName(p: Project): string {
  const codes = [...namePrefixes(useSources.getState().list), "CC"];
  return p.name.replace(new RegExp(`^(${codes.join("|")})\\s*[-:]\\s*`, "i"), "").replace(/\s-\s/g, " – ");
}

export function money(n: number | null | undefined): string {
  if (n == null) return "redacted";
  return n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}K`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// "5 mo" under a year, "1.4 yrs" after that
export const gapLabel = (days: number) => (days < 365 ? `${Math.round(days / 30.4)} mo` : `${(days / 365).toFixed(1)} yrs`);

// the providers whose models write text (as opposed to Jev's typed decisions or code)
export const WRITERS = new Set(["gemini", "claude", "openai"]);

export const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

// one engine token per decider, defined in app/globals.css
export function engineColor(engine: string): string {
  if (engine.includes("Jev") && engine.includes("Gemini")) return "var(--mixed)";
  if (engine.includes("Jev")) return "var(--jev)";
  if (engine.includes("Gemini")) return "var(--gemini)";
  if (engine.includes("OSM")) return "var(--osm)";
  return "var(--code)";
}
