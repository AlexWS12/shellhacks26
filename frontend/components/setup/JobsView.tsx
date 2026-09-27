"use client";

// Setup · Models · Jobs and models: the pipeline graph. Pick an agent to see and edit the models for its jobs; pick a
// model to see every agent that uses it. The graph is fluid: x in percent of a 400-unit width, edges in a stretched SVG.

import { engineColor } from "@/lib/format";
import type { AgentView } from "@/lib/run";
import type { ModelList, SetupProvider, SetupRef, SetupResult, SetupRole } from "@/lib/types";

import { keyOf, parseKey, plural } from "./common";

export type Sel = null | { kind: "agent"; id: string } | { kind: "model"; id: string } | { kind: "role"; id: string };

const W = 400, NW = 90, NH = 56, RH = 78, PER_ROW = 4;

function layout(agents: AgentView[]) {
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  const deps = (a: AgentView) => a.depends_on.filter((d) => byId[d]);
  const depth: Record<string, number> = {};
  const d = (id: string): number => depth[id] ??= deps(byId[id]).length ? Math.max(...deps(byId[id]).map(d)) + 1 : 0;
  agents.forEach((a) => d(a.id));
  // The research team starts from nothing: draw it just above the agent that gathers its results.
  for (const a of agents) {
    if ((a.team ?? "core") !== "research" || deps(a).length) continue;
    const kids = agents.filter((k) => deps(k).includes(a.id)).map((k) => depth[k.id]);
    if (kids.length) depth[a.id] = Math.max(0, Math.min(...kids) - 1);
  }
  const rows: AgentView[][] = [];
  for (const a of agents) (rows[depth[a.id]] ??= []).push(a);
  const pos: Record<string, { x: number; y: number }> = {};
  let line = 0;
  for (const row of rows.filter(Boolean)) {
    row.sort((x, y) => Number((y.team ?? "core") === "research") - Number((x.team ?? "core") === "research"));
    for (let k = 0; k < row.length; k += PER_ROW) {
      const chunk = row.slice(k, k + PER_ROW);
      const gap = (W - chunk.length * NW) / (chunk.length + 1);
      chunk.forEach((a, i) => (pos[a.id] = { x: gap + i * (NW + gap), y: line * RH + 6 }));
      line += 1;
    }
  }
  const team = agents.filter((a) => (a.team ?? "core") === "research" && !deps(a).length && pos[a.id]);
  const lane = team.length ? {
    x: Math.min(...team.map((a) => pos[a.id].x)) - 6, y: Math.min(...team.map((a) => pos[a.id].y)) - 8,
    w: Math.max(...team.map((a) => pos[a.id].x)) + NW + 6 - (Math.min(...team.map((a) => pos[a.id].x)) - 6),
    h: Math.max(...team.map((a) => pos[a.id].y)) - Math.min(...team.map((a) => pos[a.id].y)) + NH + 16,
  } : null;
  return { pos, lane, height: Math.max(0, ...Object.values(pos).map((p) => p.y)) + NH + 8, deps };
}

const pct = (x: number) => `${(x / W) * 100}%`;

