"use client";

import { Activity, Database, Menu, Target, TriangleAlert, Workflow, type LucideIcon } from "lucide-react";
import { useRef } from "react";

import { run, useRev } from "@/lib/run";
import { RAIL_TABS, useUI, type RailTab } from "@/lib/ui";

import { SECTION_PANELS } from "./PipelinePanel";

type Tone = "muted" | "zone" | "good" | "bad";

const TABS: Record<RailTab, { icon: LucideIcon; label: string; head: string }> = {
  sources: { icon: Database, label: "Sources", head: "Sources" },
  agents: { icon: Workflow, label: "Agents", head: "Agents · click to inspect" },
  bench: { icon: Target, label: "Accuracy", head: "Accuracy benchmark" },
  issues: { icon: TriangleAlert, label: "Issues", head: "Data issues found" },
  activity: { icon: Activity, label: "Activity", head: "Activity" },
};

// The headline number beside each tab, and a dot when something needs a look. Nothing before the run starts.
function status(tab: RailTab): { meta: string; tone: Tone; dot: Tone | null } {
  const none = { meta: "", tone: "muted" as Tone, dot: null };
  if (run.phase === "idle") return none;
  switch (tab) {
    case "sources": {
      const srcs = run.sourceOrder.map((id) => run.sources[id]);
      if (!srcs.length) return none;
      const read = srcs.reduce((n, s) => n + s.read, 0), total = srcs.reduce((n, s) => n + s.total, 0);
      return { meta: `${read}/${total}`, tone: "muted", dot: null };
    }
    case "agents": {
      const agents = Object.values(run.agents);
      const working = agents.filter((a) => a.status === "working").length;
      const dot = agents.some((a) => a.status === "error") ? "bad" : working ? "zone" : null;
      if (working) return { meta: `${working} running`, tone: "zone", dot };
      const done = agents.filter((a) => a.status === "done").length;
      return { meta: agents.length ? `${done}/${agents.length}` : "", tone: "muted", dot };
    }
    case "bench": {
      const total = run.reference.length;
      if (!total) return none;
      const passed = run.reference.filter((r) => r.passed).length;
      return { meta: `${passed}/${total}`, tone: passed === total ? "good" : "bad", dot: passed === total ? null : "bad" };
    }
    case "issues": {
      const n = run.checks.length;
      if (!n) return none;
      return { meta: String(n), tone: "zone", dot: run.checks.some((c) => c.level === "error") ? "bad" : "zone" };
    }
    case "activity":
      return run.phase === "running" ? { meta: "live", tone: "bad", dot: null } : none;
  }
}

export function Rail() {
  useRev((s) => s.rev);
  const tab = useUI((s) => s.railTab);
  const open = useUI((s) => s.railOpen);
  const expanded = useUI((s) => s.railExpanded);
  const select = useUI((s) => s.selectRailTab);
  const toggle = useUI((s) => s.toggleRailExpanded);
  const items = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow keys move between tabs; only the current one is in the tab order.
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? RAIL_TABS.length - 1 : step ? (i + step + RAIL_TABS.length) % RAIL_TABS.length : -1;
    if (to < 0) return;
    e.preventDefault();
    items.current[to]?.focus();
  };

  return (
    <nav className={`rail ${expanded ? "expanded" : ""}`} aria-label="Pipeline">
      <button className="rail-menu" onClick={toggle} aria-expanded={expanded} aria-label={expanded ? "Hide labels" : "Show labels"}>
        <Menu size={22} strokeWidth={2} aria-hidden="true" />
        {expanded && <span>Pipeline</span>}
      </button>
      <div className="rail-items">
        {RAIL_TABS.map((id, i) => {
          const t = TABS[id];
          const st = status(id);
          const on = open && tab === id;
          const Icon = t.icon;
          return (
            <button key={id} ref={(b) => { items.current[i] = b; }} className={`rail-item ${on ? "on" : ""}`}
              onClick={() => select(id)} onKeyDown={(e) => onKey(e, i)} tabIndex={tab === id ? 0 : -1}
              title={expanded ? undefined : t.label} aria-label={t.label} aria-pressed={on}>
              <Icon size={20} strokeWidth={2} aria-hidden="true" />
              {st.dot && <i className={`rail-dot ${st.dot}`} aria-hidden="true" />}
              {expanded && (
                <>
                  <span className="rail-label">{t.label}</span>
                  <span className={`rail-meta ${st.tone}`}>{st.meta}</span>
                </>
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

export function Flyout() {
  useRev((s) => s.rev);
  const tab = useUI((s) => s.railTab);
  const close = useUI((s) => s.closeRail);
  const t = TABS[tab];
  const st = status(tab);
  const Body = SECTION_PANELS[tab];
  return (
    <section className="flyout" aria-label={t.label} onKeyDown={(e) => {
      if (e.key !== "Escape") return;
      close();
      document.querySelector<HTMLElement>(".rail-item[tabindex='0']")?.focus();
    }}>
      <div className="flyout-head">
        <span className="label">{t.head}</span>
        <span className={`rail-meta ${st.tone}`}>{st.meta}</span>
        <span className="spacer" />
        <button className="flyout-close" onClick={close} aria-label="Close panel">×</button>
      </div>
      <div className="flyout-body">
        <Body />
      </div>
    </section>
  );
}
