// Build windows of the two projects of a pair, and the band where both are under construction.
// Faded start = the work began before its plan's first year; exact date not in the filing.
// HTML, not SVG, so the bars can slide (left/width transitions) when the panel steps to another pair.

import type { Project } from "@/lib/types";

const d = (s: string) => new Date(`${s}T00:00:00`);
const month = (t: Date) => t.toLocaleDateString("en-US", { month: "short", year: "numeric" });

function win(p: Project) {
  const from = p.build_start ?? p.build_active_from;
  return { start: from ? d(from) : null, end: d(p.in_service_date), exact: Boolean(p.build_start) };
}

function both(a: Project, b: Project): { lo: Date; hi: Date } | null {
  const wa = win(a), wb = win(b);
  if (!wa.start || !wb.start) return null;
  const lo = new Date(Math.max(wa.start.getTime(), wb.start.getTime()));
  const hi = new Date(Math.min(wa.end.getTime(), wb.end.getTime()));
  return hi > lo ? { lo, hi } : null;
}

// The sentence under the chart.
export function overlapText(a: Project, b: Project): string {
  if (!win(a).start || !win(b).start) return "One start date isn't in the filings, so the overlap can't be drawn.";
  const o = both(a, b);
  return o ? `Both under construction from ${month(o.lo)} to ${month(o.hi)}.` : "The build windows don't overlap.";
}

// colors: each project's source color (the caller passes colorOf).
export default function Gantt({ a, b, colors }: { a: Project; b: Project; colors: [string, string] }) {
  const wa = win(a), wb = win(b);
  const all = [wa.start, wa.end, wb.start, wb.end].filter(Boolean) as Date[];
  const y0 = Math.min(...all.map((x) => x.getFullYear()));
  const y1 = Math.max(...all.map((x) => x.getFullYear())) + 1;
  const t0 = new Date(y0, 0, 1).getTime(), span = new Date(y1, 0, 1).getTime() - t0;
  const pct = (t: Date) => `${((t.getTime() - t0) / span) * 100}%`;
  const width = (from: Date, to: Date) => `${Math.max(0.8, ((to.getTime() - from.getTime()) / span) * 100)}%`;
  const step = Math.max(1, Math.ceil((y1 - y0) / 5));
  const years: number[] = [];
  for (let y = y0; y < y1; y += step) years.push(y);
  const o = both(a, b);

  const bar = (w: ReturnType<typeof win>, row: "a" | "b", color: string) => (
    <i className={`bw-bar ${row} ${w.start && !w.exact ? "fade" : ""} ${w.start ? "" : "point"}`}
      style={{ left: w.start ? pct(w.start) : `calc(${pct(w.end)} - 3px)`, width: w.start ? width(w.start, w.end) : undefined,
        "--c": color } as React.CSSProperties} />
  );

  return (
    <div className="bw" role="img" aria-label={`Build windows. ${overlapText(a, b)}`}>
      <div className="bw-track">
        <i className={`bw-band ${o ? "" : "none"}`} style={o ? { left: pct(o.lo), width: width(o.lo, o.hi) } : undefined} />
        {bar(wa, "a", colors[0])}
        {bar(wb, "b", colors[1])}
      </div>
      <div className="bw-axis">
        {years.map((y) => <span key={y} style={{ left: pct(new Date(y, 0, 1)) }}>{y}</span>)}
      </div>
    </div>
  );
}