export default function JobsView({ agents, roles, draft, results, lists, providers, colors, max, sel, setSel, setChain, replaceEverywhere, problemRoles }: {
  agents: AgentView[];
  roles: Record<string, SetupRole>;
  draft: Record<string, SetupRef[]>;
  results: Record<string, SetupResult>;
  lists: Record<string, ModelList>;
  providers: Record<string, SetupProvider>;
  colors: Record<string, string>;
  max: number;
  sel: Sel;
  setSel: (s: Sel) => void;
  setChain: (role: string, chain: SetupRef[]) => void;
  replaceEverywhere: (from: SetupRef, to: SetupRef) => void;
  problemRoles: Set<string>;
}) {
  const { pos, lane, height, deps } = layout(agents);
  const jobsOf = (a: AgentView) => (a.roles ?? []).filter((r) => roles[r]);
  const uses = (a: AgentView, m: string) => jobsOf(a).some((r) => (draft[r] ?? []).some((x) => keyOf(x) === m));
  const failing = (m: string) => Boolean(results[m] && !results[m].ok && results[m].status !== "Off");
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  const selAgent = sel?.kind === "agent" ? byId[sel.id] : null;
  const selColor = sel?.kind === "model" ? colors[sel.id] ?? "var(--accent)" : "var(--accent)";
  const hot = (a: AgentView) => (sel?.kind === "agent" ? a.id === sel.id : sel?.kind === "model" ? uses(a, sel.id) : false);
  const related = (a: AgentView) => Boolean(selAgent && (deps(selAgent).includes(a.id) || deps(a).includes(selAgent.id)));
  const inUse = [...new Set(Object.values(draft).flat().map(keyOf))];
  const loose = Object.keys(roles).filter((r) => !agents.some((a) => jobsOf(a).includes(r))); // jobs no agent on screen calls

  return (
    <div className="su-jobs">
      <div className="su-legend">
        <span className="label">Models in use</span>
        {inUse.map((m) => {
          const ref = parseKey(m), n = agents.filter((a) => uses(a, m)).length;
          return (
            <button key={m} className="su-mpill" aria-pressed={sel?.kind === "model" && sel.id === m}
              onClick={() => setSel(sel?.kind === "model" && sel.id === m ? null : { kind: "model", id: m })}>
              <i className="dot" style={{ background: colors[m] }} />
              <span className="prov">{providers[ref.provider]?.label ?? ref.provider}</span>
              <span className="mono">{ref.model}</span>
              <span className={failing(m) ? "fail" : "count"}>{plural(n, "agent")}</span>
            </button>
          );
        })}
        {sel && <button className="linkbtn" onClick={() => setSel(null)}>Clear selection</button>}
      </div>

      <div className="su-jobgrid">
        <div className="su-graphcard">
          <p className="label">Pipeline <span className="hint">{sel?.kind === "model" ? "Agents using this model" : sel?.kind === "agent" ? "Agent and its neighbors" : ""}</span></p>
          <div className="su-graph" style={{ height }}>
            {lane && (
              <div className="su-lane" style={{ left: pct(lane.x), width: pct(lane.w), top: lane.y, height: lane.h }}>
                <span>Research team</span>
              </div>
            )}
            <svg className="su-edges" viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" aria-hidden="true">
              {agents.flatMap((a) => deps(a).map((dep) => {
                const p = pos[dep], q = pos[a.id];
                if (!p || !q) return null;
                const x1 = p.x + NW / 2, y1 = p.y + NH, x2 = q.x + NW / 2, y2 = q.y, my = (y1 + y2) / 2;
                const onPath = sel?.kind === "agent" && (dep === sel.id || a.id === sel.id);
                const both = sel?.kind === "model" && hot(a) && hot(byId[dep]);
                const lit = onPath || both;
                return (
                  <path key={`${dep}-${a.id}`} d={`M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`} vectorEffect="non-scaling-stroke"
                    className={lit ? "lit" : sel ? "dim" : ""} style={lit ? { stroke: selColor } : undefined} />
                );
              }))}
            </svg>
            {agents.map((a) => {
              const p = pos[a.id];
              if (!p) return null;
              const h = hot(a), rel = related(a);
              const jobs = jobsOf(a);
              return (
                <button key={a.id} className={`su-node ${h ? "hot" : rel ? "rel" : sel ? "dim" : ""}`}
                  style={{ left: pct(p.x), top: p.y, "--eng": engineColor(a.engine ?? ""), ...(h ? { "--hot": selColor } : {}) } as React.CSSProperties}
                  onClick={() => setSel({ kind: "agent", id: a.id })} title={a.role} aria-pressed={h}>
                  <span className="nm">{a.name}</span>
                  <span className="eng">{a.engine}</span>
                  <span className="dots">
                    {jobs.length === 0 ? <span className="nomodel">no model</span> : jobs.map((r) => {
                      const first = draft[r]?.[0];
                      const k = first ? keyOf(first) : "";
                      return <i key={r} style={{ background: colors[k] ?? "var(--line-2)" }}
                        className={sel?.kind === "model" && sel.id === k ? "ring" : problemRoles.has(r) ? "bad" : ""} />;
                    })}
                  </span>
                </button>
              );
            })}
          </div>
          {loose.length > 0 && (
            <p className="su-loose">Not tied to an agent:{" "}
              {loose.map((r) => <button key={r} className="linkbtn" onClick={() => setSel({ kind: "role", id: r })}>{roles[r].label}</button>)}
            </p>
          )}
        </div>

        <div className="su-detail">
          {!sel && <p className="empty su-center">Click an agent in the pipeline, or a model above.</p>}
          {sel?.kind === "agent" && selAgent && (
            <>
              <h3>{selAgent.name}</h3>
              <div className="su-meta"><span className="tag" style={{ color: engineColor(selAgent.engine ?? "") }}>{selAgent.engine}</span>
                {jobsOf(selAgent).length > 0 && <span>{plural(jobsOf(selAgent).length, "job")}</span>}</div>
              <p className="sub">{selAgent.role}.</p>
              {jobsOf(selAgent).length === 0 && <p className="note">This agent is plain code and calls no model.</p>}
              {jobsOf(selAgent).map((r) => (
                <JobCard key={r} role={roles[r]} chain={draft[r] ?? []} providers={providers} lists={lists} results={results}
                  colors={colors} max={max} problem={problemRoles.has(r)} onChange={(c) => setChain(r, c)}
                  others={agents.filter((x) => x.id !== selAgent.id && jobsOf(x).includes(r)).map((x) => x.name)} />
              ))}
            </>
          )}
          {sel?.kind === "role" && roles[sel.id] && (
            <JobCard role={roles[sel.id]} chain={draft[sel.id] ?? []} providers={providers} lists={lists} results={results}
              colors={colors} max={max} problem={problemRoles.has(sel.id)} onChange={(c) => setChain(sel.id, c)} others={[]} />
          )}
          {sel?.kind === "model" && (
            <ModelDetail id={sel.id} agents={agents} roles={roles} draft={draft} results={results} lists={lists} providers={providers}
              colors={colors} jobsOf={jobsOf} onAgent={(id) => setSel({ kind: "agent", id })}
              onRole={(id) => setSel({ kind: "role", id })} replaceEverywhere={(to) => { replaceEverywhere(parseKey(sel.id), to); setSel({ kind: "model", id: keyOf(to) }); }} />
          )}
        </div>
      </div>
    </div>
  );
}

