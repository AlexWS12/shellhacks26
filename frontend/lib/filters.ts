// Same check as `visible` in backend/app/core/overlap.py. Only decides what to draw.

import type { FilterState } from "./api";
import { hiddenByDefault } from "./owners";
import type { Project, ResearchProject } from "./types";

const ORDER = ["verified", "confirmed_osm", "partial", "town", "unlocated"];

export function visible(p: Project, f: FilterState, today: string): boolean {
  if (p.lat == null || p.lon == null) return false;
  if (!f.allSponsors && hiddenByDefault(p)) return false; // owners shown by default come from /api/sources
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

// Same limit as span_limit in backend/app/core/overlap.py: 60 mi unless the filing states a longer line.
export const MAX_SPAN_MI = 60;
const spanLimit = (miles: number | null) => (miles ? Math.max(MAX_SPAN_MI, miles * 1.1 + 5) : MAX_SPAN_MI);

function miles(a: [number, number], b: [number, number]): number {
  const r = Math.PI / 180;
  const h = Math.sin(((b[0] - a[0]) * r) / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(((b[1] - a[1]) * r) / 2) ** 2;
  return 2 * 3958.8 * Math.asin(Math.sqrt(h));
}

// The title's located ends. Places named only in the description never make a line.
export function lineEnds(p: Project) {
  return p.endpoints.filter((e) => e.lat != null && e.lon != null && e.role !== "context");
}

// Whether the map may draw a line between the two located endpoints, and why not.
// A span past the limit means one end is misplaced. A filing that names far fewer miles than the
// span means the work is a short piece of the line, somewhere we don't know, so the whole line would mislead.
export function lineCheck(p: Project): { draw: boolean; span: number | null; why: string } {
  const eps = lineEnds(p);
  if (eps.length !== 2) return { draw: false, span: null, why: "" };
  const span = miles([eps[0].lat!, eps[0].lon!], [eps[1].lat!, eps[1].lon!]);
  if (span > spanLimit(p.miles)) return { draw: false, span, why: `ends are ${span.toFixed(0)} mi apart, more than any single line, so one is likely misplaced` };
  if (p.miles != null && span > Math.max(3 * p.miles, p.miles + 15)) {
    return { draw: false, span, why: `the work covers about ${p.miles} mi of a ${span.toFixed(0)} mi line; where along it isn't known` };
  }
  return { draw: true, span, why: "" };
}
