// Failure notifications for live runs, built from the run's model events.
//   modal:  a key or model name that doesn't work (AuthError, ModelNotFound, QuotaExceeded: the model is out for the
//           rest of the run), a job with no working model left (role.exhausted), or a run the server refused to start.
//           At most one entry per {provider, model, problem} per run; events that arrive together share one modal.
//   notice: short-lived problems the run recovers from (rate limits, timeouts, unusable answers, outages) and a
//           backup taking over. Non-blocking.
// Replays never notify: the pipeline panel lists the same events as history instead.

import { create } from "zustand";

import type { PreflightDetail } from "./api";
import type { RoleInfo, RunEvent } from "./types";

export interface AlertEntry {
  key: string;
  title: string;
  models: string[]; // "provider/model"
  roles: string[]; // jobs it happened in
  usedBy: string[]; // other jobs that use the same model (plain names)
  outcome: string; // what happened next
  detail: string;
  fix: "keys" | "models"; // where Open setup goes: a key problem is fixed on the Keys step
}
export interface AlertModal { source: "run" | "preflight"; entries: AlertEntry[]; canForce: boolean; problems: PreflightDetail["problems"] }
export interface Notice { key: string; text: string; count: number; at: number }

export const useAlerts = create<{ modal: AlertModal | null; notices: Notice[] }>(() => ({ modal: null, notices: [] }));

const MODAL_CLASSES = new Set(["AuthError", "ModelNotFound", "QuotaExceeded"]);
const NOTICE_CLASSES = new Set(["RateLimited", "Timeout", "BadResponse", "ProviderUnavailable"]);
const BATCH_MS = 500; // events this close together share one modal
const NOTICE_MS = 8000;
const MAX_NOTICES = 4;

export const PLAIN: Record<string, string> = {
  ok: "Works", NoKey: "No key yet", Off: "Turned off", AuthError: "Key rejected", ModelNotFound: "Model name not found",
  RateLimited: "Rate limited", QuotaExceeded: "Usage limit reached", Timeout: "No answer in time",
  ProviderUnavailable: "Service unavailable", BadResponse: "Gave an unusable answer", Disabled: "Turned off",
};
const PROVIDER: Record<string, string> = { gemini: "Gemini", claude: "Claude", openai: "OpenAI", jev: "Jev" };
export const providerName = (p?: string) => (p ? PROVIDER[p] ?? p : "");
const modelOf = (ref: string) => ref.slice(ref.indexOf("/") + 1);

const roleLabel = (roles: RoleInfo, id: string) => roles[id]?.label ?? id;
// role.exhausted is about one call: that item lost its model, and the rest of the stage carried on.
const STOPPED: Record<string, string> = {
  text: "No backup left, so a model didn't write this text. It comes from a template when Template fallback is on.",
  judge: "No backup left, so this decision was made by a simple rule instead.",
  json: "No backup left, so this step produced no output for that item.",
  search: "No backup left, so the web search was skipped.",
};
const stopped = (roles: RoleInfo, role: string) => STOPPED[roles[role]?.kind ?? ""] ?? "No backup left; this step produced no output.";

