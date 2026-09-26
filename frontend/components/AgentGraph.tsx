"use client";

// Everything here moves because of an event.

import { engineColor } from "@/lib/format";
import { run, useRev, type AgentView } from "@/lib/run";
import { useUI } from "@/lib/ui";

const W = 318;
const NODE_W = 100;
const NODE_H = 58;
const ROW_H = 72;
const HANDOFF_MS = 900;

function layout(agents: AgentView[]): Record<string, { x: number; y: number }> {
  const depth: Record<string, number> = {};
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  const d = (id: string): number => {
    if (depth[id] != null) return depth[id];
    const deps = byId[id]?.depends_on ?? [];
    depth[id] = deps.length ? Math.max(...deps.map(d)) + 1 : 0;
    return depth[id];
  };
  agents.forEach((a) => d(a.id));
  const rows: string[][] = [];
  agents.forEach((a) => (rows[depth[a.id]] ??= []).push(a.id));
  const pos: Record<string, { x: number; y: number }> = {};
  rows.forEach((row, r) => {
    const gap = (W - row.length * NODE_W) / (row.length + 1);
    row.forEach((id, i) => (pos[id] = { x: gap + i * (NODE_W + gap), y: r * ROW_H }));
  });
  return pos;
}

function counter(a: AgentView): string {
  if (a.status === "idle") return "waiting";
  if (a.status === "error") return a.summary;
  if (a.status === "done") return a.summary;
  if (a.count != null) return `${a.count}${a.total ? ` / ${a.total}` : ""} ${a.label}`;
  return a.lastTool ? `running ${a.lastTool}` : "working…";
}

export default function AgentGraph({ onSelect }: { onSelect: (id: string) => void }) {
  useRev((s) => s.rev);
  const panel = useUI((s) => s.panel);
  const agents = run.agentOrder.map((id) => run.agents[id]).filter(Boolean);
  if (!agents.length) return <p className="empty">Loading the agent graph…</p>;
  const pos = layout(agents);
  const height = Math.max(...Object.values(pos).map((p) => p.y)) + NODE_H + 4;
  const path = (from: string, to: string) => {
    const a = pos[from], b = pos[to];
    const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y;
    const my = (y1 + y2) / 2;
    return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
  };
  // each dot animates once on mount, then stays invisible
  const flows = run.handoffs.filter((h) => pos[h.from] && pos[h.to]);

  return (
    <div className="graph" style={{ height }}>
      <svg className="edges" width={W} height={height} aria-hidden="true">
        {agents.flatMap((a) =>
          a.depends_on.filter((d) => pos[d]).map((d) => (
            <path key={`${d}-${a.id}`} id={`e-${d}-${a.id}`} d={path(d, a.id)}
              className={`edge ${a.status === "working" ? "hot" : ""}`} />
          )),
        )}
      </svg>
      {flows.map((h) => (
        <i key={h.id} className="flowdot" style={{ offsetPath: `path("${path(h.from, h.to)}")`, animationDuration: `${HANDOFF_MS}ms` }} />
      ))}
      {agents.map((a) => {
        const h = run.health[a.id];
        const selected = panel.kind === "agent" && panel.id === a.id;
        return (
          <button
            key={a.id}
            className={`node ${a.kind} ${a.status} ${selected ? "sel" : ""}`}
            style={{ left: pos[a.id].x, top: pos[a.id].y, ["--eng" as string]: engineColor(a.engine ?? "") }}
            onClick={() => onSelect(a.id)}
            title={`${a.name}: ${a.role}`}
            aria-label={`${a.name}, ${a.status}`}
          >
            <span className="nm">{a.name}</span>
            <span className="eng">{a.engine}</span>
            <span className="ct">{counter(a)}</span>
            <i className="st" title={h ? `Watchdog: stuck ${h.stuck.toFixed(2)}, progress ${h.progress.toFixed(1)}/4` : undefined}
              style={h && a.status === "working" && h.stuck >= 0.6 ? { background: "var(--bad)" } : undefined} />
          </button>
        );
      })}
    </div>
  );
}
