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
  partial: "Partly verified",
  town: "Town-level",
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
  jev: "Jev",
  "jev-mock": "Jev (mock)",
  heuristic: "Rule",
  osm: "OSM",
  sponsor_file: "Benchmark",
  override: "Human",
  template: "Template",
  research_file: "Research",
};

export const utilityName = (p: Project) => (p.utility === "DESC" ? "Dominion Energy SC" : `Georgia · ${p.sponsor}`);
// list rows drop the owner prefix (the diamond and tooltip carry it) and use en-dashes
export const shortName = (p: Project) => p.name.replace(/^(SAV|GTC|MEAG|DU|CC)\s*[-:]\s*/i, "").replace(/\s-\s/g, " – ");

export const OWNER: Record<string, string> = { GPC: "Georgia Power", SAV: "Georgia Power (Savannah)", GTC: "Georgia Transmission",
  MEAG: "MEAG Power", DU: "Dalton Utilities", DESC: "Dominion Energy SC" };

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

export const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

// one engine token per decider, defined in app/globals.css
export function engineColor(engine: string): string {
  if (engine.includes("Jev") && engine.includes("Gemini")) return "var(--mixed)";
  if (engine.includes("Jev")) return "var(--jev)";
  if (engine.includes("Gemini")) return "var(--gemini)";
  if (engine.includes("OSM")) return "var(--osm)";
  return "var(--code)";
}
