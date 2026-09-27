// Faded start = the work began before its plan's first year; exact date not in the filing.

import type { Project } from "@/lib/types";

const d = (s: string) => new Date(`${s}T00:00:00`);

function win(p: Project) {
  const from = p.build_start ?? p.build_active_from;
  return { start: from ? d(from) : null, end: d(p.in_service_date), exact: Boolean(p.build_start) };
}

export default function Gantt({ a, b, labels = ["Dominion", "Georgia"], colors = ["var(--desc)", "var(--gpc)"] }:
  { a: Project; b: Project; labels?: [string, string]; colors?: [string, string] }) {
  const wa = win(a), wb = win(b);
  const all = [wa.start, wa.end, wb.start, wb.end].filter(Boolean) as Date[];
  const y0 = Math.min(...all.map((x) => x.getFullYear()));
  const y1 = Math.max(...all.map((x) => x.getFullYear())) + 1;
  const w = 340, h = 80, left = 70, right = w - 18;
  const x = (t: Date) => left + ((t.getTime() - new Date(y0, 0, 1).getTime()) / (new Date(y1, 0, 1).getTime() - new Date(y0, 0, 1).getTime())) * (right - left);
  const step = Math.max(1, Math.ceil((y1 - y0) / 5));
  const years: number[] = [];
  for (let y = y0; y <= y1; y += step) years.push(y);
  const lo = wa.start && wb.start ? new Date(Math.max(wa.start.getTime(), wb.start.getTime())) : null;
  const hi = new Date(Math.min(wa.end.getTime(), wb.end.getTime()));
  const overlap = lo && hi > lo;

  const bar = (wd: ReturnType<typeof win>, y: number, color: string, id: string) =>
    wd.start ? (
      <g>
        {!wd.exact && (
          <linearGradient id={id} x1="0" x2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.1" />
            <stop offset="0.35" stopColor={color} stopOpacity="1" />
          </linearGradient>
        )}
        <rect x={x(wd.start)} y={y} width={Math.max(3, x(wd.end) - x(wd.start))} height={14} rx={3} fill={wd.exact ? color : `url(#${id})`} />
      </g>
    ) : (
      <rect x={x(wd.end) - 3} y={y} width={6} height={14} rx={2} fill={color} />
    );

  return (
    <svg className="gantt" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Build windows">
      {years.map((y) => (
        <g key={y}>
          <line x1={x(new Date(y, 0, 1))} x2={x(new Date(y, 0, 1))} y1={4} y2={58} className="tick" />
          <text x={x(new Date(y, 0, 1))} y={74} textAnchor="middle" className="yr">{y}</text>
        </g>
      ))}
      {overlap && <rect x={x(lo!)} y={4} width={x(hi) - x(lo!)} height={54} fill="var(--zone)" fillOpacity={0.14} />}
      <text x={0} y={22}>{labels[0].slice(0, 11)}</text>
      {bar(wa, 12, colors[0], "ga-a")}
      <text x={0} y={48}>{labels[1].slice(0, 11)}</text>
      {bar(wb, 38, colors[1], "ga-b")}
    </svg>
  );
}
