// Who owns a project and which color it gets. Dominion and Georgia are built in; plans submitted in the
// Sources menu are peers and take the peer colors in the order the run lists them (stable for the whole run).

import { run } from "./run";
import type { Project } from "./types";

export type Slot = "desc" | "gpc" | "p1" | "p2" | "p3";
const GA_OWNERS: Record<string, string> = {
  GPC: "Georgia Power", SAV: "Georgia Power (Savannah)", GTC: "Georgia Transmission", MEAG: "MEAG Power", DU: "Dalton Utilities",
};

let cacheKey = "";
let cache: { key: string; name: string; slot: Slot }[] = [];

// Submitted owners in this run, each with its color slot. Colors repeat after three owners.
export function peers(): { key: string; name: string; slot: Slot }[] {
  const fromSources = run.sourceOrder.map((id) => run.sources[id]).filter((s) => s?.owner_key);
  const key = fromSources.length ? run.sourceOrder.join(",") : `p:${Object.keys(run.projects).length}`;
  if (key === cacheKey) return cache;
  const names = new Map<string, string>();
  for (const s of fromSources) names.set(s.owner_key!, s.label);
  if (!fromSources.length) {  // results mode or an older recording: fall back to the projects themselves
    for (const p of Object.values(run.projects)) if (p.utility !== "DESC" && p.utility !== "GA") names.set(p.utility, p.sponsor);
  }
  const keys = fromSources.length ? [...names.keys()] : [...names.keys()].sort();
  cache = keys.map((k, i) => ({ key: k, name: names.get(k)!, slot: `p${(i % 3) + 1}` as Slot }));
  cacheKey = key;
  return cache;
}

export function slotOf(utility: string): Slot {
  if (utility === "DESC") return "desc";
  if (utility === "GA") return "gpc";
  return peers().find((x) => x.key === utility)?.slot ?? "p1";
}

export const ownClass = (p: Project) => `own-${slotOf(p.utility)}`;

export function ownerName(p: Project): string {
  if (p.utility === "DESC") return "Dominion Energy SC";
  if (p.utility === "GA") return GA_OWNERS[p.sponsor] ?? p.sponsor;
  return p.sponsor;
}

export function ownerShort(p: Project): string {
  if (p.utility === "DESC") return "Dominion";
  if (p.utility === "GA") return "Georgia";
  return p.sponsor;
}

// Georgia's plan gives a need date; everyone else an in-service date.
export const dateWord = (p: Project) => (p.utility === "GA" ? "Needed by" : "In service");

// A submitted plan may give only a year or month; say so instead of printing a made-up day.
export const approxDate = (p: Project | undefined) => Boolean(p && p.date_precision && p.date_precision !== "day");

export function serviceDate(p: Project, fmt: (iso: string) => string): string {
  if (p.date_precision === "year") return `${p.in_service_date.slice(0, 4)} (year only)`;
  if (p.date_precision === "month") {
    const [y, m] = p.in_service_date.split("-");
    return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
  }
  return fmt(p.in_service_date);
}

// "about 730" when either side's date is only a year or month.
export const gapText = (days: number, a?: Project, b?: Project) =>
  `${approxDate(a) || approxDate(b) ? "about " : ""}${days.toLocaleString()}`;

// Where a project came from: a page of a filing, or a row of a submitted spreadsheet.
export const whereFrom = (p: Project) =>
  p.utility === "DESC" || p.utility === "GA" ? `${p.source_file}, page ${p.source_page} (${p.source_ref})` : `${p.source_file}, ${p.source_ref}`;
