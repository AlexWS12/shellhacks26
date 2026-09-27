// Same check as `visible` in backend/app/core/overlap.py. Only decides what to draw.

import type { FilterState } from "./api";
import type { Project, ResearchProject } from "./types";

const ORDER = ["verified", "confirmed_osm", "partial", "town", "unlocated"];

export function visible(p: Project, f: FilterState, today: string): boolean {
  if (p.lat == null || p.lon == null) return false;
  if (p.utility === "GA" && !f.allSponsors && !["GPC", "SAV"].includes(p.sponsor)) return false;
  const min = f.townLevel ? "town" : "confirmed_osm";
  if (ORDER.indexOf(p.location_confidence) > ORDER.indexOf(min)) return false;
  if (f.hideFinished && p.in_service_date < today) return false;
  return true;
}

// A project counts as active from its build start (or in-service year if unknown) through its in-service year.
export function activeIn(p: Project, year: number | null): boolean {
  if (year == null) return true;
  const end = Number(p.in_service_date.slice(0, 4));
  const from = p.build_start ?? p.build_active_from;
  const start = from ? Number(from.slice(0, 4)) : end;
  return start <= year && year <= end;
}

// Other utilities' work: same idea. Unknown dates stay visible, since timing is unknown, not absent.
export function researchActiveIn(r: ResearchProject, year: number | null): boolean {
  if (year == null) return true;
  const end = r.in_service_date ? Number(r.in_service_date.slice(0, 4)) : null;
  const start = r.start_date ? Number(r.start_date.slice(0, 4)) : end;
  if (start == null && end == null) return true;
  return (start ?? -Infinity) <= year && year <= (end ?? Infinity);
}