let runId: string | null = null;
let seen = new Set<string>();
let pending: AlertEntry[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

const fixFor = (cls: string): AlertEntry["fix"] => (cls === "AuthError" || cls === "NoKey" ? "keys" : "models");
const NEXT_STEP: Record<string, string> = {
  AuthError: "Check the key in Model setup, step 1, then retry.", NoKey: "Add a key in Model setup, step 1, then retry.",
  ModelNotFound: "Pick another model for these jobs in Model setup.",
  QuotaExceeded: "Wait for the limit to reset, or pick another model in Model setup.",
  RateLimited: "Try again in a minute, or pick another model.",
};

function titleFor(provider: string, model: string, cls: string, message = ""): string {
  const p = providerName(provider);
  if (cls === "AuthError" || cls === "NoKey") return cls === "NoKey" || /not set|needs /i.test(message) ? `No ${p} API key is set` : `${p} rejected the API key`;
  if (cls === "ModelNotFound") return `Model '${model}' doesn't exist`;
  if (cls === "QuotaExceeded") return `'${model}' has reached its usage limit`;
  return `${p} '${model}': ${PLAIN[cls] ?? cls}`;
}

function usedBy(roles: RoleInfo, ref: string, except: string[]): string[] {
  return Object.entries(roles).filter(([id, r]) => r.models.includes(ref) && !except.includes(id)).map(([, r]) => r.label ?? "");
}

// Adds an entry, or merges it into one with the same title (a rejected key shows once, listing its models).
function add(entry: AlertEntry, roles: RoleInfo): void {
  const open = useAlerts.getState().modal;
  const lists = [pending, open?.source === "run" ? open.entries : []];
  for (const list of lists) {
    const same = list.find((x) => x.title === entry.title);
    if (same) {
      same.models = [...new Set([...same.models, ...entry.models])];
      same.roles = [...new Set([...same.roles, ...entry.roles])];
      same.usedBy = [...new Set(same.models.flatMap((m) => usedBy(roles, m, same.roles)))];
      if (list !== pending) useAlerts.setState({ modal: { ...open!, entries: [...open!.entries] } });
      return;
    }
  }
  pending.push(entry);
  timer ??= setTimeout(flush, BATCH_MS);
}

function flush(): void {
  timer = null;
  if (!pending.length) return;
  const open = useAlerts.getState().modal;
  const entries = open?.source === "run" ? [...open.entries, ...pending] : pending;
  pending = [];
  useAlerts.setState({ modal: { source: "run", entries, canForce: false, problems: [] } });
}

// Updates "what happened next" on entries already shown or waiting to be shown.
function update(match: (e: AlertEntry) => boolean, outcome: string): boolean {
  let hit = false;
  for (const e of pending) if (match(e)) { e.outcome = outcome; hit = true; }
  const open = useAlerts.getState().modal;
  if (open?.source === "run" && open.entries.some(match)) {
    hit = true;
    useAlerts.setState({ modal: { ...open, entries: open.entries.map((e) => (match(e) ? { ...e, outcome } : e)) } });
  }
  return hit;
}

function notice(key: string, text: string): void {
  const now = Date.now();
  const list = useAlerts.getState().notices;
  const old = list.find((n) => n.key === key);
  const next = old ? list.map((n) => (n.key === key ? { ...n, text, count: n.count + 1, at: now } : n))
    : [...list, { key, text, count: 1, at: now }].slice(-MAX_NOTICES);
  useAlerts.setState({ notices: next });
  setTimeout(() => {
    const n = useAlerts.getState().notices.find((x) => x.key === key);
    if (n && n.at === now) dismissNotice(key);
  }, NOTICE_MS);
}

export function dismissNotice(key: string): void {
  useAlerts.setState({ notices: useAlerts.getState().notices.filter((n) => n.key !== key) });
}

// What the modal's "Retry run" does. A pipeline run by default; the Sources menu sets it while it reads a filing.
let retryHandler: (() => void) | null = null;
export function setRetry(fn: (() => void) | null): void {
  retryHandler = fn;
}
export const retryAction = () => retryHandler;

export function dismissModal(): void {
  useAlerts.setState({ modal: null });
}

function reset(id: string | null): void {
  runId = id;
  seen = new Set();
  pending = [];
  if (timer) clearTimeout(timer);
  timer = null;
  const open = useAlerts.getState().modal;
  useAlerts.setState({ modal: open?.source === "preflight" ? open : null, notices: [] });
}

// Called for each new event of a run. live: whether this run is live (replays never notify).
export function onRunEvent(e: RunEvent, live: boolean, roles: RoleInfo): void {
  if (e.type === "run.started") return reset(live ? e.run_id : null);
  if (!live || e.run_id !== runId) return;
  const role = String(e.role ?? "");
  if (e.type === "model.call_failed") {
    const cls = String(e.error_class), provider = String(e.provider), model = String(e.model), ref = `${provider}/${model}`;
    if (MODAL_CLASSES.has(cls)) {
      const key = `${ref}/${cls}`;
      if (seen.has(key)) return;
      seen.add(key);
      add({ key, title: titleFor(provider, model, cls, String(e.message ?? "")), models: [ref], roles: [role],
            usedBy: usedBy(roles, ref, [role]), detail: String(e.message ?? ""), fix: fixFor(cls),
            outcome: e.will_fallback ? "Trying the next backup…" : "No backup left for this job." }, roles);
    } else if (NOTICE_CLASSES.has(cls)) {
      const next = e.will_retry ? ", trying again" : e.will_fallback ? ", trying a backup" : "";
      notice(`${ref}/${cls}`, `${roleLabel(roles, role)}: ${providerName(provider)} ${model}: ${PLAIN[cls] ?? cls}${next}.`);
    }
  } else if (e.type === "model.fallback_used") {
    const from = String(e.from), to = String(e.to);
    const took = update((x) => x.models.includes(from) && x.roles.includes(role), `Switched to backup: ${modelOf(to)}.`);
    if (!took) notice(`fallback/${role}/${to}`, `${roleLabel(roles, role)}: switched from ${modelOf(from)} to backup ${modelOf(to)}.`);
  } else if (e.type === "role.exhausted") {
    update((x) => x.roles.includes(role), stopped(roles, role));
    const key = `exhausted/${role}`;
    if (seen.has(key)) return;
    seen.add(key);
    const attempts = (e.attempts as { provider: string; model: string; error_class: string }[] | undefined) ?? [];
    const tried = [...new Map(attempts.map((a) => [`${a.provider}/${a.model}`, a])).values()];
    add({ key, title: `${roleLabel(roles, role)}: no model worked`, models: [], roles: [role], usedBy: [], fix: "models",
          outcome: stopped(roles, role),
          detail: tried.length ? `Tried ${tried.map((a) => `${a.model} (${PLAIN[a.error_class] ?? a.error_class})`).join(", ")}.` : "" },
        roles);
  }
}

// A live run the server refused to start: same modal, one entry per kind of problem.
export function showPreflight(d: PreflightDetail): void {
  const groups = new Map<string, AlertEntry>();
  for (const p of d.problems) {
    const first = p.tried?.[0];
    const title = first ? titleFor(first.provider, first.model, first.status, first.message) : `${p.label} has no working model`;
    const g = groups.get(title) ?? { key: `preflight/${title}`, title, models: [], roles: [], usedBy: [], detail: "",
      fix: fixFor(first?.status ?? ""), outcome: NEXT_STEP[first?.status ?? ""] ?? "Pick a working model for these jobs in Model setup." };
    if (first) g.models = [...new Set([...g.models, `${first.provider}/${first.model}`])];
    g.roles.push(p.role);
    g.detail = g.roles.length === 1 ? p.reason : "";
    groups.set(title, g);
  }
  useAlerts.setState({ modal: { source: "preflight", entries: [...groups.values()], canForce: d.can_force, problems: d.problems } });
}
