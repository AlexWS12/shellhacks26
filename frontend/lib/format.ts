import type { Confidence, Project } from "./types";

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
};

export const utilityName = (p: Project) => (p.utility === "DESC" ? "Dominion Energy SC" : `Georgia · ${p.sponsor}`);
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

export const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

// one engine token per decider, defined in app/globals.css
export function engineColor(engine: string): string {
  if (engine.includes("Jev") && engine.includes("Gemini")) return "var(--mixed)";
  if (engine.includes("Jev")) return "var(--jev)";
  if (engine.includes("Gemini")) return "var(--gemini)";
  if (engine.includes("OSM")) return "var(--osm)";
  return "var(--code)";
}
