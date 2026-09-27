"use client";

// Model setup: which AI model does each job, and what to try when it fails. Four steps: keys, jobs and models,
// test, save. Saving writes config/models.local.json on the server. Keys never come back from the server, only
// whether one is set and where. On a hosted server the whole screen needs the admin passcode, and keys can't be
// entered here.

import { useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, setPasscode } from "@/lib/api";
import { useFocusTrap } from "@/lib/focus";
import type { ModelList, SetupConfig, SetupProvider, SetupRef, SetupResult, SetupRole } from "@/lib/types";
import { closeSetup, startRun, useUI } from "@/lib/ui";

const STEPS = ["Keys", "Jobs and models", "Test", "Save"] as const;
const keyOf = (r: SetupRef) => `${r.provider}/${r.model}`;
const parseKey = (v: string): SetupRef => {
  const i = v.indexOf("/");
  return { provider: v.slice(0, i), model: v.slice(i + 1) };
};
const same = (a: SetupRef[], b: SetupRef[]) => a.length === b.length && a.every((x, i) => keyOf(x) === keyOf(b[i]));
const draftOf = (cfg: SetupConfig) => Object.fromEntries(cfg.roles.map((r) => [r.name, r.models.map((m) => ({ ...m }))]));

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function ModelSetup() {
  const setupState = useUI((s) => s.setup);
  const [cfg, setCfg] = useState<SetupConfig | null>(null);
  const [locked, setLocked] = useState<string | null>(null); // hosted: why the passcode is needed
  const [code, setCode] = useState("");
  const [draft, setDraft] = useState<Record<string, SetupRef[]>>({});
  const [lists, setLists] = useState<Record<string, ModelList>>({});
  const [results, setResults] = useState<Record<string, SetupResult>>({});
  const [testedAt, setTestedAt] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState<{ role: string; label: string; reason: string }[]>([]);
  const [saved, setSaved] = useState<{ warnings: string[]; changed: string[] } | null>(null);
  const [keyIn, setKeyIn] = useState<Record<string, string>>({});
  const [jevHost, setJevHost] = useState("typesafe");
  const [keyNote, setKeyNote] = useState<Record<string, { ok: boolean; text: string }>>({});
  const focused = useRef(false);

  const focusRole = setupState?.focusRole ?? null;
  const problems = setupState?.problems ?? [];
  const problemRoles = new Set(problems.map((p) => p.role));

  async function loadLists(providers: SetupProvider[]) {
    const out: Record<string, ModelList> = {};
    await Promise.all(providers.map(async (p) => {
      try {
        out[p.id] = await api.setup.models(p.id);
      } catch (e) {
        out[p.id] = { provider: p.id, models: [], status: "error", plain: errText(e) };
      }
    }));
    setLists(out);
  }

  async function load(keepDraft = false) {
    setBusy("loading");
    setError("");
    try {
      const c = await api.setup.config();
      setCfg(c);
      setLocked(null);
      if (!keepDraft) setDraft(draftOf(c));
      if (c.providers.find((p) => p.id === "jev")?.host) setJevHost(c.providers.find((p) => p.id === "jev")!.host!);
      void loadLists(c.providers);
    } catch (e) {
      if (e instanceof ApiError && [401, 429, 503].includes(e.status)) setLocked(e.message);
      else setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    void load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const box = useRef<HTMLDivElement>(null);
  useFocusTrap(box, closeSetup);

  // Opened because a run was refused: go straight to that job.
  useEffect(() => {
    if (!cfg || focused.current) return;
    if (setupState?.step === "keys") {
      focused.current = true;
      return setStep(0);
    }
    if (!focusRole) return;
    focused.current = true;
    setStep(1);
    requestAnimationFrame(() => document.getElementById(`role-${focusRole}`)?.scrollIntoView({ block: "center" }));
  }, [cfg, focusRole, setupState?.step]);

  const providers = useMemo(() => Object.fromEntries((cfg?.providers ?? []).map((p) => [p.id, p])), [cfg]);
  const dirty = cfg ? cfg.roles.some((r) => !same(draft[r.name] ?? [], r.models)) : false;

  const setChain = (role: string, chain: SetupRef[]) => {
    setDraft((d) => ({ ...d, [role]: chain }));
    setSaved(null);
    setBlocked([]);
  };

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    setPasscode(code);
    await load();
  }

  async function saveKey(p: SetupProvider) {
    const host = p.id === "jev" ? jevHost : undefined;
    const fields = p.id === "jev" ? p.hosts?.find((h) => h.id === jevHost)?.fields ?? [] : p.fields;
    const values = Object.fromEntries(fields.map((f) => [f.var, keyIn[f.var] ?? ""]));
    setBusy(`key:${p.id}`);
    setError("");
    try {
      const r = await api.setup.keys(p.id, values, host);
      setKeyNote((n) => ({ ...n, [p.id]: { ok: r.saved, text: r.plain + (r.warning ? ` ${r.warning}` : "") } }));
      if (r.saved) {
        setKeyIn({});
        setCfg((c) => (c ? { ...c, providers: r.providers } : c));
        void loadLists(r.providers);
        setResults({});
      }
    } catch (e) {
      setKeyNote((n) => ({ ...n, [p.id]: { ok: false, text: errText(e) } }));
    } finally {
      setBusy("");
    }
  }

  async function testAll() {
    if (!cfg) return;
    const refs = [...new Map(Object.values(draft).flat().map((r) => [keyOf(r), r])).values()];
    setBusy("testing");
    setError("");
    try {
      const out: Record<string, SetupResult> = {};
      for (let i = 0; i < refs.length; i += 40) {
        for (const r of (await api.setup.validate(refs.slice(i, i + 40))).results) out[keyOf(r)] = r;
      }
      setResults(out);
      setTestedAt(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  async function save() {
    setBusy("saving");
    setError("");
    setBlocked([]);
    try {
      const r = await api.setup.save(draft);
      setSaved({ warnings: r.warnings, changed: r.changed });
      setResults((old) => ({ ...old, ...Object.fromEntries(r.results.map((x) => [keyOf(x), x])) }));
      await load(true);
    } catch (e) {
      const d = e instanceof ApiError ? (e.detail as { blocked?: typeof blocked; results?: SetupResult[] } | null) : null;
      if (d?.blocked) {
        setBlocked(d.blocked);
        if (d.results) setResults((old) => ({ ...old, ...Object.fromEntries(d.results!.map((x) => [keyOf(x), x])) }));
      }
      setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  async function resetToDefaults() {
    setBusy("saving");
    try {
      await api.setup.reset();
      setSaved(null);
      setResults({});
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  // Swap one model for another in every job that uses it (e.g. a model that hit its usage limit).
  const replaceEverywhere = (from: SetupRef, to: SetupRef) => {
    setDraft((d) => Object.fromEntries(Object.entries(d).map(([name, chain]) => {
      const swapped = chain.map((x) => (keyOf(x) === keyOf(from) ? to : x));
      return [name, swapped.filter((x, i) => swapped.findIndex((y) => keyOf(y) === keyOf(x)) === i)];
    })));
    setSaved(null);
    setBlocked([]);
  };

  const statusOf = (role: SetupRole) => {
    // A provider switched off on purpose is skipped, like the run skips it: neither a pass nor a failing backup.
    const rs = (draft[role.name] ?? []).map((r) => results[keyOf(r)]).filter((x) => x?.status !== "Off");
    if (rs.some((x) => !x)) return "untested";
    if (!rs.some((x) => x.ok)) return "none";
    return rs.every((x) => x.ok) ? "all" : "some";
  };
  const needed = (cfg?.roles ?? []).filter((r) => !r.not_used);
  const noneWork = needed.filter((r) => statusOf(r) === "none");
  const backupFails = needed.filter((r) => statusOf(r) === "some");
  const untested = (cfg?.roles ?? []).some((r) => statusOf(r) === "untested");

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && closeSetup()}>
      <div ref={box} className="modal setup" role="dialog" aria-modal="true" aria-labelledby="setup-title" tabIndex={-1}>
        <div className="modal-head">
          <h2 id="setup-title">Model setup</h2>
          <button className="linkbtn" onClick={closeSetup} aria-label="Close">Close</button>
        </div>
        <p className="sub">
          Choose which AI model does each job, and which backups to try if it fails. Keys stay on the server: this
          screen only shows whether one is set.
        </p>

        {problems.length > 0 && (
          <div className="banner" role="alert">
            <b>The run didn&apos;t start.</b> These jobs have no working model:
            <ul>
              {[...problems.reduce((m, p) => m.set(p.reason, [...(m.get(p.reason) ?? []), p]), new Map<string, typeof problems>())].map(([reason, ps]) => (
                <li key={reason}><b>{ps.map((p) => p.label).join(", ")}</b>: {reason}</li>
              ))}
            </ul>
            {setupState?.canForce && (
              <div className="row2">
                <button onClick={() => { closeSetup(); void startRun("live", true); }}>Run anyway</button>
                <span className="note">Those models are only busy or over their limit right now. Their text will come from templates.</span>
              </div>
            )}
          </div>
        )}

        {locked ? (
          <form className="unlock" onSubmit={(e) => void unlock(e)}>
            <p>{locked} Model setup on this server is for its admin.</p>
            <div className="fields">
              <label className="field"><span>Admin passcode</span>
                <input type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)} autoFocus /></label>
            </div>
            <div className="row2"><button className="primary" type="submit" disabled={!code || busy === "loading"}>Unlock</button></div>
          </form>
        ) : !cfg ? (
          <p className="empty">{busy === "loading" ? "Loading…" : "Couldn't load the setup."}</p>
        ) : (
          <>
            <div className="seg tabs" role="group" aria-label="Steps">
              {STEPS.map((s, i) => (
                <button key={s} aria-pressed={step === i} onClick={() => setStep(i)}>
                  {i + 1}. {s}
                </button>
              ))}
            </div>
            {cfg.error && <p className="err-inline">{cfg.error}</p>}

            {step === 0 && (
              <div>
                {cfg.mode === "hosted" && (
                  <p className="note">This is a hosted server: keys are set as environment variables by whoever runs it, not here.</p>
                )}
                {cfg.providers.map((p) => {
                  const fields = p.id === "jev" ? p.hosts?.find((h) => h.id === jevHost)?.fields ?? [] : p.fields;
                  const where = p.fields.find((f) => f.source)?.source;
                  return (
                    <div key={p.id} className="pcard">
                      <div className="pcard-head">
                        <b>{p.label}</b>
                        {p.key_present ? <span className="good">Key set{where ? ` in ${where}` : ""}</span>
                          : p.off_reason && !p.host ? <span className="note">Turned off.{p.can_enter_key ? " Add a key to use it." : ""}</span>
                            : <span className="fail">No key yet</span>}
                      </div>
                      <p className="sub">{p.what} Get a key from {p.get_key}.</p>
                      {p.can_enter_key && (
                        <>
                          <div className="fields">
                            {p.id === "jev" && (
                              <label className="field"><span>Where Jev runs</span>
                                <select value={jevHost} onChange={(e) => setJevHost(e.target.value)}>
                                  {p.hosts?.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}
                                </select></label>
                            )}
                            {fields.map((f) => (
                              <label key={f.var} className="field">
                                <span>{p.key_present ? `Replace the ${f.label} (optional)` : f.label}</span>
                                <input type={f.secret ? "password" : "text"} autoComplete="off" spellCheck={false}
                                  value={keyIn[f.var] ?? ""} onChange={(e) => setKeyIn((k) => ({ ...k, [f.var]: e.target.value }))}
                                  placeholder={f.secret ? "Paste it here" : ""} />
                              </label>
                            ))}
                          </div>
                          <div className="row2">
                            <button onClick={() => void saveKey(p)} disabled={busy !== "" || !fields.some((f) => (keyIn[f.var] ?? "").trim())}>
                              {busy === `key:${p.id}` ? "Checking…" : "Check and save key"}
                            </button>
                            {keyNote[p.id] && <span className={keyNote[p.id].ok ? "good" : "fail"}>{keyNote[p.id].text}</span>}
                          </div>
                          <p className="note">The key is checked with {p.label} first, then saved in backend/.env.local on this machine.</p>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {step === 1 && (
              <div>
                <p className="note">
                  Each job tries its first model, then each backup in order. A backup can come from another provider,
                  for example Gemini behind Jev for the quick decisions.
                </p>
                {[...new Set(cfg.roles.map((r) => r.group))].map((g) => (
                  <section key={g}>
                    <h3>{g}</h3>
                    {cfg.roles.filter((r) => r.group === g).map((r) => (
                      <RoleEditor key={r.name} role={r} chain={draft[r.name] ?? []} providers={providers} lists={lists}
                        results={results} max={cfg.max_models_per_role} highlight={problemRoles.has(r.name) || r.name === focusRole}
                        onChange={(c) => setChain(r.name, c)} />
                    ))}
                  </section>
                ))}
              </div>
            )}

            {step === 2 && (
              <div>
                <div className="row2">
                  <button className="primary" onClick={() => void testAll()} disabled={busy !== ""}>
                    {busy === "testing" ? "Testing…" : "Test all"}
                  </button>
                  <span className="note">
                    {testedAt ? `Last tested at ${testedAt}.` : "Checks every chosen model with its key: one tiny request each."}
                  </span>
                </div>
                {Object.keys(results).length > 0 && (
                  <div className="preview tests">
                    <table>
                      <thead><tr><th>Model</th><th>Used by</th><th>Result</th><th>Change it everywhere</th></tr></thead>
                      <tbody>
                        {[...new Map(Object.values(draft).flat().map((x) => [keyOf(x), x])).values()].map((m) => {
                          const res = results[keyOf(m)];
                          const users = cfg.roles.filter((r) => (draft[r.name] ?? []).some((x) => keyOf(x) === keyOf(m)));
                          return (
                            <tr key={keyOf(m)}>
                              <td>{providers[m.provider]?.label ?? m.provider} · {m.model}</td>
                              <td>{users.length === 1 ? users[0].label : `${users.length} jobs`}</td>
                              <td className={!res || res.status === "Off" ? "" : res.ok ? "good" : "fail"} title={res?.message || undefined}>
                                {res ? res.plain : "Not tested yet"}
                              </td>
                              <td>
                                {(lists[m.provider]?.models.length ?? 0) > 0 && (
                                  <select aria-label={`Use another model instead of ${m.model} in every job`} value=""
                                    onChange={(e) => e.target.value && replaceEverywhere(m, parseKey(e.target.value))}>
                                    <option value="">Use instead…</option>
                                    {lists[m.provider].models.filter((x) => x.id !== m.model).map((x) => (
                                      <option key={x.id} value={`${m.provider}/${x.id}`}>{x.id}</option>
                                    ))}
                                  </select>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <Summary noneWork={noneWork} backupFails={backupFails} untested={untested} tested={Object.keys(results).length > 0} />
              </div>
            )}

            {step === 3 && (
              <div>
                <Summary noneWork={noneWork} backupFails={backupFails} untested={untested} tested={Object.keys(results).length > 0} />
                {blocked.length > 0 && (
                  <ul className="fail">{blocked.map((b) => <li key={b.role}><b>{b.label}</b>: {b.reason}</li>)}</ul>
                )}
                {saved ? (
                  <div>
                    <p className="ok-inline">
                      Saved. The next run uses these models
                      {saved.changed.length ? ` (${saved.changed.length} job${saved.changed.length === 1 ? "" : "s"} changed from the defaults)` : " (the defaults)"}.
                    </p>
                    {saved.warnings.map((w) => <p key={w} className="note warn">{w}</p>)}
                    <div className="row2">
                      <button className="primary" onClick={() => { closeSetup(); void startRun("live"); }}>Start a run</button>
                      <button onClick={closeSetup}>Close</button>
                    </div>
                  </div>
                ) : (
                  <div className="row2">
                    <button className="primary" onClick={() => void save()} disabled={busy !== "" || noneWork.length > 0}>
                      {busy === "saving" ? "Checking and saving…" : "Save"}
                    </button>
                    {cfg.local_file && <button onClick={() => void resetToDefaults()} disabled={busy !== ""}>Go back to the defaults</button>}
                  </div>
                )}
                <p className="note">
                  Saving checks any model not tested in the last few minutes. It stops if a job has no working model, but a
                  failing backup is only a warning.{dirty ? "" : " Nothing has changed from what's saved."}
                </p>
              </div>
            )}

            {error && <p className="err-inline" role="alert">{error}</p>}
            <div className="row2 steps-nav">
              {step > 0 && <button onClick={() => setStep(step - 1)}>Back</button>}
              {step < STEPS.length - 1 && <button onClick={() => setStep(step + 1)}>Next: {STEPS[step + 1]}</button>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Summary({ noneWork, backupFails, untested, tested }: { noneWork: SetupRole[]; backupFails: SetupRole[]; untested: boolean; tested: boolean }) {
  if (!tested) return <p className="note">Run the test to see which jobs have a working model.</p>;
  return (
    <div className="summary">
      {noneWork.length > 0 && (
        <p className="fail">No working model for: {noneWork.map((r) => r.label).join(", ")}. Pick another model for {noneWork.length === 1 ? "it" : "them"} before saving.</p>
      )}
      {backupFails.length > 0 && (
        <p className="warn">A backup failed for: {backupFails.map((r) => r.label).join(", ")}. You can still save; the first working model is used.</p>
      )}
      {noneWork.length === 0 && backupFails.length === 0 && !untested && <p className="good">Every job has a working model.</p>}
      {untested && <p className="note">Some models changed since the last test.</p>}
    </div>
  );
}

function RoleEditor({ role, chain, providers, lists, results, max, highlight, onChange }: {
  role: SetupRole;
  chain: SetupRef[];
  providers: Record<string, SetupProvider>;
  lists: Record<string, ModelList>;
  results: Record<string, SetupResult>;
  max: number;
  highlight: boolean;
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
  const add = () => {
    const used = new Set(chain.map(keyOf));
    for (const o of options) {
      const id = o.ids.find((m) => !used.has(`${o.pid}/${m}`));
      if (id) return onChange([...chain, { provider: o.pid, model: id }]);
    }
  };
  return (
    <div id={`role-${role.name}`} className={`role${highlight ? " focus" : ""}`}>
      <div className="role-head">
        <b>{role.label}</b>
        {role.not_used && <span className="tag">Not needed right now</span>}
        {!same(chain, role.default_models) && <span className="tag">Changed from the default</span>}
      </div>
      <p className="sub">{role.description}{role.not_used ? ` ${role.not_used}` : ""}</p>
      <div className="chain">
        {chain.map((ref, i) => {
          const res = results[keyOf(ref)];
          return (
            <div key={`${i}-${keyOf(ref)}`} className="chain-row">
              <span className="chain-label">{i === 0 ? "Try first" : `Backup ${i}`}</span>
              <select aria-label={`${role.label}: ${i === 0 ? "first model" : `backup ${i}`}`} value={keyOf(ref)}
                onChange={(e) => onChange(chain.map((x, j) => (j === i ? parseKey(e.target.value) : x)))}>
                {options.map((o) => (
                  <optgroup key={o.pid} label={`${providers[o.pid]?.label ?? o.pid}${providers[o.pid]?.key_present ? "" : " (no key yet)"}`}>
                    {o.ids.map((m) => (
                      <option key={m} value={`${o.pid}/${m}`}>{m}{o.listed.size && !o.listed.has(m) ? " (not in the list)" : ""}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <span className={`res ${!res || res.status === "Off" ? "" : res.ok ? "good" : "fail"}`} title={res?.message || undefined}>{res ? res.plain : ""}</span>
              <span className="order">
                <button aria-label="Move up" title="Try earlier" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button aria-label="Move down" title="Try later" disabled={i === chain.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button aria-label="Remove" title="Remove" disabled={chain.length === 1} onClick={() => onChange(chain.filter((_, j) => j !== i))}>✕</button>
              </span>
            </div>
          );
        })}
        {chain.length < max && <button className="linkbtn" onClick={add}>+ Add a backup model</button>}
      </div>
    </div>
  );
}