function Result({ res }: { res?: SetupResult }) {
  if (!res) return null;
  return <span className={`res ${res.status === "Off" ? "" : res.ok ? "good" : "fail"}`} title={res.message || undefined}>{res.plain}</span>;
}

function JobCard({ role, chain, providers, lists, results, colors, max, problem, others, onChange }: {
  role: SetupRole; chain: SetupRef[]; providers: Record<string, SetupProvider>; lists: Record<string, ModelList>;
  results: Record<string, SetupResult>; colors: Record<string, string>; max: number; problem: boolean; others: string[];
  onChange: (chain: SetupRef[]) => void;
}) {
  // Every model a provider lists, plus whatever this job already uses (so a name missing from a list still shows).
  const options = role.providers.map((pid) => {
    const listed = (lists[pid]?.models ?? []).map((m) => m.id);
    const own = [...chain, ...role.default_models].filter((r) => r.provider === pid).map((r) => r.model);
    return { pid, ids: [...new Set([...listed, ...own])], listed: new Set(listed) };
  });
  const move = (i: number, d: number) => {
    const next = [...chain];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const used = new Set(chain.map(keyOf));
  const spare = options.flatMap((o) => o.ids.filter((m) => !used.has(`${o.pid}/${m}`)).map((m) => ({ provider: o.pid, model: m })));
  const first = chain[0] ? results[keyOf(chain[0])] : undefined;
  const failsFirst = problem || Boolean(first && !first.ok && first.status !== "Off");
  return (
    <div id={`role-${role.name}`} className={`su-job ${failsFirst ? "bad" : ""}`}>
      <div className="su-job-head">
        <b>{role.label}</b>
        <span className="tag">{role.group}</span>
        {role.not_used && <span className="tag">Not needed right now</span>}
      </div>
      {others.length > 0 && <p className="note">Also used by {others.join(", ")}.</p>}
      <p className="sub">{role.description}{role.not_used ? ` ${role.not_used}` : ""}</p>
      <div className="su-chain">
        {chain.map((ref, i) => (
          <div key={`${i}-${keyOf(ref)}`} className="su-chain-row">
            <span className="slot">{i === 0 ? "Try first" : `Backup ${i}`}</span>
            <i className="dot" style={{ background: colors[keyOf(ref)] }} />
            <select aria-label={`${role.label}: ${i === 0 ? "first model" : `backup ${i}`}`} value={keyOf(ref)}
              onChange={(e) => {
                const next = chain.map((x, j) => (j === i ? parseKey(e.target.value) : x));
                onChange(next.filter((x, j) => next.findIndex((y) => keyOf(y) === keyOf(x)) === j));
              }}>
              {options.map((o) => (
                <optgroup key={o.pid} label={`${providers[o.pid]?.label ?? o.pid}${providers[o.pid]?.key_present ? "" : " (no key yet)"}`}>
                  {o.ids.map((m) => (
                    <option key={m} value={`${o.pid}/${m}`}>{m}{o.listed.size && !o.listed.has(m) ? " (not in the list)" : ""}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <Result res={results[keyOf(ref)]} />
            <span className="order">
              <button aria-label="Move up" title="Try earlier" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button aria-label="Move down" title="Try later" disabled={i === chain.length - 1} onClick={() => move(i, 1)}>↓</button>
              <button aria-label="Remove" title="Remove" disabled={chain.length === 1} onClick={() => onChange(chain.filter((_, j) => j !== i))}>✕</button>
            </span>
          </div>
        ))}
        {chain.length < max && spare.length > 0 && <button className="linkbtn" onClick={() => onChange([...chain, spare[0]])}>+ Add a backup model</button>}
      </div>
    </div>
  );
}

function ModelDetail({ id, agents, roles, draft, results, lists, providers, colors, jobsOf, onAgent, onRole, replaceEverywhere }: {
  id: string; agents: AgentView[]; roles: Record<string, SetupRole>; draft: Record<string, SetupRef[]>;
  results: Record<string, SetupResult>; lists: Record<string, ModelList>; providers: Record<string, SetupProvider>;
  colors: Record<string, string>; jobsOf: (a: AgentView) => string[]; onAgent: (id: string) => void; onRole: (id: string) => void;
  replaceEverywhere: (to: SetupRef) => void;
}) {
  const ref = parseKey(id);
  const slot = (r: string) => (draft[r] ?? []).findIndex((x) => keyOf(x) === id);
  const rows = agents.flatMap((a) => jobsOf(a).filter((r) => slot(r) >= 0).map((r) => ({ key: `${a.id}:${r}`, who: a.name, job: r, open: () => onAgent(a.id) })));
  const claimed = new Set(agents.flatMap(jobsOf));
  for (const r of Object.keys(roles)) if (!claimed.has(r) && slot(r) >= 0) rows.push({ key: r, who: "—", job: r, open: () => onRole(r) });
  const jobs = new Set(rows.map((x) => x.job)).size;
  const others = (lists[ref.provider]?.models ?? []).filter((m) => m.id !== ref.model);
  return (
    <>
      <div className="su-mhead">
        <i className="dot" style={{ background: colors[id] }} />
        <h3>{providers[ref.provider]?.label ?? ref.provider} · {ref.model}</h3>
        <Result res={results[id]} />
      </div>
      <p className="sub">Used by {plural(new Set(rows.filter((x) => x.who !== "—").map((x) => x.who)).size, "agent")} across {plural(jobs, "job")}. Click a row to open that agent.</p>
      {others.length > 0 && (
        <label className="field"><span>Change it everywhere</span>
          <select value="" onChange={(e) => e.target.value && replaceEverywhere(parseKey(e.target.value))}>
            <option value="">Use instead…</option>
            {others.map((m) => <option key={m.id} value={`${ref.provider}/${m.id}`}>{m.id}</option>)}
          </select>
        </label>
      )}
      <div className="preview su-uses">
        <table>
          <thead><tr><th>Agent</th><th>Job</th><th>Position</th></tr></thead>
          <tbody>
            {rows.map((x) => (
              <tr key={x.key} tabIndex={0} onClick={x.open} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), x.open())}>
                <td>{x.who}</td><td>{roles[x.job]?.label ?? x.job}</td><td>{slot(x.job) === 0 ? "Try first" : `Backup ${slot(x.job)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
